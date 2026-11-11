from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummary,
    responses={**AUTHED, **errors(422)},
    summary="Counts for the console home screen",
)
def summary(
    principal: CurrentPrincipal,
    db: DbSession,
    date_from: Annotated[
        date | None,
        Query(alias="from", description="Inclusive Asia/Kolkata calendar start date (YYYY-MM-DD)"),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(alias="to", description="Inclusive Asia/Kolkata calendar end date (YYYY-MM-DD)"),
    ] = None,
):
    """
    Without `from`/`to`, behaviour is unchanged: active/status counts are point-in-time and
    `registered_today` means since Asia/Kolkata midnight.

    With both parameters, case-created bounds filter the dashboard metrics (end date inclusive).
    """
    return dashboard_service.summary(db, principal.user, date_from=date_from, date_to=date_to)
