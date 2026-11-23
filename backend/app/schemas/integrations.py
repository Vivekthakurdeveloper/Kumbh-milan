import uuid
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, Field, field_validator

from app.models.enums import (
    AlertStatus,
    DispatchOperation,
    DispatchStatus,
    InboundMessageStatus,
    IntegrationAdapter,
    IntegrationHealth,
    IntegrationKind,
    UrgencyLevel,
)
from app.schemas.common import ReadModel, Reason, StrictModel

Code = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")


class DestinationCreate(StrictModel):
    code: str = Code
    name: str = Field(min_length=1, max_length=200)
    kind: IntegrationKind
    system: str = Field(min_length=1, max_length=64, description="Vendor/system, e.g. CCTNS, ERSS_112, PA_GENERIC")
    adapter: IntegrationAdapter = IntegrationAdapter.MOCK
    endpoint_url: str | None = Field(None, max_length=500)
    secret: str | None = Field(None, min_length=16, max_length=500, description="Write-only signing secret")
    config: dict[str, Any] = Field(default_factory=dict, description="Zones, display groups, account ids")
    is_enabled: bool = False
    timeout_seconds: int = Field(10, ge=1, le=60)
    max_attempts: int = Field(5, ge=1, le=20)

    _endpoint = field_validator("endpoint_url")(lambda v: _http_url(v))


def _http_url(value: str | None) -> str | None:
    if value is not None and not value.startswith(("https://", "http://")):
        raise ValueError("must be an http(s) URL")
    return value


class DestinationUpdate(StrictModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    adapter: IntegrationAdapter | None = None
    endpoint_url: str | None = Field(None, max_length=500)
    secret: str | None = Field(None, min_length=16, max_length=500)
    config: dict[str, Any] | None = None
    is_enabled: bool | None = None
    timeout_seconds: int | None = Field(None, ge=1, le=60)
    max_attempts: int | None = Field(None, ge=1, le=20)

    _endpoint = field_validator("endpoint_url")(lambda v: _http_url(v))


class DestinationRead(ReadModel):
    id: uuid.UUID
    code: str
    name: str
    kind: IntegrationKind
    system: str
    adapter: IntegrationAdapter
    endpoint_url: str | None
    config: dict[str, Any]
    is_enabled: bool
    timeout_seconds: int
    max_attempts: int
    health: IntegrationHealth
    consecutive_failures: int
    circuit_open_until: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    has_secret: bool = False


class AlertCreate(StrictModel):
    case_id: uuid.UUID | None = None
    title_en: str = Field(min_length=1, max_length=200)
    title_hi: str = Field(min_length=1, max_length=200)
    body_en: str = Field(min_length=1, max_length=1000)
    body_hi: str = Field(min_length=1, max_length=1000)
    priority: UrgencyLevel = UrgencyLevel.MEDIUM
    destination_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    consent_reference: str | None = Field(None, min_length=3, max_length=200)
    starts_at: AwareDatetime
    expires_at: AwareDatetime


class AlertUpdate(StrictModel):
    title_en: str | None = Field(None, min_length=1, max_length=200)
    title_hi: str | None = Field(None, min_length=1, max_length=200)
    body_en: str | None = Field(None, min_length=1, max_length=1000)
    body_hi: str | None = Field(None, min_length=1, max_length=1000)
    priority: UrgencyLevel | None = None
    destination_ids: list[uuid.UUID] | None = Field(None, min_length=1, max_length=50)
    consent_reference: str | None = Field(None, min_length=3, max_length=200)
    starts_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None


class AlertWithdraw(StrictModel):
    reason: Reason


class AlertRead(ReadModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    title_en: str
    title_hi: str
    body_en: str
    body_hi: str
    priority: UrgencyLevel
    status: AlertStatus
    destination_ids: list[uuid.UUID]
    consent_reference: str | None
    starts_at: datetime
    expires_at: datetime
    created_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    published_at: datetime | None
    withdrawn_at: datetime | None
    withdraw_reason: str | None
    created_at: datetime
    version: int


class DispatchRead(ReadModel):
    id: uuid.UUID
    destination_id: uuid.UUID
    alert_id: uuid.UUID | None
    case_id: uuid.UUID | None
    operation: DispatchOperation
    status: DispatchStatus
    attempts: int
    next_attempt_at: datetime | None
    remote_reference: str | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime


class CaseExportRequest(StrictModel):
    case_id: uuid.UUID
    legal_basis: str = Field(min_length=10, max_length=500, description="Statutory basis or order reference")


class InboundRead(ReadModel):
    id: uuid.UUID
    destination_id: uuid.UUID
    external_id: str
    message_type: str
    payload: dict[str, Any]
    status: InboundMessageStatus
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime


class InboundReview(StrictModel):
    accept: bool
    note: str | None = Field(None, max_length=2000)
