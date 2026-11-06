"""In-app staff notifications for authorised officers."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.models.enums import StaffNotificationStatus
from app.schemas.common import Page, PageParams
from app.schemas.faces import StaffNotificationRead
from app.services import staff_notifications as staff_notification_service

router = APIRouter(prefix="/staff-notifications", tags=["Staff notifications"])
NOT_FOUND = {**AUTHED, **errors(404)}


@router.get(
    "",
    response_model=Page[StaffNotificationRead],
    responses=AUTHED,
    summary="In-app notifications for the authenticated officer",
)
def list_notifications(
    principal: CurrentPrincipal,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    status: StaffNotificationStatus | None = None,
) -> Page[StaffNotificationRead]:
    items, total = staff_notification_service.list_for_user(
        db, principal.user, status=status, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/{notification_id}/read",
    response_model=StaffNotificationRead,
    responses=NOT_FOUND,
    summary="Mark a staff notification as read",
)
def mark_read(
    notification_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> StaffNotificationRead:
    return staff_notification_service.mark_read(db, principal.user, notification_id)
