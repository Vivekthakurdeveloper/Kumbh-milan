"""Operational counts for the console's home screen, limited to what the user may see."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.time import utcnow
from app.models import Case, CaseAttachment, CaseMatch, Notification, User
from app.models.enums import CaseStatus, MatchStatus, NotificationStatus, ScanStatus, UrgencyLevel
from app.services.cases import CASE_NUMBER_TZ

ACTIVE = (CaseStatus.OPEN, CaseStatus.UNDER_REVIEW, CaseStatus.ASSIGNED, CaseStatus.MATCH_FOUND, CaseStatus.REOPENED)


def summary(db: Session, user: User) -> dict:
    require_permission(user, Permission.CASE_READ)
    scoped = and_(case_visibility_clause(user), Case.archived_at.is_(None))
    local_midnight = utcnow().astimezone(CASE_NUMBER_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    by_type_status = db.execute(
        select(Case.case_type, Case.status, func.count()).where(scoped).group_by(Case.case_type, Case.status)
    ).all()
    active = select(Case.id).where(scoped, Case.status.in_(ACTIVE))
    count = lambda stmt: db.scalar(select(func.count()).select_from(stmt.subquery())) or 0  # noqa: E731

    open_by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for case_type, status, n in by_type_status:
        by_status[status] = by_status.get(status, 0) + n
        if status in ACTIVE:
            open_by_type[case_type] = open_by_type.get(case_type, 0) + n

    in_scope_ids = select(Case.id).where(scoped)
    return {
        "generated_at": utcnow(),
        "active_by_type": open_by_type,
        "by_status": by_status,
        "active_total": sum(open_by_type.values()),
        "urgent_active": count(active.where(Case.urgency_level.in_((UrgencyLevel.CRITICAL, UrgencyLevel.HIGH)))),
        "unassigned_active": count(active.where(Case.assigned_to_user_id.is_(None))),
        "registered_today": count(select(Case.id).where(scoped, Case.created_at >= local_midnight)),
        "pending_match_reviews": count(
            select(CaseMatch.id).where(
                CaseMatch.status == MatchStatus.PROPOSED,
                or_(CaseMatch.left_case_id.in_(in_scope_ids), CaseMatch.right_case_id.in_(in_scope_ids)),
            )
        ),
        "attachments_awaiting_scan": count(
            select(CaseAttachment.id).where(
                CaseAttachment.case_id.in_(in_scope_ids),
                CaseAttachment.scan_status.in_((ScanStatus.QUARANTINED, ScanStatus.SCANNING)),
            )
        ),
        "notifications_failed": count(
            select(Notification.id).where(Notification.case_id.in_(in_scope_ids), Notification.status == NotificationStatus.FAILED)
        ),
    }
