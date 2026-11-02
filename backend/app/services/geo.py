"""
Reference locations and spatial case search (PostGIS geography, metres).

A case's position is its own coordinates (device GPS or map pin) when present, otherwise the coordinates of
the location it references. Results always say which one was used.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, joinedload

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import Conflict, NotFound, ValidationFailed
from app.config import get_settings
from app.models import Case, Location, Station, User
from app.models.enums import CaseStatus, CaseType, LocationKind
from app.schemas.geo import CaseMap, CaseMarker, CasePosition, LocationCreate, LocationUpdate, NearbyCase

ACTIVE_STATUSES = (
    CaseStatus.OPEN, CaseStatus.UNDER_REVIEW, CaseStatus.ASSIGNED, CaseStatus.MATCH_FOUND, CaseStatus.REOPENED,
)


# --- Reference locations -------------------------------------------------------------------------


def list_locations(
    db: Session, *, kind: LocationKind | None, sector_id: uuid.UUID | None, q: str | None, include_inactive: bool
) -> list[Location]:
    stmt = select(Location).order_by(Location.kind, Location.name, Location.id)
    if kind:
        stmt = stmt.where(Location.kind == kind)
    if sector_id:
        stmt = stmt.where(Location.sector_id == sector_id)
    if q:
        pattern = f"%{q.strip().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(Location.name.ilike(pattern), Location.name_local.ilike(pattern), Location.code.ilike(pattern)))
    if not include_inactive:
        stmt = stmt.where(Location.is_active.is_(True))
    return list(db.scalars(stmt.limit(1000)))


def get_location(db: Session, location_id: uuid.UUID) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise NotFound("Location not found", code="location_not_found")
    return location


def _check_references(db: Session, kind: LocationKind, sector_id: uuid.UUID | None, station_id: uuid.UUID | None,
                      self_id: uuid.UUID | None = None) -> None:
    if sector_id is not None:
        if kind is LocationKind.SECTOR:
            raise ValidationFailed("Sectors cannot have a parent sector", code="invalid_sector")
        sector = db.get(Location, sector_id)
        if sector is None or sector.kind != LocationKind.SECTOR or sector.id == self_id:
            raise ValidationFailed("sector_id must reference a SECTOR location", code="invalid_sector")
    if station_id is not None and db.get(Station, station_id) is None:
        raise ValidationFailed("Station is unknown", code="invalid_station")


def _commit_unique(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A location with this code already exists", code="location_code_taken") from exc


def create_location(db: Session, actor: User, payload: LocationCreate) -> Location:
    require_permission(actor, Permission.LOCATION_MANAGE)
    _check_references(db, payload.kind, payload.sector_id, payload.station_id)
    location = Location(id=uuid.uuid4(), **payload.model_dump())
    db.add(location)
    record_audit(
        db, action=AuditAction.LOCATION_CREATED, entity_type="location", entity_id=location.id,
        actor_user_id=actor.id, after=payload.model_dump(),
    )
    _commit_unique(db)
    db.refresh(location)
    return location


def update_location(db: Session, actor: User, location_id: uuid.UUID, payload: LocationUpdate) -> Location:
    require_permission(actor, Permission.LOCATION_MANAGE)
    location = db.get(Location, location_id, with_for_update=True)
    if location is None:
        raise NotFound("Location not found", code="location_not_found")
    changes = payload.model_dump(exclude_unset=True)
    _check_references(
        db, LocationKind(location.kind), changes.get("sector_id", location.sector_id),
        changes.get("station_id", location.station_id), self_id=location.id,
    )
    before = {k: getattr(location, k) for k in changes}
    for key, value in changes.items():
        setattr(location, key, value)
    record_audit(
        db, action=AuditAction.LOCATION_UPDATED, entity_type="location", entity_id=location.id,
        actor_user_id=actor.id, before=before, after=changes,
    )
    _commit_unique(db)
    db.refresh(location)
    return location


# --- Spatial case search -------------------------------------------------------------------------


def _point(latitude: float, longitude: float) -> ColumnElement:
    return func.geography(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326))


@dataclass
class _Scope:
    clause: ColumnElement[bool]
    scoped: ColumnElement[bool]


def _scope(user: User, all_stations: bool) -> _Scope:
    require_permission(user, Permission.CASE_READ)
    scoped = case_visibility_clause(user)
    if all_stations:
        require_permission(user, Permission.CASE_SEARCH_ALL_STATIONS)
        return _Scope(true(), scoped)
    return _Scope(scoped, scoped)


def _filters(case_type: CaseType | None, statuses: list[CaseStatus] | None) -> list[ColumnElement[bool]]:
    conditions = [Case.status.in_(statuses or ACTIVE_STATUSES)]
    if case_type:
        conditions.append(Case.case_type == case_type)
    return conditions


def _positioned(stmt: Select, loc) -> Select:
    return stmt.outerjoin(loc, loc.id == Case.location_id)


def nearby_cases(
    db: Session, user: User, *, latitude: float, longitude: float, radius_m: int,
    case_type: CaseType | None, statuses: list[CaseStatus] | None, all_stations: bool, limit: int,
) -> list[NearbyCase]:
    from app.services.cases import to_summary

    settings = get_settings()
    if radius_m > settings.geo_max_radius_m:
        raise ValidationFailed(f"radius_m may not exceed {settings.geo_max_radius_m}", code="radius_too_large")
    scope = _scope(user, all_stations)
    point = _point(latitude, longitude)
    loc = aliased(Location)

    # Locations in range use their GiST index; cases with their own point use theirs.
    nearby_locations = select(Location.id).where(func.ST_DWithin(Location.geog, point, radius_m)).scalar_subquery()
    in_range = or_(
        func.ST_DWithin(Case.geo, point, radius_m),
        and_(Case.geo.is_(None), Case.location_id.in_(nearby_locations)),
    )
    position = func.coalesce(Case.geo, loc.geog)
    distance = func.ST_Distance(position, point).label("distance_m")
    stmt = (
        _positioned(select(Case, distance, loc.latitude, loc.longitude), loc)
        .where(scope.clause, in_range, *_filters(case_type, statuses))
        .options(
            joinedload(Case.missing_person), joinedload(Case.found_person),
            joinedload(Case.lost_item), joinedload(Case.found_item),
        )
        .order_by(distance, Case.id)
        .limit(limit)
    )
    rows = db.execute(stmt).unique().all()
    ids = [row[0].id for row in rows]
    in_scope = set(db.scalars(select(Case.id).where(Case.id.in_(ids), scope.scoped))) if ids else set()
    results = []
    for found, dist, loc_lat, loc_lng in rows:
        summary = to_summary(found, in_scope=found.id in in_scope)
        results.append(
            NearbyCase(**summary.model_dump(), distance_m=round(float(dist), 1), position=_position(found, loc_lat, loc_lng))
        )
    return results


def _position(found: Case, loc_lat: float | None, loc_lng: float | None) -> CasePosition:
    if found.latitude is not None:
        return CasePosition(latitude=found.latitude, longitude=found.longitude, position_source="case")
    return CasePosition(latitude=loc_lat, longitude=loc_lng, position_source="location")


def map_cases(
    db: Session, user: User, *, min_lat: float, min_lng: float, max_lat: float, max_lng: float,
    case_type: CaseType | None, statuses: list[CaseStatus] | None, all_stations: bool,
) -> CaseMap:
    settings = get_settings()
    if min_lat >= max_lat or min_lng >= max_lng:
        raise ValidationFailed("Bounding box minimums must be below maximums", code="invalid_bbox")
    if max_lat - min_lat > settings.geo_max_bbox_degrees or max_lng - min_lng > settings.geo_max_bbox_degrees:
        raise ValidationFailed(
            f"Bounding box may span at most {settings.geo_max_bbox_degrees} degrees; zoom in", code="bbox_too_large"
        )
    scope = _scope(user, all_stations)
    envelope = func.geography(func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326))
    loc = aliased(Location)
    boxed_locations = select(Location.id).where(Location.geog.op("&&")(envelope)).scalar_subquery()
    in_box = or_(
        and_(Case.geo.op("&&")(envelope), func.ST_Intersects(Case.geo, envelope)),
        and_(Case.geo.is_(None), Case.location_id.in_(boxed_locations)),
    )
    limit = settings.geo_map_limit
    stmt = (
        _positioned(
            select(
                Case.id, Case.case_number, Case.case_type, Case.status, Case.urgency_level, Case.station_id,
                Case.latitude, Case.longitude, loc.latitude.label("loc_lat"), loc.longitude.label("loc_lng"),
                case((scope.scoped, True), else_=False).label("in_scope"),
            ),
            loc,
        )
        .where(scope.clause, in_box, *_filters(case_type, statuses))
        .order_by(Case.created_at.desc(), Case.id)
        .limit(limit + 1)
    )
    rows = db.execute(stmt).all()
    markers = []
    for row in rows[:limit]:
        own = row.latitude is not None
        markers.append(
            CaseMarker(
                id=row.id, case_number=row.case_number, case_type=row.case_type, status=row.status,
                urgency_level=row.urgency_level, station_id=row.station_id, in_scope=bool(row.in_scope),
                position=CasePosition(
                    latitude=row.latitude if own else row.loc_lat,
                    longitude=row.longitude if own else row.loc_lng,
                    position_source="case" if own else "location",
                ),
            )
        )
    return CaseMap(items=markers, truncated=len(rows) > limit)


# --- Linking historical free-text locations ------------------------------------------------------


def _normalise_place(value: str | None) -> str:
    return " ".join((value or "").replace(",", " ").replace(".", " ").casefold().split())


@dataclass
class LinkProposal:
    case_id: uuid.UUID
    case_number: str
    text: str
    location_id: uuid.UUID | None
    outcome: str  # "link", "ambiguous", "no_match"


def plan_location_links(db: Session, *, limit: int = 10_000) -> list[LinkProposal]:
    """
    Proposes location_id for cases whose free-text place exactly equals a location's name, local name, or
    code after normalising case and whitespace. Never sets coordinates; ambiguous text is left alone.
    """
    from app.services.case_types import spec_for

    index: dict[str, set[uuid.UUID]] = {}
    for location in db.scalars(select(Location).where(Location.is_active.is_(True))):
        for label in (location.name, location.name_local, location.code):
            key = _normalise_place(label)
            if key:
                index.setdefault(key, set()).add(location.id)

    proposals = []
    cases = db.scalars(
        select(Case)
        .where(Case.location_id.is_(None))
        .options(joinedload(Case.missing_person), joinedload(Case.found_person),
                 joinedload(Case.lost_item), joinedload(Case.found_item))
        .order_by(Case.created_at, Case.id)
        .limit(limit)
    ).unique()
    for found in cases:
        spec = spec_for(found.case_type)
        detail = getattr(found, spec.relationship)
        text = getattr(detail, spec.location_field, None) if detail is not None else None
        if not text:
            continue
        candidates = index.get(_normalise_place(text), set())
        if len(candidates) == 1:
            proposals.append(LinkProposal(found.id, found.case_number, text, next(iter(candidates)), "link"))
        else:
            proposals.append(LinkProposal(found.id, found.case_number, text, None, "ambiguous" if candidates else "no_match"))
    return proposals


def apply_location_links(db: Session, proposals: list[LinkProposal]) -> int:
    applied = 0
    for proposal in proposals:
        if proposal.outcome != "link":
            continue
        target = db.get(Case, proposal.case_id, with_for_update=True)
        if target is None or target.location_id is not None:
            continue
        target.location_id = proposal.location_id
        target.updated_at = func.now()
        record_audit(
            db, action=AuditAction.CASE_LOCATION_LINKED, entity_type="case", entity_id=target.id,
            actor_user_id=None, case=target,
            after={"location_id": proposal.location_id, "method": "exact_name_match", "source": "cli"},
        )
        applied += 1
    db.commit()
    return applied
