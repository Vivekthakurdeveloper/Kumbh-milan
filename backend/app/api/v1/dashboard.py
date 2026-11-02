from fastapi import APIRouter

from app.api.v1.responses import AUTHED
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary, responses=AUTHED, summary="Counts for the console home screen")
def summary(principal: CurrentPrincipal, db: DbSession):
    return dashboard_service.summary(db, principal.user)
