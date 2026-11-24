import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import GrievanceCategory, GrievanceEventKind, GrievanceStatus, MediaType, ScanStatus, UrgencyLevel
from app.schemas.common import LongText, Reason, ShortText, StrictModel


class GrievanceCreate(StrictModel):
    category: GrievanceCategory
    subject: ShortText
    description: LongText
    case_number: str | None = Field(None, min_length=3, max_length=32, description="Related report, if any")
    locale: Literal["en", "hi"] = "hi"


class GrievanceAck(BaseModel):
    reference: str
    status: GrievanceStatus
    message: str


class GrievancePublicEvent(BaseModel):
    kind: GrievanceEventKind
    to_status: GrievanceStatus | None
    message: str | None
    by_citizen: bool
    created_at: datetime


class GrievancePublicAttachment(BaseModel):
    """A file the citizen uploaded. Staff evidence is never listed to the citizen."""

    id: uuid.UUID
    media_type: MediaType
    file_size_bytes: int
    scan_status: ScanStatus
    created_at: datetime


class GrievanceAttachmentRead(GrievancePublicAttachment):
    original_filename: str | None
    content_type: str
    checksum_sha256: str
    uploaded_by_user_id: uuid.UUID | None
    by_citizen: bool
    scanned_at: datetime | None
    downloadable: bool = Field(False, description="True once the file passed malware scanning")


class GrievancePublicRead(BaseModel):
    reference: str
    category: GrievanceCategory
    subject: str
    status: GrievanceStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    events: list[GrievancePublicEvent] = []
    attachments: list[GrievancePublicAttachment] = []


class GrievanceCitizenComment(StrictModel):
    message: LongText


class GrievanceCitizenReopen(StrictModel):
    reason: Reason


class GrievanceRead(BaseModel):
    id: uuid.UUID
    reference: str
    category: GrievanceCategory
    status: GrievanceStatus
    priority: UrgencyLevel
    station_id: uuid.UUID
    case_id: uuid.UUID | None
    subject: str
    assigned_to_user_id: uuid.UUID | None
    mobile_hint: str | None
    due_at: datetime
    overdue: bool
    escalation_level: int
    escalated_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class GrievanceEventRead(BaseModel):
    id: uuid.UUID
    kind: GrievanceEventKind
    from_status: GrievanceStatus | None
    to_status: GrievanceStatus | None
    actor_user_id: uuid.UUID | None
    assignee_user_id: uuid.UUID | None
    message: str | None
    is_internal: bool
    by_citizen: bool
    created_at: datetime


class GrievanceDetail(GrievanceRead):
    description: str
    resolution: str | None
    anonymized_at: datetime | None
    legal_hold_active: bool
    events: list[GrievanceEventRead]
    attachments: list[GrievanceAttachmentRead]


class GrievanceAssign(StrictModel):
    assignee_user_id: uuid.UUID
    reason: Reason | None = None
    expected_version: int | None = Field(None, ge=1)


class GrievanceStatusChange(StrictModel):
    status: GrievanceStatus
    message: str | None = Field(None, min_length=3, max_length=5000, description="Required when resolving")
    expected_version: int | None = Field(None, ge=1)


class GrievanceStaffComment(StrictModel):
    message: LongText
    internal: bool = Field(True, description="Internal notes are never shown to the citizen")


class GrievanceSummary(BaseModel):
    generated_at: datetime
    by_status: dict[str, int]
    by_category: dict[str, int]
    open_total: int
    overdue: int
    escalated_open: int
    sla_hours: int
