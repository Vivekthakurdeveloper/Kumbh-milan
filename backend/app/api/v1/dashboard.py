from typing import Annotated

from fastapi import APIRouter, Query

from app.api.v1.responses import AUTHED
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

PeriodFrom = Annotated[
    str | None,
    Query(
        alias="from", max_length=40, examples=["2026-11-06", "2026-11-06T00:00:00+05:30"],
        description=(
            "Start of the reporting window (inclusive). ISO 8601 date (whole Asia/Kolkata day) or datetime; "
            "a datetime without an offset is Asia/Kolkata time."
        ),
    ),
]
PeriodTo = Annotated[
    str | None,
    Query(
        alias="to", max_length=40, examples=["2026-11-06", "2026-11-06T18:00:00+05:30"],
        description="End of the reporting window (inclusive). A date means the end of that Asia/Kolkata day.",
    ),
]


@router.get("/summary", response_model=DashboardSummary, responses=AUTHED, summary="Counts for the console home screen")
def summary(principal: CurrentPrincipal, db: DbSession, from_: PeriodFrom = None, to: PeriodTo = None):
    """
    Pass `from` and `to` for the Today / 7 days / 30 days selector: urgent_active, unassigned_active,
    registered_today and pending_match_reviews then count only cases registered (and matches proposed) inside the
    window. Today is `from=<today>&to=<today>`; 7 days is `from=<today-6>&to=<today>`. Either bound may be
    omitted; the window may not exceed 366 days. Without either, the original all-time behaviour is unchanged.
    """
    return dashboard_service.summary(db, principal.user, from_=from_, to=to)
