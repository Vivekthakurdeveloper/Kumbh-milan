import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.schemas.common import Page, PageParams
from app.schemas.reporting import ReportJobCreate, ReportJobRead, ReportScheduleCreate, ReportScheduleRead
from app.services import mis_reports as mis_service
from app.services import reports as report_service
from app.storage import ObjectStorage, get_storage

StorageDep = Annotated[ObjectStorage, Depends(get_storage)]

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


@router.post("/jobs", response_model=ReportJobRead, status_code=202, responses=AUTHED,
             summary="Queue an MIS report (CSV or PDF). Poll the job, then download when COMPLETED.")
def create_job(payload: ReportJobCreate, principal: CurrentPrincipal, db: DbSession):
    return mis_service.request_report(db, principal.user, **payload.model_dump())


@router.get("/jobs", response_model=Page[ReportJobRead], responses=AUTHED)
def list_jobs(principal: CurrentPrincipal, db: DbSession, page: Annotated[PageParams, Depends()]):
    rows, total = mis_service.list_jobs(db, principal.user, limit=page.limit, offset=page.offset)
    return Page(items=[ReportJobRead.model_validate(r) for r in rows], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/jobs/{job_id}", response_model=ReportJobRead, responses={**AUTHED, **errors(404)})
def get_job(job_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return mis_service.get_job(db, principal.user, job_id)


@router.get("/jobs/{job_id}/download", responses={**AUTHED, **errors(404, 409, 410)},
            summary="Download a completed report (audited; expires with the job)")
def download_job(job_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, storage: StorageDep) -> Response:
    body, filename, content_type = mis_service.download(db, storage, principal.user, job_id)
    return Response(content=body, media_type=content_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@router.post("/jobs/{job_id}/cancel", response_model=ReportJobRead, responses={**AUTHED, **errors(404, 409)},
             summary="Cancel a queued report or revoke a completed one (deletes the file)")
def cancel_job(job_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, storage: StorageDep):
    return mis_service.cancel_job(db, storage, principal.user, job_id)


@router.post("/schedules", response_model=ReportScheduleRead, status_code=201, responses=AUTHED,
             summary="Schedule a daily or weekly report (administrators)")
def create_schedule(payload: ReportScheduleCreate, principal: CurrentPrincipal, db: DbSession):
    return mis_service.create_schedule(db, principal.user, **payload.model_dump())


@router.get("/schedules", response_model=list[ReportScheduleRead], responses=AUTHED)
def list_schedules(principal: CurrentPrincipal, db: DbSession):
    return mis_service.list_schedules(db, principal.user)


@router.post("/schedules/{schedule_id}/{action}", response_model=ReportScheduleRead,
             responses={**AUTHED, **errors(404)})
def toggle_schedule(
    schedule_id: uuid.UUID, action: Literal["activate", "deactivate"], principal: CurrentPrincipal, db: DbSession
):
    return mis_service.set_schedule_active(db, principal.user, schedule_id, action == "activate")
