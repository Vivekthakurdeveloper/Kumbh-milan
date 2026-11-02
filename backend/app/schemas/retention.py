import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import RetentionPolicy, RetentionRunStatus
from app.schemas.common import ReadModel, Reason, StrictModel


class LegalHoldCreate(StrictModel):
    reason: Reason
    reference: str | None = Field(None, max_length=200, description="Court order, FIR, or request reference")


class LegalHoldRelease(StrictModel):
    reason: Reason


class LegalHoldRead(ReadModel):
    id: uuid.UUID
    case_id: uuid.UUID
    reason: str
    reference: str | None
    placed_by_user_id: uuid.UUID
    created_at: datetime
    released_by_user_id: uuid.UUID | None
    released_at: datetime | None
    release_reason: str | None


class RetentionPolicyRead(ReadModel):
    policy: str
    data: str
    after_days: int | None
    action: str


class RetentionRunCreate(StrictModel):
    policy: RetentionPolicy
    note: str | None = Field(None, max_length=2000)


class RetentionRunCancel(StrictModel):
    reason: Reason


class RetentionPlanReport(ReadModel):
    cutoff: datetime
    eligible: int
    excluded_by_legal_hold: int
    truncated: bool = Field(description="More items were eligible than one run may process; plan another run afterwards")
    sample_case_numbers: list[str]


class RetentionRunRead(ReadModel):
    id: uuid.UUID
    policy: RetentionPolicy
    status: RetentionRunStatus
    parameters: dict[str, Any]
    plan_report: RetentionPlanReport
    execution_report: dict[str, Any] | None
    planned_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    executed_by_user_id: uuid.UUID | None
    execution_started_at: datetime | None
    executed_at: datetime | None
    cancelled_by_user_id: uuid.UUID | None
    cancelled_at: datetime | None
    note: str | None
    created_at: datetime
