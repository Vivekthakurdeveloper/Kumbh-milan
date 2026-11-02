from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.v1.responses import AUTHED
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.services import reports as report_service

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get(
    "/cases.csv",
    responses=AUTHED,
    summary="Export visible case metadata for a date range (maximum 31 days and 1000 rows)",
)
def export_cases(
    principal: CurrentPrincipal,
    db: DbSession,
    created_from: datetime,
    created_to: datetime,
) -> Response:
    body, count, truncated = report_service.export_cases(db, principal.user, created_from, created_to)
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=\"cases.csv\"",
            "X-Export-Rows": str(count),
            "X-Export-Truncated": "true" if truncated else "false",
        },
    )
