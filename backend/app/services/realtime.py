"""Realtime staff events derived from the append-only audit log (BE-106).

Events carry identifiers only, never before/after payloads. Every poll re-checks the session and filters by
the caller's current case and grievance scope, so a role or station change takes effect on the next batch.
A short lag window keeps the (created_at, id) cursor safe against transactions that commit out of order.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.orm import Session

from app.audit import AuditAction
from app.auth.permissions import Permission, has_permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import Unauthenticated, ValidationFailed
from app.common.time import utcnow
from app.models import AuditEvent, Case, Grievance, RefreshToken, User
from app.models.enums import Role

CASE_EVENTS = {
    AuditAction.CASE_CREATED: "case.created",
    AuditAction.CASE_UPDATED: "case.updated",
    AuditAction.CASE_ASSIGNED: "case.assigned",
    AuditAction.CASE_UNASSIGNED: "case.unassigned",
    AuditAction.CASE_STATUS_CHANGED: "case.status_changed",
    AuditAction.CASE_CLOSED: "case.closed",
    AuditAction.CASE_REOPENED: "case.reopened",
    AuditAction.CUSTODY_CHANGED: "case.custody_changed",
    AuditAction.NOTE_ADDED: "case.note_added",
    AuditAction.ATTACHMENT_UPLOADED: "case.attachment_added",
    AuditAction.PUBLIC_CASE_SUBMITTED: "case.public_submitted",
    AuditAction.PUBLIC_CASE_REVIEWED: "case.public_reviewed",
    AuditAction.MATCH_PROPOSED: "match.proposed",
    AuditAction.MATCH_SUGGESTED: "match.suggested",
    AuditAction.MATCH_FIRST_APPROVAL: "match.first_approval",
    AuditAction.MATCH_REVIEWED: "match.reviewed",
    AuditAction.MATCH_REVOKED: "match.revoked",
    AuditAction.HANDOVER_CREATED: "handover.created",
    AuditAction.HANDOVER_VERIFIED: "handover.verified",
    AuditAction.HANDOVER_COMPLETED: "handover.completed",
    AuditAction.HANDOVER_REJECTED: "handover.rejected",
    AuditAction.NOTIFICATION_FAILED: "notification.failed",
    AuditAction.ASSIGNMENT_REQUESTED: "assignment.requested",
    AuditAction.ASSIGNMENT_APPROVED: "assignment.approved",
    AuditAction.ASSIGNMENT_REJECTED: "assignment.rejected",
}
GRIEVANCE_EVENTS = {
    AuditAction.GRIEVANCE_SUBMITTED: "grievance.submitted",
    AuditAction.GRIEVANCE_ASSIGNED: "grievance.assigned",
    AuditAction.GRIEVANCE_STATUS_CHANGED: "grievance.status_changed",
    AuditAction.GRIEVANCE_COMMENTED: "grievance.commented",
    AuditAction.GRIEVANCE_ESCALATED: "grievance.escalated",
}
EVENT_TYPES = {**CASE_EVENTS, **GRIEVANCE_EVENTS}
MAX_BATCH = 200


def encode_cursor(created_at: datetime, event_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{event_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created, event_id = raw.split("|", 1)
        value = datetime.fromisoformat(created)
        if value.tzinfo is None:
            raise ValueError
        return value, uuid.UUID(event_id)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ValidationFailed("Invalid event cursor", code="invalid_cursor") from None


def initial_cursor() -> str:
    return encode_cursor(utcnow(), uuid.UUID(int=0))


def revalidate(db: Session, user_id: uuid.UUID, session_id: uuid.UUID, token_version: int) -> User:
    """Long-lived streams outlive the request that authenticated them, so re-check the session each poll."""
    user = db.get(User, user_id, populate_existing=True)
    session = db.get(RefreshToken, session_id, populate_existing=True)
    if (
        user is None
        or not user.is_active
        or user.token_version != token_version
        or session is None
        or session.revoked_at is not None
        or session.expires_at <= utcnow()
    ):
        raise Unauthenticated("Session is no longer valid", code="session_revoked")
    return user


def fetch(db: Session, user: User, cursor: str, *, lag_seconds: float, limit: int = MAX_BATCH) -> tuple[list[dict], str]:
    require_permission(user, Permission.CASE_READ)
    after_at, after_id = decode_cursor(cursor)
    horizon = utcnow() - timedelta(seconds=lag_seconds)
    visible_cases = select(Case.id).where(case_visibility_clause(user))
    visibility = [and_(AuditEvent.action.in_(list(CASE_EVENTS)), AuditEvent.case_id.in_(visible_cases))]
    if has_permission(user, Permission.GRIEVANCE_READ):
        grievance_scope = select(Grievance.id)
        if Role(user.role) is not Role.ADMIN:
            grievance_scope = grievance_scope.where(
                or_(Grievance.station_id == user.station_id, Grievance.assigned_to_user_id == user.id)
            )
        visibility.append(
            and_(AuditEvent.action.in_(list(GRIEVANCE_EVENTS)), AuditEvent.entity_id.in_(grievance_scope))
        )
    rows = list(
        db.scalars(
            select(AuditEvent)
            .where(
                tuple_(AuditEvent.created_at, AuditEvent.id) > tuple_(after_at, after_id),
                AuditEvent.created_at <= horizon,
                or_(*visibility),
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
            .limit(min(limit, MAX_BATCH))
        )
    )
    events = [
        {
            "id": encode_cursor(row.created_at, row.id),
            "type": EVENT_TYPES[row.action],
            "case_id": row.case_id,
            "case_number": row.case_number,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "occurred_at": row.created_at,
        }
        for row in rows
    ]
    next_cursor = events[-1]["id"] if events else cursor
    return events, next_cursor
