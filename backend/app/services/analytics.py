"""Operational analytics for the dashboard (BE-201). Every aggregate is limited to the caller's case scope.

Periods filter on case registration time. Day buckets use Asia/Kolkata calendar days. Durations are medians in
seconds over cases registered in the period that reached the milestone.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Float, and_, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.time import utcnow
from app.models import (
    AuditEvent,
    Case,
    CaseAssignment,
    CaseHandover,
    CaseMatch,
    Grievance,
    Location,
    Notification,
    User,
)
from app.models.enums import CaseStatus, HandoverStatus, MatchStatus, NotificationStatus, Role
from app.services.dashboard import ACTIVE, resolve_period
from app.services.realtime import CASE_EVENTS

DEFAULT_PERIOD = timedelta(days=30)
AGE_BUCKETS = (
    ("under_6h", timedelta(0), timedelta(hours=6)),
    ("6h_to_24h", timedelta(hours=6), timedelta(hours=24)),
    ("1d_to_3d", timedelta(days=1), timedelta(days=3)),
    ("3d_to_7d", timedelta(days=3), timedelta(days=7)),
    ("over_7d", timedelta(days=7), None),
)


def _median_seconds(db: Session, milestone, case_ids) -> float | None:
    """Median of (milestone - cases.created_at) for cases in `case_ids` that reached the milestone."""
    seconds = func.extract("epoch", milestone.c.reached_at - Case.created_at)
    value = db.scalar(
        select(func.percentile_cont(0.5).within_group(cast(seconds, Float)))
        .select_from(Case)
        .join(milestone, milestone.c.case_id == Case.id)
        .where(Case.id.in_(case_ids))
    )
    return round(float(value), 1) if value is not None else None


def analytics(db: Session, user: User, *, from_: str | None, to: str | None) -> dict:
    require_permission(user, Permission.ANALYTICS_READ)
    start, end = resolve_period(from_, to)
    now = utcnow()
    end = end or now
    start = start or end - DEFAULT_PERIOD

    scope = and_(case_visibility_clause(user), Case.archived_at.is_(None))
    in_period = and_(scope, Case.created_at >= start, Case.created_at <= end)
    period_ids = select(Case.id).where(in_period)

    def grouped(column) -> dict[str, int]:
        rows = db.execute(select(column, func.count()).select_from(Case).where(in_period).group_by(column)).all()
        return {str(key) if key is not None else "UNSPECIFIED": n for key, n in rows}

    day = func.to_char(func.timezone("Asia/Kolkata", Case.created_at), "YYYY-MM-DD")
    by_day = {
        key: n
        for key, n in db.execute(
            select(day, func.count()).where(in_period).group_by(day).order_by(day)
        ).all()
    }
    sector = func.coalesce(Location.sector_id, Location.id)
    by_sector = {
        str(key) if key is not None else "UNLINKED": n
        for key, n in db.execute(
            select(sector, func.count()).select_from(Case).outerjoin(Location, Location.id == Case.location_id)
            .where(in_period).group_by(sector)
        ).all()
    }

    first_assignment = (
        select(CaseAssignment.case_id, func.min(CaseAssignment.assigned_at).label("reached_at"))
        .group_by(CaseAssignment.case_id).subquery()
    )
    left = select(CaseMatch.left_case_id.label("case_id"), CaseMatch.reviewed_at.label("reached_at")).where(
        CaseMatch.status == MatchStatus.CONFIRMED
    )
    right = select(CaseMatch.right_case_id.label("case_id"), CaseMatch.reviewed_at.label("reached_at")).where(
        CaseMatch.status == MatchStatus.CONFIRMED
    )
    confirmed = left.union_all(right).subquery()
    first_match = select(confirmed.c.case_id, func.min(confirmed.c.reached_at).label("reached_at")).group_by(
        confirmed.c.case_id
    ).subquery()
    handover = (
        select(CaseHandover.primary_case_id.label("case_id"), func.min(CaseHandover.completed_at).label("reached_at"))
        .where(CaseHandover.status == HandoverStatus.COMPLETED)
        .group_by(CaseHandover.primary_case_id).subquery()
    )
    closure = select(Case.id.label("case_id"), Case.closed_at.label("reached_at")).where(
        Case.closed_at.is_not(None)
    ).subquery()

    age = literal(now) - Case.created_at
    active_scope = and_(scope, Case.status.in_(ACTIVE))
    unresolved_age = {}
    for name, low, high in AGE_BUCKETS:
        condition = [active_scope, age >= low]
        if high is not None:
            condition.append(age < high)
        unresolved_age[name] = db.scalar(select(func.count()).select_from(Case).where(*condition)) or 0

    failed_notifications = db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.case_id.in_(period_ids), Notification.status == NotificationStatus.FAILED
        )
    ) or 0

    grievance_scope = []
    if Role(user.role) is not Role.ADMIN:
        grievance_scope.append(or_(Grievance.station_id == user.station_id, Grievance.assigned_to_user_id == user.id))
    open_grievance = Grievance.status.not_in(("RESOLVED", "CLOSED"))
    grievances_open = db.scalar(select(func.count()).select_from(Grievance).where(*grievance_scope, open_grievance)) or 0
    grievances_overdue = db.scalar(
        select(func.count()).select_from(Grievance).where(*grievance_scope, open_grievance, Grievance.due_at < now)
    ) or 0

    recent = db.execute(
        select(AuditEvent.action, AuditEvent.case_id, AuditEvent.case_number, AuditEvent.created_at)
        .where(AuditEvent.action.in_(list(CASE_EVENTS)), AuditEvent.case_id.in_(select(Case.id).where(scope)))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(20)
    ).all()

    return {
        "generated_at": now,
        "period_from": start,
        "period_to": end,
        "registered_total": db.scalar(select(func.count()).select_from(Case).where(in_period)) or 0,
        "by_station": grouped(Case.station_id),
        "by_case_type": grouped(Case.case_type),
        "by_urgency": grouped(Case.urgency_level),
        "by_status": grouped(Case.status),
        "by_sector": by_sector,
        "by_day": by_day,
        "median_seconds": {
            "to_assignment": _median_seconds(db, first_assignment, period_ids),
            "to_confirmed_match": _median_seconds(db, first_match, period_ids),
            "to_reunification_or_return": _median_seconds(db, handover, period_ids),
            "to_closure": _median_seconds(db, closure, period_ids),
        },
        "unresolved_age": unresolved_age,
        "resolved_in_period": db.scalar(
            select(func.count()).select_from(Case).where(
                in_period, Case.status.in_((CaseStatus.RESOLVED, CaseStatus.CLOSED))
            )
        ) or 0,
        "notifications_failed": failed_notifications,
        "grievances_open": grievances_open,
        "grievances_overdue": grievances_overdue,
        "recent_activity": [
            {"type": CASE_EVENTS[r.action], "case_id": r.case_id, "case_number": r.case_number, "occurred_at": r.created_at}
            for r in recent
        ],
    }
