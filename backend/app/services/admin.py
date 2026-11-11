import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.passwords import hash_password, validate_new_password
from app.auth.permissions import Permission, require_permission
from app.auth.service import revoke_all_sessions
from app.common.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import Station, User
from app.models.enums import Role
from app.schemas.admin import StationCreate, StationUpdate, UserCreate, UserUpdate

_UNIQUE_MESSAGES = {
    "uq_stations_code": "A station with this code already exists",
    "uq_users_username": "Username is already taken",
    "uq_users_email": "Email is already registered",
    "uq_users_police_badge_number": "Badge number is already registered",
}


def _commit_unique(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in _UNIQUE_MESSAGES:
            raise Conflict(_UNIQUE_MESSAGES[constraint], code="duplicate") from exc
        raise


def _validate_role_fields(db: Session, role: Role, station_id: uuid.UUID | None, badge: str | None) -> None:
    if role is Role.PUBLIC:
        raise ValidationFailed(
            "PUBLIC accounts must self-register; administrators cannot create them",
            code="public_role_forbidden",
        )
    if role is not Role.ADMIN:
        if station_id is None:
            raise ValidationFailed("station_id is required for officers and help-desk operators", code="station_required")
        station = db.get(Station, station_id)
        if station is None or not station.is_active:
            raise ValidationFailed("Station is unknown or inactive", code="invalid_station")
    if role is Role.POLICE_OFFICER and not badge:
        raise ValidationFailed("police_badge_number is required for police officers", code="badge_required")


# --- Stations ------------------------------------------------------------------------------------


def create_station(db: Session, actor: User, payload: StationCreate) -> Station:
    require_permission(actor, Permission.STATION_MANAGE)
    station = Station(id=uuid.uuid4(), **payload.model_dump())
    db.add(station)
    record_audit(
        db, action=AuditAction.STATION_CREATED, entity_type="station", entity_id=station.id,
        actor_user_id=actor.id, after=payload.model_dump(),
    )
    _commit_unique(db)
    return station


def list_stations(db: Session, actor: User, *, include_inactive: bool) -> list[Station]:
    stmt = select(Station).order_by(Station.name, Station.id)
    if not include_inactive:
        stmt = stmt.where(Station.is_active.is_(True))
    return list(db.scalars(stmt))


def update_station(db: Session, actor: User, station_id: uuid.UUID, payload: StationUpdate) -> Station:
    require_permission(actor, Permission.STATION_MANAGE)
    station = db.get(Station, station_id, with_for_update=True)
    if station is None:
        raise NotFound("Station not found", code="station_not_found")
    changes = payload.model_dump(exclude_unset=True)
    before = {k: getattr(station, k) for k in changes}
    for key, value in changes.items():
        setattr(station, key, value)
    record_audit(
        db, action=AuditAction.STATION_UPDATED, entity_type="station", entity_id=station.id,
        actor_user_id=actor.id, before=before, after=changes,
    )
    _commit_unique(db)
    return station


# --- Users ---------------------------------------------------------------------------------------


def create_user(db: Session, actor: User, payload: UserCreate) -> User:
    require_permission(actor, Permission.USER_MANAGE)
    _validate_role_fields(db, payload.role, payload.station_id, payload.police_badge_number)
    validate_new_password(payload.password, username=payload.username)
    data = payload.model_dump(exclude={"password"})
    if payload.role is Role.ADMIN and payload.station_id is None:
        data["station_id"] = None
    user = User(id=uuid.uuid4(), **data, password_hash=hash_password(payload.password))
    db.add(user)
    record_audit(
        db, action=AuditAction.USER_CREATED, entity_type="user", entity_id=user.id, actor_user_id=actor.id,
        after={"username": user.username, "role": user.role, "station_id": user.station_id},
    )
    _commit_unique(db)
    return user


def list_users(
    db: Session,
    actor: User,
    *,
    role: Role | None,
    station_id: uuid.UUID | None,
    is_active: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[User], int]:
    require_permission(actor, Permission.USER_MANAGE)
    conditions = []
    if role:
        conditions.append(User.role == role)
    if station_id:
        conditions.append(User.station_id == station_id)
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))
    total = db.scalar(select(func.count(User.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(User).where(*conditions).order_by(User.full_name, User.id).limit(limit).offset(offset)
    )
    return list(rows), total


def get_user(db: Session, actor: User, user_id: uuid.UUID) -> User:
    require_permission(actor, Permission.USER_MANAGE)
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found", code="user_not_found")
    return user


def update_user(db: Session, actor: User, user_id: uuid.UUID, payload: UserUpdate) -> User:
    require_permission(actor, Permission.USER_MANAGE)
    user = db.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFound("User not found", code="user_not_found")
    changes = payload.model_dump(exclude_unset=True)
    if user.id == actor.id and (changes.get("is_active") is False or changes.get("role", Role.ADMIN) != Role.ADMIN):
        raise Forbidden("Administrators cannot deactivate or demote themselves", code="self_lockout")

    role = Role(changes.get("role", user.role))
    station_id = changes.get("station_id", user.station_id)
    badge = changes.get("police_badge_number", user.police_badge_number)
    _validate_role_fields(db, role, station_id, badge)

    before = {k: getattr(user, k) for k in changes}
    for key, value in changes.items():
        setattr(user, key, value)
    access_changed = any(k in changes for k in ("role", "station_id")) or changes.get("is_active") is False
    if access_changed:
        revoke_all_sessions(db, user, "access_changed")
    record_audit(
        db, action=AuditAction.USER_UPDATED, entity_type="user", entity_id=user.id, actor_user_id=actor.id,
        before=before, after=changes,
    )
    _commit_unique(db)
    return user


def reset_password(db: Session, actor: User, user_id: uuid.UUID, new_password: str) -> None:
    require_permission(actor, Permission.USER_MANAGE)
    from app.auth.mfa import require_step_up

    require_step_up(actor)
    user = db.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFound("User not found", code="user_not_found")
    validate_new_password(new_password, username=user.username)
    user.password_hash = hash_password(new_password)
    revoke_all_sessions(db, user, "password_reset")
    record_audit(db, action=AuditAction.PASSWORD_RESET, entity_type="user", entity_id=user.id, actor_user_id=actor.id)
    db.commit()


def list_assignable_officers(db: Session, actor: User, station_id: uuid.UUID | None) -> list[User]:
    require_permission(actor, Permission.USER_LIST_ASSIGNABLE)
    if Role(actor.role) is not Role.ADMIN:
        if station_id is not None and station_id != actor.station_id:
            raise Forbidden("You can only list officers at your own station", code="station_scope")
        station_id = actor.station_id
    stmt = select(User).where(User.role == Role.POLICE_OFFICER, User.is_active.is_(True))
    if station_id is not None:
        stmt = stmt.where(User.station_id == station_id)
    return list(db.scalars(stmt.order_by(User.full_name, User.id)))


def directory_lookup(db: Session, actor: User, ids: list[uuid.UUID]) -> list[User]:
    """Minimal identity for displaying names on case screens. Available to every authenticated role."""
    require_permission(actor, Permission.CASE_READ)
    if not ids:
        return []
    # Preserve request order while dropping duplicates and unknown ids.
    unique: list[uuid.UUID] = list(dict.fromkeys(ids))
    rows = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_(unique)))
    }
    return [rows[uid] for uid in unique if uid in rows]
