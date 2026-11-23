"""Supervisor approval queue and bulk assignment."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.models.enums import AssignmentRequestStatus
from app.schemas.common import Page, PageParams
from app.schemas.workflow import (
    AssignmentDecisionRequest,
    AssignmentRequestRead,
    BulkAssignRequest,
    BulkAssignResult,
)
from app.services import assignments as assignment_service

router = APIRouter(tags=["Assignments"])
STATE = {**AUTHED, **errors(404, 409)}


@router.get("/assignment-requests", response_model=Page[AssignmentRequestRead], responses=AUTHED,
            summary="Assignment requests (supervisors see all; requesters see their own)")
def list_requests(
    principal: CurrentPrincipal, db: DbSession, page: Annotated[PageParams, Depends()],
    status: AssignmentRequestStatus | None = None,
) -> Page[AssignmentRequestRead]:
    rows, total = assignment_service.list_requests(
        db, principal.user, status=status, limit=page.limit, offset=page.offset
    )
    return Page(items=[AssignmentRequestRead.model_validate(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@router.get("/assignment-requests/{request_id}", response_model=AssignmentRequestRead, responses=STATE)
def get_request(request_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return assignment_service.get_request(db, principal.user, request_id)


@router.post("/assignment-requests/{request_id}/approve", response_model=AssignmentRequestRead, responses=STATE,
             summary="Supervisor approves and applies the assignment")
def approve(request_id: uuid.UUID, payload: AssignmentDecisionRequest, principal: CurrentPrincipal, db: DbSession):
    return assignment_service.approve(db, principal.user, request_id, payload.reason)


@router.post("/assignment-requests/{request_id}/reject", response_model=AssignmentRequestRead, responses=STATE)
def reject(request_id: uuid.UUID, payload: AssignmentDecisionRequest, principal: CurrentPrincipal, db: DbSession):
    return assignment_service.reject(db, principal.user, request_id, payload.reason)


@router.post("/assignment-requests/{request_id}/cancel", response_model=AssignmentRequestRead, responses=STATE)
def cancel(request_id: uuid.UUID, payload: AssignmentDecisionRequest, principal: CurrentPrincipal, db: DbSession):
    return assignment_service.cancel(db, principal.user, request_id, payload.reason)


@router.post("/cases/bulk-assign", response_model=BulkAssignResult, responses=AUTHED,
             summary="Assign up to 100 cases (requests are created when approval is required)")
def bulk_assign(payload: BulkAssignRequest, principal: CurrentPrincipal, db: DbSession) -> BulkAssignResult:
    return BulkAssignResult(items=assignment_service.bulk_assign(db, principal.user, payload))
