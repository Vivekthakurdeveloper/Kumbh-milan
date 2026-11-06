import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession, requires
from app.auth.permissions import Permission
from app.models.enums import Role
from app.schemas.admin import (
    OfficerSummary,
    PasswordResetRequest,
    StationCreate,
    StationRead,
    StationUpdate,
    UserCreate,
    UserDirectoryEntry,
    UserRead,
    UserUpdate,
)
from app.schemas.common import Page, PageParams
from app.services import admin as admin_service

stations = APIRouter(prefix="/stations", tags=["Stations"])
users = APIRouter(prefix="/users", tags=["Users"])

_STATION_ADMIN = [Depends(requires(Permission.STATION_MANAGE))]
_USER_ADMIN = [Depends(requires(Permission.USER_MANAGE))]


@stations.get("", response_model=list[StationRead], responses=AUTHED)
def list_stations(
    principal: CurrentPrincipal, db: DbSession, include_inactive: bool = False
) -> list[StationRead]:
    """All stations and help desks (used for registration and filtering)."""
    return admin_service.list_stations(db, principal.user, include_inactive=include_inactive)


@stations.post("", dependencies=_STATION_ADMIN, response_model=StationRead, status_code=status.HTTP_201_CREATED, responses={**AUTHED, **errors(409)})
def create_station(payload: StationCreate, principal: CurrentPrincipal, db: DbSession) -> StationRead:
    """Admin only."""
    return admin_service.create_station(db, principal.user, payload)


@stations.patch("/{station_id}", dependencies=_STATION_ADMIN, response_model=StationRead, responses={**AUTHED, **errors(404, 409)})
def update_station(
    station_id: uuid.UUID, payload: StationUpdate, principal: CurrentPrincipal, db: DbSession
) -> StationRead:
    """Admin only. Deactivate instead of deleting: stations are referenced by case history."""
    return admin_service.update_station(db, principal.user, station_id, payload)


@users.get("/assignable-officers", response_model=list[OfficerSummary], responses=AUTHED)
def assignable_officers(
    principal: CurrentPrincipal, db: DbSession, station_id: uuid.UUID | None = None
) -> list[OfficerSummary]:
    """Active police officers who can receive assignments. Officers see their own station only."""
    return admin_service.list_assignable_officers(db, principal.user, station_id)


@users.get(
    "/directory",
    response_model=list[UserDirectoryEntry],
    responses=AUTHED,
    summary="Resolve user ids to display names (all roles)",
)
def user_directory(
    principal: CurrentPrincipal,
    db: DbSession,
    ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=100)],
) -> list[UserDirectoryEntry]:
    """
    Returns id, full_name, role, and station_id only. Use this to show who reported a case,
    who is assigned, or who wrote a note. Unknown ids are omitted.
    """
    return [
        UserDirectoryEntry.model_validate(u)
        for u in admin_service.directory_lookup(db, principal.user, ids)
    ]


@users.get("", dependencies=_USER_ADMIN, response_model=Page[UserRead], responses=AUTHED)
def list_users(
    principal: CurrentPrincipal,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    role: Role | None = None,
    station_id: uuid.UUID | None = None,
    is_active: bool | None = None,
) -> Page[UserRead]:
    """Admin only."""
    rows, total = admin_service.list_users(
        db, principal.user, role=role, station_id=station_id, is_active=is_active, limit=page.limit, offset=page.offset
    )
    return Page(items=[UserRead.model_validate(u) for u in rows], total=total, limit=page.limit, offset=page.offset)


@users.post("", dependencies=_USER_ADMIN, response_model=UserRead, status_code=status.HTTP_201_CREATED, responses={**AUTHED, **errors(409)})
def create_user(payload: UserCreate, principal: CurrentPrincipal, db: DbSession) -> UserRead:
    """Admin only. Officers and help-desk operators must belong to an active station."""
    return admin_service.create_user(db, principal.user, payload)


@users.get("/{user_id}", dependencies=_USER_ADMIN, response_model=UserRead, responses={**AUTHED, **errors(404)})
def get_user(user_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> UserRead:
    """Admin only."""
    return admin_service.get_user(db, principal.user, user_id)


@users.patch("/{user_id}", dependencies=_USER_ADMIN, response_model=UserRead, responses={**AUTHED, **errors(404, 409)})
def update_user(user_id: uuid.UUID, payload: UserUpdate, principal: CurrentPrincipal, db: DbSession) -> UserRead:
    """Admin only. Changing role or station, or deactivating, revokes the user's sessions."""
    return admin_service.update_user(db, principal.user, user_id, payload)


@users.post("/{user_id}/reset-password", dependencies=_USER_ADMIN, status_code=status.HTTP_204_NO_CONTENT, responses={**AUTHED, **errors(404)})
def reset_password(
    user_id: uuid.UUID, payload: PasswordResetRequest, principal: CurrentPrincipal, db: DbSession
) -> None:
    """Admin only. Sets a new password and revokes every session for the user."""
    admin_service.reset_password(db, principal.user, user_id, payload.new_password)
