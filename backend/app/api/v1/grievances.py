"""Grievance management: citizen submission/tracking and the staff workflow."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession, _bearer, get_principal
from app.auth.public_tokens import decode_public_token
from app.common.crypto import decrypt_text
from app.common.errors import Forbidden, Unauthenticated
from app.common.time import utcnow
from app.models import Grievance
from app.models.enums import GrievanceCategory, GrievanceStatus, OtpPurpose, Role
from app.schemas.common import Page, PageParams
from app.schemas.grievances import (
    GrievanceAck,
    GrievanceAssign,
    GrievanceCitizenComment,
    GrievanceCitizenReopen,
    GrievanceCreate,
    GrievanceDetail,
    GrievancePublicRead,
    GrievanceRead,
    GrievanceStaffComment,
    GrievanceStatusChange,
    GrievanceSummary,
)
from app.services import grievances as grievance_service
from app.services.grievances import OPEN_STATES, Citizen

public_router = APIRouter(prefix="/public/grievances", tags=["Public grievances"])
router = APIRouter(prefix="/grievances", tags=["Grievances"])
CITIZEN = errors(401, 403, 404, 422)
STATE = {**AUTHED, **errors(404, 409)}


def citizen_identity(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verified_mobile_token: Annotated[str | None, Header(alias="X-Verified-Mobile-Token")] = None,
) -> Citizen:
    """A GRIEVANCE-purpose verified-mobile token, or a PUBLIC account bearer token."""
    if verified_mobile_token:
        claims = decode_public_token(verified_mobile_token, expected_typ="verified_mobile")
        if claims.get("purpose") != OtpPurpose.GRIEVANCE.value or not claims.get("mobile"):
            raise Unauthenticated("Verified mobile token purpose mismatch", code="invalid_token")
        return Citizen(mobile=claims["mobile"])
    if credentials is None:
        raise Unauthenticated("Verify your mobile number or sign in to continue")
    principal = get_principal(request, credentials, db)
    if Role(principal.user.role) is not Role.PUBLIC:
        raise Forbidden("Staff accounts use the staff grievance endpoints", code="permission_denied")
    return Citizen(user_id=principal.user.id)


CitizenDep = Annotated[Citizen, Depends(citizen_identity)]


def _public_read(db, row: Grievance, *, with_events: bool) -> GrievancePublicRead:
    return GrievancePublicRead(
        reference=row.reference, category=row.category, subject=row.subject, status=row.status,
        created_at=row.created_at, updated_at=row.updated_at, resolved_at=row.resolved_at,
        events=grievance_service.public_events(db, row) if with_events else [],
    )


def _read(row: Grievance) -> GrievanceRead:
    return GrievanceRead(
        id=row.id, reference=row.reference, category=row.category, status=row.status, priority=row.priority,
        station_id=row.station_id, case_id=row.case_id, subject=row.subject,
        assigned_to_user_id=row.assigned_to_user_id, mobile_hint=row.mobile_hint, due_at=row.due_at,
        overdue=row.status in OPEN_STATES and row.due_at < utcnow(), escalation_level=row.escalation_level,
        escalated_at=row.escalated_at, resolved_at=row.resolved_at, closed_at=row.closed_at,
        created_at=row.created_at, updated_at=row.updated_at, version=row.version,
    )


def _detail(db, row: Grievance) -> GrievanceDetail:
    return GrievanceDetail(
        **_read(row).model_dump(),
        description=decrypt_text(row.description_ciphertext),
        resolution=decrypt_text(row.resolution_ciphertext) if row.resolution_ciphertext else None,
        events=grievance_service.staff_events(db, row),
    )


@public_router.post("", response_model=GrievanceAck, status_code=status.HTTP_201_CREATED, responses=CITIZEN,
                    summary="Submit a grievance (verified mobile token or citizen account)")
def submit(payload: GrievanceCreate, citizen: CitizenDep, db: DbSession) -> GrievanceAck:
    row = grievance_service.submit(db, citizen, **payload.model_dump())
    return GrievanceAck(
        reference=row.reference, status=row.status,
        message="Your grievance is registered. Keep this reference number to track it.",
    )


@public_router.get("", response_model=Page[GrievancePublicRead], responses=CITIZEN,
                   summary="Grievances submitted by this citizen")
def citizen_list(citizen: CitizenDep, db: DbSession, page: Annotated[PageParams, Depends()]):
    rows, total = grievance_service.citizen_list(db, citizen, limit=page.limit, offset=page.offset)
    return Page(items=[_public_read(db, r, with_events=False) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@public_router.get("/{reference}", response_model=GrievancePublicRead, responses=CITIZEN)
def citizen_get(reference: str, citizen: CitizenDep, db: DbSession):
    return _public_read(db, grievance_service.citizen_get(db, citizen, reference), with_events=True)


@public_router.post("/{reference}/comments", response_model=GrievancePublicRead, responses=CITIZEN)
def citizen_comment(reference: str, payload: GrievanceCitizenComment, citizen: CitizenDep, db: DbSession):
    row = grievance_service.citizen_comment(db, citizen, reference, payload.message)
    return _public_read(db, row, with_events=True)


@public_router.post("/{reference}/reopen", response_model=GrievancePublicRead, responses=CITIZEN)
def citizen_reopen(reference: str, payload: GrievanceCitizenReopen, citizen: CitizenDep, db: DbSession):
    row = grievance_service.citizen_reopen(db, citizen, reference, payload.reason)
    return _public_read(db, row, with_events=True)


@router.get("", response_model=Page[GrievanceRead], responses=AUTHED, summary="Grievance queue (station scoped)")
def staff_list(
    principal: CurrentPrincipal,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    status_: Annotated[list[GrievanceStatus] | None, Query(alias="status")] = None,
    category: GrievanceCategory | None = None,
    assigned_to_user_id: uuid.UUID | None = None,
    overdue: bool | None = None,
):
    rows, total = grievance_service.staff_list(
        db, principal.user, status=status_, category=category, assigned_to_user_id=assigned_to_user_id,
        overdue=overdue, limit=page.limit, offset=page.offset,
    )
    return Page(items=[_read(r) for r in rows], total=total, limit=page.limit, offset=page.offset)


@router.get("/summary", response_model=GrievanceSummary, responses=AUTHED,
            summary="Scoped grievance counts by status and category, plus overdue and escalated")
def summary(principal: CurrentPrincipal, db: DbSession):
    return grievance_service.summary(db, principal.user)


@router.get("/{grievance_id}", response_model=GrievanceDetail, responses={**AUTHED, **errors(404)})
def staff_get(grievance_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return _detail(db, grievance_service.load(db, principal.user, grievance_id))


@router.post("/{grievance_id}/assign", response_model=GrievanceDetail, responses=STATE)
def staff_assign(grievance_id: uuid.UUID, payload: GrievanceAssign, principal: CurrentPrincipal, db: DbSession):
    row = grievance_service.assign(
        db, principal.user, grievance_id, payload.assignee_user_id, payload.reason, payload.expected_version
    )
    return _detail(db, row)


@router.post("/{grievance_id}/status", response_model=GrievanceDetail, responses=STATE)
def staff_status(
    grievance_id: uuid.UUID, payload: GrievanceStatusChange, principal: CurrentPrincipal, db: DbSession
):
    row = grievance_service.change_status(
        db, principal.user, grievance_id, payload.status, payload.message, payload.expected_version
    )
    return _detail(db, row)


@router.post("/{grievance_id}/comments", response_model=GrievanceDetail, responses=STATE)
def staff_comment(
    grievance_id: uuid.UUID, payload: GrievanceStaffComment, principal: CurrentPrincipal, db: DbSession
):
    return _detail(db, grievance_service.comment(db, principal.user, grievance_id, payload.message, payload.internal))
