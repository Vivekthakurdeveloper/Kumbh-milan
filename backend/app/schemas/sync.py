import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from app.models.enums import CaseType
from app.schemas.common import PastDatetime, ReadModel, StrictModel

DeviceId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
ClientRecordId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
OptionalClientId = Annotated[str | None, StringConstraints(min_length=1, max_length=128)]


class SyncOperation(StrEnum):
    CREATE_CASE = "CREATE_CASE"
    UPDATE_CASE = "UPDATE_CASE"
    ADD_NOTE = "ADD_NOTE"


class SyncRecordOutcome(StrEnum):
    SYNCED = "SYNCED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"


class SyncPushRecord(StrictModel):
    client_record_id: ClientRecordId
    operation: SyncOperation
    client_created_at: PastDatetime
    data: dict[str, Any] = Field(default_factory=dict)
    case_type: CaseType | None = Field(None, description="Required for CREATE_CASE")
    case_id: uuid.UUID | None = Field(None, description="Server case id for UPDATE_CASE and ADD_NOTE")
    case_client_record_id: ClientRecordId | None = Field(
        None, description="Offline case id when the server case id is not yet known"
    )
    base_version: int | None = Field(None, ge=1, description="Required for UPDATE_CASE; mismatch returns CONFLICT")

    @model_validator(mode="after")
    def _operation_fields(self):
        if self.operation is SyncOperation.CREATE_CASE:
            if self.case_type is None:
                raise ValueError("case_type is required for CREATE_CASE")
            if self.case_id is not None or self.case_client_record_id is not None:
                raise ValueError("case_id and case_client_record_id are not used for CREATE_CASE")
            if self.base_version is not None:
                raise ValueError("base_version is not used for CREATE_CASE")
        elif self.operation is SyncOperation.UPDATE_CASE:
            if self.base_version is None:
                raise ValueError("base_version is required for UPDATE_CASE")
            if self.case_id is None and self.case_client_record_id is None:
                raise ValueError("case_id or case_client_record_id is required for UPDATE_CASE")
            if self.case_type is not None:
                raise ValueError("case_type is not used for UPDATE_CASE")
        else:
            if self.case_id is None and self.case_client_record_id is None:
                raise ValueError("case_id or case_client_record_id is required for ADD_NOTE")
            if self.case_type is not None:
                raise ValueError("case_type is not used for ADD_NOTE")
            if self.base_version is not None:
                raise ValueError("base_version is not used for ADD_NOTE")
        return self


class SyncPushRequest(StrictModel):
    device_id: DeviceId
    client_id: OptionalClientId = None
    pending_count: int | None = Field(None, ge=0, description="Client-reported backlog; stored as reported")
    failed_count: int | None = Field(None, ge=0, description="Client-reported failures; stored as reported")
    records: list[SyncPushRecord] = Field(default_factory=list, max_length=100)


class SyncRecordResult(StrictModel):
    client_record_id: str
    operation: SyncOperation
    outcome: SyncRecordOutcome
    case_id: uuid.UUID | None = None
    case_number: str | None = None
    note_id: uuid.UUID | None = None
    version: int | None = None
    server_updated_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_details: Any = None
    case: dict[str, Any] | None = Field(
        None, description="Current server case on CONFLICT so the client can resolve without a separate fetch"
    )


class SyncPushResponse(StrictModel):
    device_id: str
    results: list[SyncRecordResult]
    synced: int
    duplicates: int
    conflicts: int
    failed: int
    server_time: datetime


class SyncPullItem(StrictModel):
    case: dict[str, Any]
    server_updated_at: datetime
    version: int


class SyncPullResponse(StrictModel):
    items: list[SyncPullItem]
    next_cursor: str | None
    has_more: bool
    server_time: datetime


class SyncStatusResponse(ReadModel):
    device_id: str
    last_successful_sync_at: datetime | None = Field(
        None, description="Latest of last successful push and pull for this device"
    )
    last_successful_push_at: datetime | None = None
    last_successful_pull_at: datetime | None = None
    pending_count: int | None = Field(None, description="Last value reported by the client; null if never reported")
    failed_count: int | None = Field(None, description="Last value reported by the client; null if never reported")
    server_cursor: str | None = Field(None, description="Current sync cursor for the caller's permitted cases")
    server_time: datetime
