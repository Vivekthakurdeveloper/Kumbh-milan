import uuid
from datetime import datetime

from pydantic import AwareDatetime, Field

from app.models.enums import ReportCadence, ReportJobStatus, ReportType
from app.schemas.common import ReadModel, StrictModel


class ReportJobCreate(StrictModel):
    report_type: ReportType
    period_from: AwareDatetime
    period_to: AwareDatetime
    station_id: uuid.UUID | None = Field(None, description="Administrators may narrow to one station")


class ReportJobRead(ReadModel):
    id: uuid.UUID
    report_type: ReportType
    status: ReportJobStatus
    period_from: datetime
    period_to: datetime
    station_id: uuid.UUID | None
    requested_by_user_id: uuid.UUID
    schedule_id: uuid.UUID | None
    row_count: int | None
    size_bytes: int | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None


class ReportScheduleCreate(StrictModel):
    report_type: ReportType
    cadence: ReportCadence
    station_id: uuid.UUID | None = None
    recipient_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class ReportScheduleRead(ReadModel):
    id: uuid.UUID
    report_type: ReportType
    cadence: ReportCadence
    station_id: uuid.UUID | None
    recipient_user_ids: list[uuid.UUID]
    next_run_at: datetime
    is_active: bool
    created_by_user_id: uuid.UUID
    created_at: datetime
