import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, has_permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.crypto import decrypt_text, encrypt_text
from app.common.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.config import get_settings
from app.models import Case, CaseContact, CaseStatusHistory, Location, Station, User
from app.models.enums import CaseStatus, Role
from app.repositories import cases as case_repo
from app.repositories.cases import CaseFilters
from app.schemas.cases import CaseSummary
from app.observability.metrics import CASES_CREATED, count_after_commit
from app.services.access import load_case
from app.services.case_types import CaseTypeSpec, spec_for
from app.services.idempotency import run_idempotent

CASE_NUMBER_TZ = ZoneInfo("Asia/Kolkata")
POSITION_FIELDS = ("location_id", "latitude", "longitude", "coordinate_source", "coordinate_accuracy_m")
ENVELOPE_FIELDS = frozenset({"urgency_level", "station_id", "contact_details", *POSITION_FIELDS})
_UNSET = object()


def _detail(case: Case, spec: CaseTypeSpec | None = None):
    spec = spec or spec_for(case.case_type)
    return getattr(case, spec.relationship)


def _build_search_text(case_number: str, spec: CaseTypeSpec, detail: Any) -> str:
    parts = [case_number]
    for field_name in spec.search_fields:
        value = getattr(detail, field_name, None)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _resolve_station(db: Session, user: User, requested: uuid.UUID | None) -> uuid.UUID:
    if Role(user.role) is Role.ADMIN:
        if requested is None:
            raise ValidationFailed("station_id is required when an administrator registers a case", code="station_required")
        station_id = requested
    else:
        if requested is not None and requested != user.station_id:
            raise Forbidden("Cases can only be registered at your own station", code="station_scope")
        station_id = user.station_id
    station = db.get(Station, station_id)
    if station is None or not station.is_active:
        raise ValidationFailed("Station is unknown or inactive", code="invalid_station")
    return station_id


def _suggest_matches(case_id: uuid.UUID) -> None:
    from app.services.matching import run_after_change

    run_after_change(case_id)


def _validate_position(db: Session, values: dict[str, Any]) -> None:
    location_id = values.get("location_id")
    if location_id is not None:
        location = db.get(Location, location_id)
        if location is None or not location.is_active:
            raise ValidationFailed("Location is unknown or inactive", code="invalid_location")
    lat, lng = values.get("latitude"), values.get("longitude")
    area = get_settings().geo_operating_area
    if lat is not None and area and not (area[0] <= lat <= area[2] and area[1] <= lng <= area[3]):
        raise ValidationFailed(
            "Coordinates are outside the mela operating area; check that latitude and longitude are not swapped",
            code="coordinates_out_of_area", details={"operating_area": area},
        )


def _apply_position(db: Session, case: Case, data: dict[str, Any]) -> dict[str, Any]:
    """Applies sent position fields; returns {field: (old, new)} for changed ones."""
    sent = {k: data.pop(k) for k in POSITION_FIELDS if k in data}
    if not sent:
        return {}
    if "latitude" in sent and sent["latitude"] is None:
        sent.setdefault("coordinate_source", None)
        sent.setdefault("coordinate_accuracy_m", None)
    merged = {k: sent.get(k, getattr(case, k)) for k in POSITION_FIELDS}
    _validate_position(db, {k: v for k, v in merged.items() if k in sent or k in ("latitude", "longitude")})
    changes = {}
    for key, value in sent.items():
        if getattr(case, key) != value:
            changes[key] = (getattr(case, key), value)
            setattr(case, key, value)
    if (case.latitude is None) != (case.coordinate_source is None):
        raise ValidationFailed("coordinate_source is required with coordinates", code="coordinate_source_required")
    return changes


def _set_contact(db: Session, case: Case, user: User, contact: str | None) -> None:
    existing = db.get(CaseContact, case.id)
    if contact is None:
        if existing is not None:
            db.delete(existing)
        return
    if existing is None:
        db.add(CaseContact(case_id=case.id, contact_ciphertext=encrypt_text(contact), updated_by_user_id=user.id))
    else:
        existing.contact_ciphertext = encrypt_text(contact)
        existing.updated_by_user_id = user.id


def allocate_case_number(db: Session, spec: CaseTypeSpec, now: datetime | None = None) -> str:
    year = (now or datetime.now(CASE_NUMBER_TZ)).astimezone(CASE_NUMBER_TZ).year
    value = case_repo.allocate_case_number_value(db, spec.case_type, year)
    return f"{get_settings().case_number_prefix}-{spec.code}-{year}-{value:06d}"


def create_case(
    db: Session,
    user: User,
    spec: CaseTypeSpec,
    payload: BaseModel,
    *,
    idempotency_key: str | None = None,
) -> tuple[Case, bool]:
    require_permission(user, Permission.CASE_CREATE)
    data = payload.model_dump()
    station_id = _resolve_station(db, user, data.get("station_id"))

    def _create() -> uuid.UUID:
        case_number = allocate_case_number(db, spec)
        detail_values = {k: v for k, v in data.items() if k not in ENVELOPE_FIELDS}
        case = Case(
            id=uuid.uuid4(),
            case_number=case_number,
            case_type=spec.case_type,
            station_id=station_id,
            reported_by_user_id=user.id,
            status=CaseStatus.OPEN,
            urgency_level=data.get("urgency_level"),
        )
        _apply_position(db, case, {k: data[k] for k in POSITION_FIELDS if data.get(k) is not None})
        detail = spec.detail_model(case_id=case.id, **detail_values)
        case.search_text = _build_search_text(case_number, spec, detail)
        db.add(case)
        db.flush()
        db.add(detail)
        if data.get("contact_details"):
            _set_contact(db, case, user, data["contact_details"])
        db.add(
            CaseStatusHistory(
                case_id=case.id, from_status=None, to_status=CaseStatus.OPEN, changed_by_user_id=user.id,
                reason="Case registered",
            )
        )
        count_after_commit(db, CASES_CREATED, spec.case_type.value)
        record_audit(
            db,
            action=AuditAction.CASE_CREATED,
            entity_type="case",
            entity_id=case.id,
            actor_user_id=user.id,
            case=case,
            after={
                "case_type": spec.case_type,
                "status": CaseStatus.OPEN,
                "station_id": station_id,
                "urgency_level": data.get("urgency_level"),
                "fields_provided": sorted(k for k, v in detail_values.items() if v is not None),
                "contact_details_provided": bool(data.get("contact_details")),
                "location_id": data.get("location_id"),
                "coordinates_provided": data.get("latitude") is not None,
                "coordinate_source": data.get("coordinate_source"),
            },
        )
        return case.id

    case_id, replayed = run_idempotent(
        db, user=user, key=idempotency_key, endpoint=f"create:{spec.slug}", payload=data, create=_create
    )
    if not replayed:
        _suggest_matches(case_id)
    db.expire_all()
    return load_case(db, user, case_id, spec=spec), replayed


def get_case(db: Session, user: User, case_id: uuid.UUID, spec: CaseTypeSpec | None = None) -> Case:
    require_permission(user, Permission.CASE_READ)
    return load_case(db, user, case_id, spec=spec)


def update_case(db: Session, user: User, spec: CaseTypeSpec, case_id: uuid.UUID, payload: BaseModel) -> Case:
    require_permission(user, Permission.CASE_UPDATE)
    case = load_case(db, user, case_id, spec=spec, for_update=True)
    data = payload.model_dump(exclude_unset=True)
    expected = data.pop("expected_version", None)
    if expected is not None and expected != case.version:
        raise Conflict(
            "The case changed since you loaded it; reload and try again", code="case_changed",
            details={"case_id": case.id, "expected_version": expected, "current_version": case.version},
        )
    if case.status in (CaseStatus.CLOSED, CaseStatus.RESOLVED):
        raise Conflict(
            f"{case.status.title()} cases cannot be edited; reopen the case first", code="case_closed"
        )

    contact = data.pop("contact_details", _UNSET)
    urgency = data.pop("urgency_level", _UNSET)
    position_changes = _apply_position(db, case, data)
    detail = _detail(case, spec)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    changed_fields = []
    for name, value in data.items():
        if getattr(detail, name) != value:
            setattr(detail, name, value)
            changed_fields.append(name)
    if urgency is not _UNSET and urgency != case.urgency_level:
        before["urgency_level"], after["urgency_level"] = case.urgency_level, urgency
        case.urgency_level = urgency
    if contact is not _UNSET:
        _set_contact(db, case, user, contact)
        after["contact_details_changed"] = True

    if position_changes:
        # Exact coordinates stay out of the audit trail's before/after; record that they changed and how.
        for key, (old, new) in position_changes.items():
            if key in ("latitude", "longitude"):
                after["coordinates_changed"] = True
            else:
                before[key], after[key] = old, new
    if not (changed_fields or before or after or contact is not _UNSET):
        return case
    if changed_fields:
        for name in ("item_category", "item_name"):
            if name in changed_fields and getattr(detail, name) is None:
                raise ValidationFailed(f"{name} cannot be cleared", code="required_field")
        case.search_text = _build_search_text(case.case_number, spec, detail)
        after["changed_fields"] = sorted(changed_fields)
    case.updated_at = func.now()
    record_audit(
        db, action=AuditAction.CASE_UPDATED, entity_type="case", entity_id=case.id, actor_user_id=user.id,
        case=case, before=before or None, after=after,
    )
    if contact is not _UNSET:
        record_audit(
            db, action=AuditAction.CONTACT_UPDATED, entity_type="case_contact", entity_id=case.id,
            actor_user_id=user.id, case=case, after={"cleared": contact is None},
        )
    db.commit()
    if changed_fields or position_changes:
        _suggest_matches(case_id)
    db.expire_all()
    return load_case(db, user, case_id, spec=spec)


def to_read(db: Session, case: Case, spec: CaseTypeSpec | None = None, contact_ids: set | None = None):
    spec = spec or spec_for(case.case_type)
    if contact_ids is None:
        contact_ids = case_repo.has_contact(db, [case.id])
    return spec.read_schema(
        **{field: getattr(case, field) for field in spec.read_schema.model_fields if hasattr(case, field) and field != "details"},
        has_contact_details=case.id in contact_ids,
        details=spec.details_read_schema.model_validate(_detail(case, spec)),
    )


def list_cases(
    db: Session, user: User, spec: CaseTypeSpec, filters: CaseFilters, *, limit: int, offset: int
) -> tuple[list, int]:
    require_permission(user, Permission.CASE_READ)
    filters.case_type = spec.case_type
    rows, total = case_repo.search_cases(db, filters, case_visibility_clause(user), limit=limit, offset=offset)
    contact_ids = case_repo.has_contact(db, [c.id for c in rows])
    return [to_read(db, c, spec, contact_ids) for c in rows], total


def to_summary(case: Case, *, in_scope: bool) -> CaseSummary:
    spec = spec_for(case.case_type)
    detail = _detail(case, spec)
    return CaseSummary(
        id=case.id,
        case_number=case.case_number,
        case_type=case.case_type,
        status=case.status,
        urgency_level=case.urgency_level,
        station_id=case.station_id,
        assigned_to_user_id=case.assigned_to_user_id,
        title=getattr(detail, spec.title_field) if spec.title_field and detail is not None else None,
        location=getattr(detail, spec.location_field) if detail is not None else None,
        event_at=getattr(detail, spec.event_time_field) if detail is not None else None,
        version=case.version,
        created_at=case.created_at,
        updated_at=case.updated_at,
        in_scope=in_scope,
    )


def search_all(
    db: Session, user: User, filters: CaseFilters, *, all_stations: bool, limit: int, offset: int
) -> tuple[list[CaseSummary], int]:
    """Unified search. `all_stations` (police and admins) widens results to summaries from other stations."""
    require_permission(user, Permission.CASE_READ)
    scoped = case_visibility_clause(user)
    if all_stations:
        require_permission(user, Permission.CASE_SEARCH_ALL_STATIONS)
        rows, total = case_repo.search_cases(db, filters, true(), limit=limit, offset=offset)
        ids = [c.id for c in rows]
        in_scope_ids = set(db.scalars(select(Case.id).where(Case.id.in_(ids), scoped))) if ids else set()
        return [to_summary(c, in_scope=c.id in in_scope_ids) for c in rows], total
    rows, total = case_repo.search_cases(db, filters, scoped, limit=limit, offset=offset)
    return [to_summary(c, in_scope=True) for c in rows], total


def reveal_contact(db: Session, user: User, case_id: uuid.UUID, reason_code: str) -> str:
    require_permission(user, Permission.CONTACT_READ)
    case = load_case(db, user, case_id)
    contact = db.get(CaseContact, case.id)
    if contact is None:
        raise NotFound("No contact details are recorded for this case", code="contact_not_found")
    plaintext = decrypt_text(contact.contact_ciphertext)
    record_audit(
        db, action=AuditAction.CONTACT_VIEWED, entity_type="case_contact", entity_id=case.id,
        actor_user_id=user.id, case=case, reason=reason_code,
    )
    db.commit()
    return plaintext


def can_see_sensitive(user: User) -> bool:
    return has_permission(user, Permission.NOTE_SENSITIVE)
