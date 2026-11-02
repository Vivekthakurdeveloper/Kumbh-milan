import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.responses import AUTHED
from app.auth.dependencies import CurrentPrincipal, DbSession, requires
from app.auth.permissions import Permission
from app.schemas.common import Page, PageParams
from app.schemas.workflow import AuditEventRead
from app.services import workflow as workflow_service

router = APIRouter(
    prefix="/audit-events", tags=["Audit"], dependencies=[Depends(requires(Permission.AUDIT_READ))]
)


@router.get("", response_model=Page[AuditEventRead], responses=AUTHED)
def list_audit_events(
    principal: CurrentPrincipal,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    case_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
):
    """Admin only. Newest first. Payloads are redacted at write time."""
    rows, total = workflow_service.list_audit_events(
        db, principal.user, case_id=case_id, actor_user_id=actor_user_id, action=action,
        created_from=created_from, created_to=created_to, limit=page.limit, offset=page.offset,
    )
    return Page(items=[AuditEventRead.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset)
