import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, has_permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.crypto import decrypt_text, encrypt_text
from app.common.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseContact, CaseStatusHistory, Location, Station, User
from app.models.enums import CaseSource, CaseStatus, PublicReviewStatus, Role
from app.observability.metrics import CASES_CREATED, count_after_commit
from app.repositories import cases as case_repo
from app.repositories.cases import CaseFilters
from app.schemas.cases import CaseSummary
from app.services.access import load_case
from app.services.case_types import CaseTypeSpec, spec_for
from app.services.idempotency import run_idempotent, run_idempotent_operation

CASE_NUMBER_TZ = ZoneInfo("Asia/Kolkata")
POSITION_FIELDS = ("location_id", "latitude", "longitude", "coordinate_source", "coordinate_accuracy_m")
SYNC_FIELDS = ("client_id", "device_id", "client_record_id", "client_created_at")
ENVELOPE_FIELDS = frozenset({"urgency_level", "station_id", "contact_details", *POSITION_FIELDS, *SYNC_FIELDS})
_UNSET = object()


def find_case_by_client_record(
    db: Session, user: User, *, device_id: str, client_record_id: str
) -> Case | None:
    return db.scalar(
        select(Case).where(
            Case.reported_by_user_id == user.id,
            Case.device_id == device_id,
            Case.client_record_id == client_record_id,
        )
    )


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
    if Role(user.role) is Role.PUBLIC:
        settings = get_settings()
        if settings.public_intake_station_id is None:
            raise ValidationFailed(
                "Public case registration is not configured",
                code="public_intake_unconfigured",
            )
        if requested is not None and requested != settings.public_intake_station_id:
            raise Forbidden("PUBLIC accounts cannot choose a station", code="station_scope")
        station_id = settings.public_intake_station_id
    elif Role(user.role) is Role.ADMIN:
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


def _set_public_contact(db: Session, case: Case, contact: dict[str, Any]) -> None:
    """Stores structured public reporter contact. Caller supplies already-normalised phone/email values."""
    from app.common.security_hash import keyed_hash, mask_email
    from app.notifications import mask

    existing = db.get(CaseContact, case.id)
    phone = contact["mobile"]
    email = contact.get("email")
    # Public rows encrypt the verified mobile itself so automated notifications can reuse it safely.
    values = dict(
        contact_ciphertext=encrypt_text(phone),
        updated_by_user_id=None,
        reporter_name=contact.get("reporter_name"),
        mobile_lookup_hash=keyed_hash(phone),
        mobile_hint=mask(phone),
        mobile_verified_at=contact.get("mobile_verified_at") or utcnow(),
        email_ciphertext=encrypt_text(email) if email else None,
        email_hint=mask_email(email) if email else None,
        email_verified_at=contact.get("email_verified_at"),
        preferred_locale=contact.get("preferred_locale"),
        notification_consent_statement=contact.get("notification_consent_statement"),
        notification_consent_at=contact.get("notification_consent_at"),
    )
    if existing is None:
        db.add(CaseContact(case_id=case.id, **values))
    else:
        for key, value in values.items():
            setattr(existing, key, value)


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
    source: CaseSource = CaseSource.STAFF,
    public_review_status: PublicReviewStatus | None = None,
    public_contact: dict[str, Any] | None = None,
    skip_permission_check: bool = False,
) -> tuple[Case, bool]:
    if not skip_permission_check:
        require_permission(user, Permission.CASE_CREATE, allow_public=True)
    data = payload.model_dump()
    is_public_user = Role(user.role) is Role.PUBLIC
    if is_public_user:
        # Citizens cannot choose source/review state; force the public intake path.
        source = CaseSource.PUBLIC
        public_review_status = PublicReviewStatus.PENDING_VERIFICATION
        if public_contact is None and user.phone_number:
            public_contact = {
                "mobile": user.phone_number,
                "reporter_name": user.full_name,
                "mobile_verified_at": utcnow(),
            }
    station_id = _resolve_station(db, user, data.get("station_id"))
    client_record_id = data.get("client_record_id")
    device_id = data.get("device_id")
    if client_record_id is not None:
        existing = find_case_by_client_record(db, user, device_id=device_id, client_record_id=client_record_id)
        if existing is not None:
            if existing.case_type != spec.case_type:
                raise Conflict(
                    "client_record_id was already used for a different case type",
                    code="client_record_reused",
                    details={"case_id": existing.id, "case_type": existing.case_type},
                )
            return load_case(db, user, existing.id, spec=spec), True

    def _create() -> uuid.UUID:
        case_number = allocate_case_number(db, spec)
        detail_values = {k: v for k, v in data.items() if k not in ENVELOPE_FIELDS}
        now = utcnow()
        case = Case(
            id=uuid.uuid4(),
            case_number=case_number,
            case_type=spec.case_type,
            station_id=station_id,
            reported_by_user_id=user.id,
            status=CaseStatus.OPEN,
            source=source,
            public_review_status=public_review_status,
            urgency_level=data.get("urgency_level"),
            client_id=data.get("client_id"),
            device_id=device_id,
            client_record_id=client_record_id,
            client_created_at=data.get("client_created_at"),
            server_synced_at=now if client_record_id else None,
            sync_status="SYNCED" if client_record_id else None,
        )
        _apply_position(db, case, {k: data[k] for k in POSITION_FIELDS if data.get(k) is not None})
        detail = spec.detail_model(case_id=case.id, **detail_values)
        case.search_text = _build_search_text(case_number, spec, detail)
        db.add(case)
        db.flush()
        db.add(detail)
        if public_contact is not None:
            _set_public_contact(db, case, public_contact)
        elif not is_public_user and data.get("contact_details"):
            _set_contact(db, case, user, data["contact_details"])
        db.add(
            CaseStatusHistory(
                case_id=case.id, from_status=None, to_status=CaseStatus.OPEN, changed_by_user_id=user.id,
                reason="Case registered" if source is CaseSource.STAFF else "Public case submitted",
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
                "source": source,
                "public_review_status": public_review_status,
                "station_id": station_id,
                "urgency_level": data.get("urgency_level"),
                "fields_provided": sorted(k for k, v in detail_values.items() if v is not None),
                "contact_details_provided": bool(data.get("contact_details") or public_contact),
                "location_id": data.get("location_id"),
                "coordinates_provided": data.get("latitude") is not None,
                "coordinate_source": data.get("coordinate_source"),
                "client_id": data.get("client_id"),
                "device_id": device_id,
                "client_record_id": client_record_id,
            },
        )
        return case.id

    try:
        case_id, replayed = run_idempotent(
            db, user=user, key=idempotency_key, endpoint=f"create:{spec.slug}", payload=data, create=_create
        )
    except IntegrityError:
        db.rollback()
        if client_record_id is None:
            raise
        existing = find_case_by_client_record(db, user, device_id=device_id, client_record_id=client_record_id)
        if existing is None:
            raise
        return load_case(db, user, existing.id, spec=spec), True
    if not replayed:
        _suggest_matches(case_id)
    db.expire_all()
    return load_case(db, user, case_id, spec=spec), replayed


def get_case(db: Session, user: User, case_id: uuid.UUID, spec: CaseTypeSpec | None = None) -> Case:
    require_permission(user, Permission.CASE_READ, allow_public=True)
    return load_case(db, user, case_id, spec=spec)


def update_case(
    db: Session,
    user: User,
    spec: CaseTypeSpec,
    case_id: uuid.UUID,
    payload: BaseModel,
    *,
    idempotency_key: str | None = None,
) -> tuple[Case, bool]:
    require_permission(user, Permission.CASE_UPDATE)
    data = payload.model_dump(exclude_unset=True)
    changed_fields_box: list[str] = []
    position_changed_box: list[bool] = []

    def _update() -> None:
        case = load_case(db, user, case_id, spec=spec, for_update=True)
        local = dict(data)
        expected = local.pop("expected_version", None)
        if expected is not None and expected != case.version:
            raise Conflict(
                "The case changed since you loaded it; reload and try again", code="case_changed",
                details={"case_id": case.id, "expected_version": expected, "current_version": case.version},
            )
        if case.status in (CaseStatus.CLOSED, CaseStatus.RESOLVED):
            raise Conflict(
                f"{case.status.title()} cases cannot be edited; reopen the case first", code="case_closed"
            )

        contact = local.pop("contact_details", _UNSET)
        urgency = local.pop("urgency_level", _UNSET)
        for sync_field in SYNC_FIELDS:
            local.pop(sync_field, None)
        position_changes = _apply_position(db, case, local)
        detail = _detail(case, spec)

        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        changed_fields = []
        for name, value in local.items():
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
            for key, (old, new) in position_changes.items():
                if key in ("latitude", "longitude"):
                    after["coordinates_changed"] = True
                else:
                    before[key], after[key] = old, new
        if not (changed_fields or before or after or contact is not _UNSET):
            db.commit()
            return
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
        changed_fields_box[:] = changed_fields
        position_changed_box[:] = [bool(position_changes)]

    replayed = run_idempotent_operation(
        db,
        user=user,
        key=idempotency_key,
        endpoint=f"update:{spec.slug}:{case_id}",
        payload=data,
        resource_id=case_id,
        operation=_update,
    )
    if not replayed and (changed_fields_box or position_changed_box):
        _suggest_matches(case_id)
    db.expire_all()
    return load_case(db, user, case_id, spec=spec), replayed


def _case_in_scope(actor: User | None, case: Case) -> bool:
    if actor is None:
        return True
    role = Role(actor.role)
    if role is Role.ADMIN:
        return True
    if role is Role.PUBLIC:
        return case.reported_by_user_id == actor.id
    return actor.station_id is not None and case.station_id == actor.station_id


def _verification_evidence_for(db: Session, actor: User | None, case: Case) -> str | None:
    """Latest RESOLVED-status verification comment. Redacted unless the caller has note:sensitive."""
    if actor is not None and not has_permission(actor, Permission.NOTE_SENSITIVE):
        return None
    evidence = db.scalar(
        select(CaseStatusHistory.verification_evidence)
        .where(
            CaseStatusHistory.case_id == case.id,
            CaseStatusHistory.to_status == CaseStatus.RESOLVED,
            CaseStatusHistory.verification_evidence.is_not(None),
        )
        .order_by(CaseStatusHistory.created_at.desc(), CaseStatusHistory.id.desc())
        .limit(1)
    )
    return evidence


def to_read(
    db: Session,
    case: Case,
    spec: CaseTypeSpec | None = None,
    contact_ids: set | None = None,
    *,
    actor: User | None = None,
):
    spec = spec or spec_for(case.case_type)
    if contact_ids is None:
        contact_ids = case_repo.has_contact(db, [case.id])
    values = {
        field: getattr(case, field)
        for field in spec.read_schema.model_fields
        if hasattr(case, field) and field not in ("details", "in_scope", "verification_evidence")
    }
    return spec.read_schema(
        **values,
        has_contact_details=case.id in contact_ids,
        in_scope=_case_in_scope(actor, case),
        verification_evidence=_verification_evidence_for(db, actor, case),
        details=spec.details_read_schema.model_validate(_detail(case, spec)),
    )


def list_cases(
    db: Session, user: User, spec: CaseTypeSpec, filters: CaseFilters, *, limit: int, offset: int
) -> tuple[list, int]:
    require_permission(user, Permission.CASE_READ, allow_public=True)
    filters.case_type = spec.case_type
    rows, total = case_repo.search_cases(db, filters, case_visibility_clause(user), limit=limit, offset=offset)
    contact_ids = case_repo.has_contact(db, [c.id for c in rows])
    return [to_read(db, c, spec, contact_ids, actor=user) for c in rows], total


def to_summary(case: Case, *, in_scope: bool) -> CaseSummary:
    spec = spec_for(case.case_type)
    detail = _detail(case, spec)
    age = getattr(detail, "approximate_age", None) if detail is not None else None
    age_group = getattr(detail, "age_group", None) if detail is not None else None
    if detail is not None and hasattr(detail, "gender"):
        gender = detail.gender
    elif detail is not None and hasattr(detail, "estimated_gender"):
        gender = detail.estimated_gender
    else:
        gender = None
    return CaseSummary(
        id=case.id,
        case_number=case.case_number,
        case_type=case.case_type,
        status=case.status,
        urgency_level=case.urgency_level,
        station_id=case.station_id,
        reported_by_user_id=case.reported_by_user_id,
        assigned_to_user_id=case.assigned_to_user_id,
        title=getattr(detail, spec.title_field) if spec.title_field and detail is not None else None,
        location=getattr(detail, spec.location_field) if detail is not None else None,
        location_id=case.location_id,
        approximate_age=age,
        age_group=age_group,
        gender=gender,
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
    require_permission(user, Permission.CASE_READ, allow_public=True)
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
