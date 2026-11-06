"""In-app staff notifications (no SMS / WhatsApp / email)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.common.errors import NotFound
from app.common.time import utcnow
from app.models import StaffNotification, User
from app.models.enums import StaffNotificationStatus
from app.schemas.faces import StaffNotificationRead


def list_for_user(
    db: Session,
    actor: User,
    *,
    status: StaffNotificationStatus | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[StaffNotificationRead], int]:
    require_permission(actor, Permission.CASE_READ)
    conditions = [StaffNotification.recipient_user_id == actor.id]
    if status is not None:
        conditions.append(StaffNotification.status == status)
    total = db.scalar(select(func.count()).select_from(StaffNotification).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(StaffNotification)
            .where(*conditions)
            .order_by(StaffNotification.created_at.desc(), StaffNotification.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return [_read(row) for row in rows], total


def mark_read(db: Session, actor: User, notification_id: uuid.UUID) -> StaffNotificationRead:
    require_permission(actor, Permission.CASE_READ)
    row = db.scalar(
        select(StaffNotification)
        .where(StaffNotification.id == notification_id, StaffNotification.recipient_user_id == actor.id)
        .with_for_update()
    )
    if row is None:
        raise NotFound("Notification not found", code="staff_notification_not_found")
    if row.status != StaffNotificationStatus.READ:
        row.status = StaffNotificationStatus.READ
        row.read_at = utcnow()
        db.commit()
        db.refresh(row)
    return _read(row)


def _read(row: StaffNotification) -> StaffNotificationRead:
    return StaffNotificationRead(
        id=row.id,
        kind=row.kind,
        status=row.status,
        match_id=row.match_id,
        link=f"/matches/{row.match_id}",
        created_at=row.created_at,
        read_at=row.read_at,
    )
