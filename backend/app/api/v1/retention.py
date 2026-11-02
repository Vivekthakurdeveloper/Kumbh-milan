import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession, requires
from app.auth.permissions import Permission
from app.common.errors import Conflict
from app.config import get_settings
from app.models.enums import RetentionPolicy
from app.schemas.retention import (
    LegalHoldCreate,
    LegalHoldRead,
    LegalHoldRelease,
    RetentionPolicyRead,
    RetentionRunCancel,
    RetentionRunCreate,
    RetentionRunRead,
)
from app.services import retention as retention_service
from app.storage import ObjectStorage, get_storage

router = APIRouter(tags=["Retention"], dependencies=[Depends(requires(Permission.RETENTION_MANAGE))])
STATE = {**AUTHED, **errors(404, 409)}


@router.get("/retention/policies", response_model=list[RetentionPolicyRead], responses=AUTHED)
def list_policies(principal: CurrentPrincipal):
    """Admin only. Configured retention periods and what each policy does."""
    return retention_service.policies()


@router.post("/retention/runs", response_model=RetentionRunRead, status_code=status.HTTP_201_CREATED, responses=AUTHED)
def plan_run(payload: RetentionRunCreate, principal: CurrentPrincipal, db: DbSession):
    """Dry run: lists what the policy would affect today, excluding legal holds. Changes no case data."""
    return retention_service.plan_run(db, principal.user, payload.policy, payload.note)


@router.get("/retention/runs", response_model=list[RetentionRunRead], responses=AUTHED)
def list_runs(
    principal: CurrentPrincipal, db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 25, offset: Annotated[int, Query(ge=0)] = 0,
):
    return retention_service.list_runs(db, principal.user, limit=limit, offset=offset)


@router.get("/retention/runs/{run_id}", response_model=RetentionRunRead, responses=STATE)
def get_run(run_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return retention_service.get_run(db, principal.user, run_id)


@router.post("/retention/runs/{run_id}/approve", response_model=RetentionRunRead, responses=STATE)
def approve_run(run_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    """Must be a different administrator from the one who planned the run."""
    return retention_service.approve_run(db, principal.user, run_id)


@router.post("/retention/runs/{run_id}/cancel", response_model=RetentionRunRead, responses=STATE)
def cancel_run(run_id: uuid.UUID, payload: RetentionRunCancel, principal: CurrentPrincipal, db: DbSession):
    return retention_service.cancel_run(db, principal.user, run_id, payload.reason)


@router.post("/retention/runs/{run_id}/execute", response_model=RetentionRunRead, responses=STATE)
def execute_run(
    run_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
):
    """
    Executes an approved run. Anonymisation needs the database owner connection, which API servers in
    staging/production do not hold: run `python -m app.cli retention-execute <run_id>` as the retention task.
    """
    run = retention_service.get_run(db, principal.user, run_id)
    settings = get_settings()
    if run.policy == RetentionPolicy.ANONYMIZE_ARCHIVED_CASES and settings.app_env in ("staging", "production"):
        raise Conflict("Run anonymisation with the retention task (retention-execute)", code="execute_via_retention_task")
    return retention_service.execute_run(db, storage, run_id, principal.user)


@router.get("/cases/{case_id}/legal-holds", response_model=list[LegalHoldRead], responses=STATE)
def list_holds(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return retention_service.list_holds(db, principal.user, case_id)


@router.post("/cases/{case_id}/legal-holds", response_model=LegalHoldRead, status_code=status.HTTP_201_CREATED, responses=STATE)
def place_hold(case_id: uuid.UUID, payload: LegalHoldCreate, principal: CurrentPrincipal, db: DbSession):
    """While a hold is active the case is never archived or anonymised."""
    return retention_service.place_hold(db, principal.user, case_id, payload.reason, payload.reference)


@router.post("/cases/{case_id}/legal-holds/{hold_id}/release", response_model=LegalHoldRead, responses=STATE)
def release_hold(case_id: uuid.UUID, hold_id: uuid.UUID, payload: LegalHoldRelease, principal: CurrentPrincipal, db: DbSession):
    return retention_service.release_hold(db, principal.user, case_id, hold_id, payload.reason)
