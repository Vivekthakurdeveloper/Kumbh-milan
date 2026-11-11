"""Row-level case visibility. Every case lookup goes through these clauses."""

from sqlalchemy import ColumnElement, and_, false, or_, select, true, union
from sqlalchemy.orm import aliased

from app.models import Case, CaseMatch, User
from app.models.enums import MatchSource, MatchStatus, Role

# Confirmed matches, and proposals a person deliberately made, open the counterpart case to the other
# station's police. Automatic suggestions do not: an algorithm alone never widens access to case data.
VISIBILITY_GRANTING_MATCH = or_(
    CaseMatch.status == MatchStatus.CONFIRMED,
    and_(CaseMatch.status == MatchStatus.PROPOSED, CaseMatch.source == MatchSource.MANUAL),
)


def _match_linked_case_ids(station_id):
    """Cases linked to a case registered at the given station by a visibility-granting match."""
    own = aliased(Case)
    right_side = (
        select(CaseMatch.right_case_id.label("case_id"))
        .join(own, own.id == CaseMatch.left_case_id)
        .where(own.station_id == station_id, VISIBILITY_GRANTING_MATCH)
    )
    left_side = (
        select(CaseMatch.left_case_id.label("case_id"))
        .join(own, own.id == CaseMatch.right_case_id)
        .where(own.station_id == station_id, VISIBILITY_GRANTING_MATCH)
    )
    return union(right_side, left_side)


def case_visibility_clause(user: User) -> ColumnElement[bool]:
    """
    ADMIN: every case.
    POLICE_OFFICER: own station, assigned to them, reported by them, or linked by a match to an own-station case.
    HELP_DESK_OPERATOR: own station only.
    PUBLIC: only cases the citizen themselves reported (prevents IDOR across accounts).
    """
    role = Role(user.role)
    if role is Role.ADMIN:
        return true()
    if role is Role.PUBLIC:
        return and_(Case.reported_by_user_id == user.id, Case.archived_at.is_(None))
    if user.station_id is None:
        return false()
    if role is Role.HELP_DESK_OPERATOR:
        return and_(Case.station_id == user.station_id, Case.archived_at.is_(None))
    return or_(
        Case.station_id == user.station_id,
        Case.assigned_to_user_id == user.id,
        Case.reported_by_user_id == user.id,
        Case.id.in_(select(_match_linked_case_ids(user.station_id).subquery().c.case_id)),
    )
