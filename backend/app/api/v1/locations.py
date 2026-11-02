import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession, requires
from app.auth.permissions import Permission
from app.models.enums import LocationKind
from app.schemas.geo import LocationCreate, LocationRead, LocationUpdate
from app.services import geo as geo_service

router = APIRouter(prefix="/locations", tags=["Locations"])
_ADMIN = [Depends(requires(Permission.LOCATION_MANAGE))]
SearchText = Annotated[str | None, Query(max_length=100, description="Matches name, local name, or code")]


@router.get("", response_model=list[LocationRead], responses=AUTHED, summary="Ghats, sectors, and landmarks")
def list_locations(
    principal: CurrentPrincipal,
    db: DbSession,
    kind: LocationKind | None = None,
    sector_id: uuid.UUID | None = None,
    q: SearchText = None,
    include_inactive: bool = False,
):
    return geo_service.list_locations(db, kind=kind, sector_id=sector_id, q=q, include_inactive=include_inactive)


@router.get("/sectors", response_model=list[LocationRead], responses=AUTHED, summary="Mela sectors")
def list_sectors(principal: CurrentPrincipal, db: DbSession, q: SearchText = None):
    return geo_service.list_locations(db, kind=LocationKind.SECTOR, sector_id=None, q=q, include_inactive=False)


@router.get("/ghats", response_model=list[LocationRead], responses=AUTHED, summary="Ghats, optionally within a sector")
def list_ghats(principal: CurrentPrincipal, db: DbSession, sector_id: uuid.UUID | None = None, q: SearchText = None):
    return geo_service.list_locations(db, kind=LocationKind.GHAT, sector_id=sector_id, q=q, include_inactive=False)


@router.get("/{location_id}", response_model=LocationRead, responses={**AUTHED, **errors(404)})
def get_location(location_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return geo_service.get_location(db, location_id)


@router.post("", dependencies=_ADMIN, response_model=LocationRead, status_code=status.HTTP_201_CREATED, responses={**AUTHED, **errors(409)})
def create_location(payload: LocationCreate, principal: CurrentPrincipal, db: DbSession):
    """Admin only. Coordinates must come from a survey or an authoritative map, not estimation."""
    return geo_service.create_location(db, principal.user, payload)


@router.patch("/{location_id}", dependencies=_ADMIN, response_model=LocationRead, responses={**AUTHED, **errors(404, 409)})
def update_location(location_id: uuid.UUID, payload: LocationUpdate, principal: CurrentPrincipal, db: DbSession):
    """Admin only. Deactivate instead of deleting: cases keep their reference."""
    return geo_service.update_location(db, principal.user, location_id, payload)
