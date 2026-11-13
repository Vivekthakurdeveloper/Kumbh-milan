import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.models.enums import (
    AgeGroup,
    CaseSource,
    CaseStatus,
    CaseType,
    CommunicationStatus,
    CoordinateSource,
    CustodyStatus,
    Gender,
    PublicReviewStatus,
    UrgencyLevel,
)
from app.schemas.common import (
    LocationText,
    LongText,
    PastDatetime,
    ReadModel,
    ShortText,
    StrictModel,
)

# Matches VARCHAR(100) on missing_person_details.language / found_person_details.language.
LanguageText = Annotated[str, StringConstraints(min_length=1, max_length=100)]

ContactText = Field(
    None,
    min_length=3,
    max_length=1000,
    description="Owner or family contact details. Write-only: encrypted at rest and never returned by "
    "case endpoints. Police and admins read it through POST /cases/{id}/contact/reveal, which is audited.",
)


# --- Shared envelope -------------------------------------------------------------------------------


class CoordinateInput(StrictModel):
    """
    Position of the event. Either reference a known location, send coordinates captured by a device or a
    map pin, or both. Coordinates are never derived from free-text descriptions.
    """

    location_id: uuid.UUID | None = Field(None, description="Known ghat, sector, or landmark (GET /locations)")
    latitude: float | None = Field(None, ge=-90, le=90, description="WGS 84 decimal degrees")
    longitude: float | None = Field(None, ge=-180, le=180, description="WGS 84 decimal degrees")
    coordinate_source: CoordinateSource | None = Field(None, description="Required when coordinates are sent")
    coordinate_accuracy_m: float | None = Field(None, ge=0, le=100000, description="Reported GPS accuracy in metres")

    @model_validator(mode="after")
    def _coordinates_consistent(self):
        sent = self.model_fields_set
        lat, lng = self.latitude, self.longitude
        if ("latitude" in sent or "longitude" in sent) and (lat is None) != (lng is None):
            raise ValueError("latitude and longitude must be sent together")
        if lat is not None and self.coordinate_source is None:
            raise ValueError("coordinate_source is required with coordinates")
        if lat is None and "latitude" in sent and self.coordinate_source is not None:
            raise ValueError("coordinate_source must be null when clearing coordinates")
        if self.coordinate_accuracy_m is not None and lat is None:
            raise ValueError("coordinate_accuracy_m requires coordinates")
        return self


class CaseCreateCommon(CoordinateInput):
    urgency_level: UrgencyLevel | None = None
    station_id: uuid.UUID | None = Field(
        None, description="Admins only. Officers and help-desk operators always register at their own station."
    )
    contact_details: str | None = ContactText
    client_id: Annotated[str | None, StringConstraints(min_length=1, max_length=128)] = Field(
        None, description="Optional app-instance id for offline sync"
    )
    device_id: Annotated[str | None, StringConstraints(min_length=1, max_length=128)] = Field(
        None, description="Required with client_record_id; identifies the device that created the record offline"
    )
    client_record_id: Annotated[str | None, StringConstraints(min_length=1, max_length=128)] = Field(
        None, description="Stable client-generated id; retries with the same id return the original case"
    )
    client_created_at: PastDatetime | None = Field(
        None, description="When the record was created on the device (UTC)"
    )

    @model_validator(mode="after")
    def _sync_fields_consistent(self):
        if self.client_record_id is not None and self.device_id is None:
            raise ValueError("device_id is required when client_record_id is sent")
        return self


class CaseUpdateCommon(CoordinateInput):
    urgency_level: UrgencyLevel | None = None
    contact_details: str | None = ContactText
    expected_version: int | None = Field(
        None, ge=1, description="Case version the client last saw; a mismatch returns 409 case_changed"
    )


class CaseRead(ReadModel):
    id: uuid.UUID
    case_number: str
    case_type: CaseType
    status: CaseStatus
    source: CaseSource = CaseSource.STAFF
    public_review_status: PublicReviewStatus | None = None
    urgency_level: UrgencyLevel | None
    station_id: uuid.UUID
    reported_by_user_id: uuid.UUID
    assigned_to_user_id: uuid.UUID | None
    has_contact_details: bool = Field(description="True when contact details exist for authorised retrieval")
    version: int = Field(description="Incremented on every change; send as expected_version to avoid lost updates")
    location_id: uuid.UUID | None = None
    latitude: float | None = None
    longitude: float | None = None
    coordinate_source: CoordinateSource | None = None
    coordinate_accuracy_m: float | None = None
    in_scope: bool = Field(
        description="True when the case belongs to the caller's operational station scope (or is owned by a PUBLIC reporter)",
    )
    verification_evidence: str | None = Field(
        None,
        description="Latest handover/reunification verification comment from status history, when the caller may see it",
    )
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    archived_at: datetime | None = Field(None, description="Set by retention; archived cases are read-only and hidden by default")
    anonymized_at: datetime | None = Field(None, description="Personal details were removed by an approved retention run")
    in_scope: bool = Field(
        description=(
            "Same meaning as on the case list: false marks a case the caller may see only as a read-only summary. "
            "A case outside the caller's scope is never returned by this endpoint (it is a 404), so a 200 always "
            "carries true; clients should still key read-only behaviour off this field."
        )
    )


class CaseSummary(ReadModel):
    """Search result row. Carries no contact details and no notes."""

    id: uuid.UUID
    case_number: str
    case_type: CaseType
    status: CaseStatus
    urgency_level: UrgencyLevel | None
    station_id: uuid.UUID
    reported_by_user_id: uuid.UUID
    assigned_to_user_id: uuid.UUID | None
    title: str | None
    location: str | None
    location_id: uuid.UUID | None = None
    approximate_age: int | None = None
    age_group: AgeGroup | None = None
    gender: Gender | None = None
    event_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    in_scope: bool = Field(description="False for cross-station results the caller can see only as a summary")


# --- Missing person --------------------------------------------------------------------------------


class MissingPersonFields(StrictModel):
    person_name: ShortText | None = Field(None, description="Leave empty when unknown")
    approximate_age: int | None = Field(None, ge=0, le=120)
    age_group: AgeGroup | None = None
    gender: Gender | None = None
    height_description: ShortText | None = None
    clothing_description: LongText | None = None
    identifying_marks: LongText | None = None
    language: LanguageText | None = None
    last_seen_location: LocationText | None = None
    last_seen_at: PastDatetime | None = None
    has_disability: bool | None = None
    medical_needs: LongText | None = None
    additional_description: LongText | None = None


class MissingPersonCreate(CaseCreateCommon, MissingPersonFields):
    pass


class MissingPersonUpdate(CaseUpdateCommon, MissingPersonFields):
    pass


class MissingPersonDetailsRead(ReadModel, MissingPersonFields):
    last_seen_at: AwareDatetime | None = None


class MissingPersonCaseRead(CaseRead):
    details: MissingPersonDetailsRead


# --- Found person ----------------------------------------------------------------------------------


class FoundPersonFields(StrictModel):
    approximate_age: int | None = Field(None, ge=0, le=120)
    age_group: AgeGroup | None = None
    estimated_gender: Gender | None = None
    physical_description: LongText | None = None
    clothing_description: LongText | None = None
    identifying_marks: LongText | None = None
    found_location: LocationText | None = None
    found_at: PastDatetime | None = None
    communication_status: CommunicationStatus | None = None
    language: LanguageText | None = None
    has_disability: bool | None = None
    care_or_assistance_required: LongText | None = None
    current_care_location: LocationText | None = None
    safeguarding_review_required: bool | None = Field(
        None,
        description="Operator flag that reunification needs recorded verification. Not a legal determination.",
    )


class FoundPersonCreate(CaseCreateCommon, FoundPersonFields):
    pass


class FoundPersonUpdate(CaseUpdateCommon, FoundPersonFields):
    pass


class FoundPersonDetailsRead(ReadModel, FoundPersonFields):
    found_at: AwareDatetime | None = None


class FoundPersonCaseRead(CaseRead):
    details: FoundPersonDetailsRead


# --- Lost item -------------------------------------------------------------------------------------


class LostItemFields(StrictModel):
    item_category: str | None = Field(None, min_length=2, max_length=64)
    item_name: ShortText | None = None
    item_description: LongText | None = None
    brand_or_color: ShortText | None = None
    identifying_features: LongText | None = None
    last_seen_location: LocationText | None = None
    last_seen_at: PastDatetime | None = None


class LostItemCreate(CaseCreateCommon, LostItemFields):
    item_category: str = Field(min_length=2, max_length=64)
    item_name: ShortText


class LostItemUpdate(CaseUpdateCommon, LostItemFields):
    pass


class LostItemDetailsRead(ReadModel, LostItemFields):
    item_category: str
    item_name: str
    last_seen_at: AwareDatetime | None = None


class LostItemCaseRead(CaseRead):
    details: LostItemDetailsRead


# --- Found item ------------------------------------------------------------------------------------


class FoundItemFields(StrictModel):
    item_category: str | None = Field(None, min_length=2, max_length=64)
    item_name: ShortText | None = None
    item_description: LongText | None = None
    item_condition: ShortText | None = None
    found_location: LocationText | None = None
    found_at: PastDatetime | None = None
    storage_location: LocationText | None = None


class FoundItemCreate(CaseCreateCommon, FoundItemFields):
    item_category: str = Field(min_length=2, max_length=64)
    item_name: ShortText


class FoundItemUpdate(CaseUpdateCommon, FoundItemFields):
    pass


class FoundItemDetailsRead(ReadModel, FoundItemFields):
    item_category: str
    item_name: str
    found_at: AwareDatetime | None = None
    custody_status: CustodyStatus
    handover_acknowledgement: str | None = None
    released_at: AwareDatetime | None = None


class FoundItemCaseRead(CaseRead):
    details: FoundItemDetailsRead


# --- Search ----------------------------------------------------------------------------------------

SortOrder = Literal["-created_at", "created_at", "-updated_at", "updated_at"]
