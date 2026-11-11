import uuid
from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.models.enums import (
    AgeGroup,
    Gender,
    OtpPurpose,
    PublicInformationKind,
    PublicReviewStatus,
    PushPlatform,
    SightingStatus,
)
from app.schemas.common import LocationText, LongText, PastDatetime, ReadModel, ShortText, StrictModel


class OtpRequest(StrictModel):
    mobile_number: str = Field(min_length=8, max_length=20)
    purpose: OtpPurpose
    locale: Literal["en", "hi"] = "hi"


class OtpVerify(StrictModel):
    mobile_number: str = Field(min_length=8, max_length=20)
    purpose: OtpPurpose
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


class StatusOtpRequest(StrictModel):
    case_id: str = Field(min_length=3, max_length=32, description="Human case reference number")
    mobile_number: str = Field(min_length=8, max_length=20)
    locale: Literal["en", "hi"] = "hi"


class StatusOtpVerify(StrictModel):
    case_id: str = Field(min_length=3, max_length=32)
    mobile_number: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


class OtpRequestResponse(StrictModel):
    status: Literal["OTP_SENT"] = "OTP_SENT"
    message: str
    expires_in_seconds: int


class VerifiedMobileResponse(StrictModel):
    verified_mobile_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    purpose: OtpPurpose


class CaseStatusTokenResponse(StrictModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    case_id: str
    scope: Literal["status:read"] = "status:read"


class PublicMissingPersonCreate(StrictModel):
    person_name: ShortText
    approximate_age: int = Field(ge=0, le=120)
    gender: Gender
    last_seen_location: LocationText
    last_seen_at: PastDatetime
    clothing_description: LongText
    reporter_name: ShortText
    reporter_mobile_number: str = Field(min_length=8, max_length=20)
    additional_description: LongText | None = None
    age_group: AgeGroup | None = None
    reporter_email: EmailStr | None = None
    preferred_locale: Literal["en", "hi"] = "hi"
    notification_consent: bool = True
    notification_consent_statement: str = Field(
        default="Reporter consented to case status notifications during public submission",
        min_length=10,
        max_length=500,
    )


class PublicSubmissionAck(StrictModel):
    case_id: str
    status: PublicReviewStatus
    message: str


class PublicCaseStatusRead(StrictModel):
    case_id: str
    status: str
    updated_at: datetime
    message: str


class PublicSightingCreate(StrictModel):
    case_id: str | None = Field(None, max_length=32, description="Optional related case reference number")
    sighting_location: LocationText
    sighting_at: PastDatetime
    description: LongText
    reporter_mobile_number: str = Field(min_length=8, max_length=20)


class PublicSightingAck(StrictModel):
    sighting_id: uuid.UUID
    status: SightingStatus
    message: str


class SightingRead(ReadModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    case_number_submitted: str | None
    station_id: uuid.UUID
    sighting_location: str
    sighting_at: datetime
    description: str
    reporter_mobile_hint: str
    status: SightingStatus
    created_at: datetime
    updated_at: datetime
    has_photo: bool = False


class PublicReviewRequest(StrictModel):
    decision: Literal["VERIFIED", "REJECTED"]
    reason: str = Field(min_length=3, max_length=2000)


class PushTokenRegister(StrictModel):
    platform: PushPlatform
    token: str = Field(min_length=20, max_length=4096)
    case_id: str | None = Field(
        None, max_length=32, description="Case reference when registering from a citizen status session"
    )


class PushTokenDelete(StrictModel):
    token: str = Field(min_length=20, max_length=4096)


class PublicInformationCreate(StrictModel):
    kind: PublicInformationKind
    title_en: ShortText
    title_hi: ShortText
    body_en: LongText
    body_hi: LongText
    contact_phone: str | None = Field(None, max_length=32)
    contact_email: EmailStr | None = None
    location_text: LocationText | None = None
    sort_order: int = Field(0, ge=0, le=10_000)
    publish: bool = False


class PublicInformationUpdate(StrictModel):
    title_en: ShortText | None = None
    title_hi: ShortText | None = None
    body_en: LongText | None = None
    body_hi: LongText | None = None
    contact_phone: str | None = Field(None, max_length=32)
    contact_email: EmailStr | None = None
    location_text: LocationText | None = None
    sort_order: int | None = Field(None, ge=0, le=10_000)


class PublicInformationRead(ReadModel):
    id: uuid.UUID
    kind: PublicInformationKind
    title_en: str
    title_hi: str
    body_en: str
    body_hi: str
    contact_phone: str | None
    contact_email: str | None
    location_text: str | None
    sort_order: int
    is_published: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicInformationList(StrictModel):
    items: list[PublicInformationRead]
