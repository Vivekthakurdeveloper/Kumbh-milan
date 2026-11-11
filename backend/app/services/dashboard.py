"""Operational counts for the console's home screen, limited to what the user may see."""

from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import ValidationFailed
from app.common.time import kolkata_day_end_exclusive, kolkata_day_start, utcnow
from app.models import Case, CaseAttachment, CaseMatch, Notification, User
from app.models.enums import CaseStatus, MatchStatus, NotificationStatus, ScanStatus, UrgencyLevel
from app.services.cases import CASE_NUMBER_TZ

ACTIVE = (CaseStatus.OPEN, CaseStatus.UNDER_REVIEW, CaseStatus.ASSIGNED, CaseStatus.MATCH_FOUND, CaseStatus.REOPENED)


def summary(
    db: Session,
    user: User,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    require_permission(user, Permission.CASE_READ)
    if (date_from is None) ^ (date_to is None):
        raise ValidationFailed(
            "from and to must be provided together",
            code="invalid_range",
            details={"from": date_from, "to": date_to},
        )
    if date_from is not None and date_to is not None and date_to < date_from:
        raise ValidationFailed(
            "to must be on or after from",
            code="invalid_range",
            details={"from": str(date_from), "to": str(date_to)},
        )

    scoped = and_(case_visibility_clause(user), Case.archived_at.is_(None))
    if date_from is not None and date_to is not None:
        range_start = kolkata_day_start(date_from)
        range_end = kolkata_day_end_exclusive(date_to)
        scoped = and_(scoped, Case.created_at >= range_start, Case.created_at < range_end)
        local_midnight = range_start
    else:
        local_midnight = utcnow().astimezone(CASE_NUMBER_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

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
        "registered_today": count(select(Case.id).where(scoped, Case.created_at >= local_midnight))
        if date_from is None
        else count(select(Case.id).where(scoped)),
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
            select(Notification.id).where(
                Notification.case_id.in_(in_scope_ids), Notification.status == NotificationStatus.FAILED
            )
        ),
    }
