from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from app.schemas.admin import UserRead
from app.schemas.common import StrictModel

MobileUsername = Annotated[
    str,
    StringConstraints(min_length=10, max_length=15, pattern=r"^[0-9]+$", strip_whitespace=True),
]


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(StrictModel):
    """Citizen self-registration. Role is always PUBLIC and cannot be supplied by the client."""

    username: MobileUsername = Field(description="Citizen mobile number used as the login username")
    password: str = Field(min_length=1, max_length=128)
    full_name: str = Field(min_length=2, max_length=200)

    @field_validator("full_name")
    @classmethod
    def _trim_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("full_name must be at least 2 characters")
        return cleaned


class ForgotPasswordRequest(StrictModel):
    username: MobileUsername = Field(description="Registered mobile number")


class ResetPasswordRequest(StrictModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=1, max_length=128)


class MessageResponse(StrictModel):
    message: str


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=10, max_length=256)


class TokenResponse(StrictModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class MeResponse(UserRead):
    permissions: list[str]


class MfaEnrollResponse(StrictModel):
    secret: str = Field(min_length=16, max_length=64)
    digits: int = 6
    period_seconds: int = 30


class MfaConfirmRequest(StrictModel):
    code: str = Field(pattern=r"^[0-9]{6}$")
