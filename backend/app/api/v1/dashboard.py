from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.common.errors import ValidationFailed
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard as dashboard_service
from app.services.cases import CASE_NUMBER_TZ

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

_BOUND_HELP = (
    "Inclusive Asia/Kolkata calendar day. Accepts a date (2026-11-06) or a date-time with offset "
    "(2026-11-06T00:00:00+05:30); a date-time is converted to its Asia/Kolkata calendar day."
)


def _kolkata_day(name: str, value: str | None) -> date | None:
    """Read a range bound as an Asia/Kolkata calendar day. Naive date-times are taken as Kolkata time."""
    if value is None:
        return None
    text = value.strip().replace(" ", "+")  # a raw '+' in a query string arrives as a space
    try:
        if "T" not in text:
            return date.fromisoformat(text)
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValidationFailed(
            f"{name} must be a date (YYYY-MM-DD) or an ISO 8601 date-time",
            code="invalid_range",
            details={name: value},
        ) from None
    if moment.tzinfo is not None:
        moment = moment.astimezone(CASE_NUMBER_TZ)
    return moment.date()


@router.get(
    "/summary",
    response_model=DashboardSummary,
    responses={**AUTHED, **errors(422)},
    summary="Counts for the console home screen",
)
def summary(
    principal: CurrentPrincipal,
    db: DbSession,
    date_from: Annotated[str | None, Query(alias="from", description=f"Range start. {_BOUND_HELP}")] = None,
    date_to: Annotated[str | None, Query(alias="to", description=f"Range end. {_BOUND_HELP}")] = None,
):
    """
    Without `from`/`to`, behaviour is unchanged: active/status counts are point-in-time and
    `registered_today` means since Asia/Kolkata midnight.

    With both parameters, case-created bounds filter the dashboard metrics (end day inclusive).
    The desk sends Kolkata bounds such as `from=2026-11-06T00:00:00+05:30&to=2026-11-06T23:59:59+05:30`
    for Today; 7 days and 30 days use the same end with a start 6 or 29 days earlier.
    `generated_at` is always the time of the response.
    """
    return dashboard_service.summary(
        db,
        principal.user,
        date_from=_kolkata_day("from", date_from),
        date_to=_kolkata_day("to", date_to),
    )
