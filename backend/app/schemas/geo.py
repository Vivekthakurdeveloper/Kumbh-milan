import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.enums import CaseStatus, CaseType, LocationKind, UrgencyLevel
from app.schemas.cases import CaseSummary
from app.schemas.common import ReadModel, StrictModel

Latitude = Field(ge=-90, le=90, description="WGS 84 latitude in decimal degrees")
Longitude = Field(ge=-180, le=180, description="WGS 84 longitude in decimal degrees")


class LocationCreate(StrictModel):
    kind: LocationKind
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9-]{1,31}$", examples=["HKP-GHAT"])
    name: str = Field(min_length=2, max_length=200, examples=["Har Ki Pauri"])
    name_local: str | None = Field(None, max_length=200, examples=["हर की पौड़ी"])
    sector_id: uuid.UUID | None = Field(None, description="Parent sector (not allowed for SECTOR locations)")
    station_id: uuid.UUID | None = Field(None, description="Police station or help desk responsible for this place")
    latitude: float = Latitude
    longitude: float = Longitude


class LocationUpdate(StrictModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    name_local: str | None = Field(None, max_length=200)
    sector_id: uuid.UUID | None = None
    station_id: uuid.UUID | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _paired(self) -> "LocationUpdate":
        fields = self.model_fields_set
        if ("latitude" in fields) != ("longitude" in fields):
            raise ValueError("latitude and longitude must be updated together")
        return self


class LocationRead(ReadModel):
    id: uuid.UUID
    kind: LocationKind
    code: str
    name: str
    name_local: str | None
    sector_id: uuid.UUID | None
    station_id: uuid.UUID | None
    latitude: float
    longitude: float
    is_active: bool
    created_at: datetime
    updated_at: datetime


PositionSource = Literal["case", "location"]


class CasePosition(ReadModel):
    latitude: float
    longitude: float
    position_source: PositionSource = Field(
        description="case: the case's own GPS fix or map pin. location: the referenced ghat/sector's coordinates"
    )


class NearbyCase(CaseSummary):
    distance_m: float = Field(description="Distance from the query point in metres")
    position: CasePosition


class CaseMarker(ReadModel):
    """Map marker: no names, descriptions, or contact details."""

    id: uuid.UUID
    case_number: str
    case_type: CaseType
    status: CaseStatus
    urgency_level: UrgencyLevel | None
    station_id: uuid.UUID
    position: CasePosition
    in_scope: bool


class CaseMap(ReadModel):
    items: list[CaseMarker]
    truncated: bool = Field(description="True when more cases are in the box than the limit; zoom in")
