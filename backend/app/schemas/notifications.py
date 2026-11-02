import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.enums import NotificationChannel, NotificationStatus, NotificationTemplate
from app.schemas.common import ReadModel, StrictModel


class NotificationCreate(StrictModel):
    template: NotificationTemplate
    channel: NotificationChannel = NotificationChannel.SMS
    locale: Literal["en", "hi"] = "hi"
    recipient_phone: str = Field(min_length=8, max_length=20, description="Mobile number; +91 is assumed when no country code is given")
    consent_statement: str = Field(
        min_length=10, max_length=500,
        description="Who agreed to receive messages and how, e.g. 'Father agreed verbally at desk 3'",
        examples=["Father Ramesh agreed verbally at Har Ki Pauri desk"],
    )


class NotificationRead(ReadModel):
    id: uuid.UUID
    case_id: uuid.UUID
    template: NotificationTemplate
    channel: NotificationChannel
    locale: str
    recipient_hint: str | None = Field(description="Masked recipient; the full number is never returned")
    status: NotificationStatus
    attempts: int
    next_attempt_at: datetime | None
    last_error: str | None
    provider: str | None
    sent_at: datetime | None
    requested_by_user_id: uuid.UUID
    consent_statement: str
    created_at: datetime
