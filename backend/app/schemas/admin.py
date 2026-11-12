import uuid
from datetime import datetime
from typing import Annotated

from pydantic import EmailStr, Field, StringConstraints

from app.models.enums import Role, StationKind
from app.schemas.common import ReadModel, StrictModel

Username = Annotated[
    str, StringConstraints(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$", to_lower=True)
]
Phone = Annotated[str, StringConstraints(min_length=6, max_length=32, pattern=r"^\+?[0-9 ()-]+$")]


class StationCreate(StrictModel):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    kind: StationKind
    location_description: str | None = Field(None, max_length=500)


class StationUpdate(StrictModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    location_description: str | None = Field(None, max_length=500)
    is_active: bool | None = None


class StationRead(ReadModel):
    id: uuid.UUID
    name: str
    code: str
    kind: StationKind
    location_description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserCreate(StrictModel):
    full_name: str = Field(min_length=2, max_length=200)
    username: Username
    email: EmailStr | None = None
    phone_number: Phone | None = None
    password: str = Field(min_length=1, max_length=128, description="Initial password; share out of band")
    role: Role
    police_badge_number: str | None = Field(None, min_length=2, max_length=64)
    station_id: uuid.UUID | None = None


class UserUpdate(StrictModel):
    full_name: str | None = Field(None, min_length=2, max_length=200)
    email: EmailStr | None = None
    phone_number: Phone | None = None
    role: Role | None = None
    police_badge_number: str | None = Field(None, min_length=2, max_length=64)
    station_id: uuid.UUID | None = None
    is_active: bool | None = None


class PasswordResetRequest(StrictModel):
    new_password: str = Field(min_length=1, max_length=128)


class UserRead(ReadModel):
    id: uuid.UUID
    full_name: str
    username: str
    email: str | None
    phone_number: str | None
    role: Role
    police_badge_number: str | None
    station_id: uuid.UUID | None
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OfficerSummary(ReadModel):
    """One row of GET /users/assignable-officers (police and admins only). No contact fields."""

    id: uuid.UUID
    full_name: str
    username: str
    role: Role
    police_badge_number: str | None
    station_id: uuid.UUID | None


class UserDirectoryEntry(ReadModel):
    """Minimal identity for displaying reporter, assignee, and note-author names. No contact fields."""

    id: uuid.UUID
    full_name: str
    role: Role
    station_id: uuid.UUID | None
