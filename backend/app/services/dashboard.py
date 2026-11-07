"""Operational counts for the console's home screen, limited to what the user may see."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import ValidationFailed
from app.common.time import utcnow
from app.models import Case, CaseAttachment, CaseMatch, Notification, User
from app.models.enums import CaseStatus, MatchStatus, NotificationStatus, ScanStatus, UrgencyLevel
from app.services.cases import CASE_NUMBER_TZ

ACTIVE = (CaseStatus.OPEN, CaseStatus.UNDER_REVIEW, CaseStatus.ASSIGNED, CaseStatus.MATCH_FOUND, CaseStatus.REOPENED)
MAX_PERIOD = timedelta(days=366)


def _parse_bound(raw: str, name: str, *, end_of_period: bool) -> datetime:
    """
    A date-only value (2026-11-06) means that whole Asia/Kolkata day: it starts at 00:00 and, as `to`,
    ends at 23:59:59.999999. A datetime without an offset is read as Asia/Kolkata time.
    """
    text = raw.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            if end_of_period:
                return datetime.combine(day, time.max, tzinfo=CASE_NUMBER_TZ)
            return datetime.combine(day, time.min, tzinfo=CASE_NUMBER_TZ)
        value = datetime.fromisoformat(text)
    except ValueError:
        raise ValidationFailed(
            f"{name} must be an ISO 8601 date (2026-11-06) or datetime (2026-11-06T09:30:00+05:30)",
            code="invalid_period",
        ) from None
    return value if value.tzinfo is not None else value.replace(tzinfo=CASE_NUMBER_TZ)


def resolve_period(from_: str | None, to: str | None) -> tuple[datetime | None, datetime | None]:
    """Validates the optional reporting window. Either side may be open; both bounds are inclusive."""
    start = _parse_bound(from_, "from", end_of_period=False) if from_ else None
    end = _parse_bound(to, "to", end_of_period=True) if to else None
    if start is not None and end is not None:
        if end < start:
            raise ValidationFailed("to must not be earlier than from", code="invalid_period")
        if end - start > MAX_PERIOD:
            raise ValidationFailed("The period may not be longer than 366 days", code="invalid_period")
    return start, end


def summary(db: Session, user: User, *, from_: str | None = None, to: str | None = None) -> dict:
    """
    `from_`/`to` narrow the four triage counters (urgent_active, unassigned_active, registered_today,
    pending_match_reviews) to cases registered, and matches proposed, inside the window. Without either, the
    counters keep their original meaning: the whole active backlog, and registrations since local midnight.
    The other fields always describe the current state.
    """
    require_permission(user, Permission.CASE_READ)
    start, end = resolve_period(from_, to)
    windowed = start is not None or end is not None
    if not windowed:
        start = utcnow().astimezone(CASE_NUMBER_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    def case_in_period(stmt):
        if start is not None:
            stmt = stmt.where(Case.created_at >= start)
        return stmt.where(Case.created_at <= end) if end is not None else stmt

    def narrowed(stmt):
        return case_in_period(stmt) if windowed else stmt

    def match_in_period(stmt):
        if not windowed:
            return stmt
        if start is not None:
            stmt = stmt.where(CaseMatch.created_at >= start)
        return stmt.where(CaseMatch.created_at <= end) if end is not None else stmt

    scoped = and_(case_visibility_clause(user), Case.archived_at.is_(None))

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
        "period_from": start if windowed else None,
        "period_to": end if windowed else None,
        "urgent_active": count(
            narrowed(active.where(Case.urgency_level.in_((UrgencyLevel.CRITICAL, UrgencyLevel.HIGH))))
        ),
        "unassigned_active": count(narrowed(active.where(Case.assigned_to_user_id.is_(None)))),
        "registered_today": count(case_in_period(select(Case.id).where(scoped))),
        "pending_match_reviews": count(
            match_in_period(
                select(CaseMatch.id).where(
                    CaseMatch.status == MatchStatus.PROPOSED,
                    or_(CaseMatch.left_case_id.in_(in_scope_ids), CaseMatch.right_case_id.in_(in_scope_ids)),
                )
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
