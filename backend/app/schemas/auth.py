from typing import Annotated, Literal

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


class ForgotPasswordRequest(StrictModel):
    identifier: str = Field(
        min_length=3, max_length=320, description="Username, email address, or mobile number on the account"
    )
    channel: Literal["EMAIL", "SMS"] | None = Field(
        None,
        description=(
            "Where to send the code. Omit to use the one matching the identifier: an email address gets email, a "
            "mobile number gets SMS, and a username gets email, or SMS when the account has no email."
        ),
    )


class ForgotPasswordResponse(StrictModel):
    message: str = Field(
        description="Always the same text, whether or not an account matched, so accounts cannot be discovered"
    )
    code_length: int = Field(description="Digits in the code the user will receive")
    expires_in_minutes: int = Field(description="How long the code stays valid")


class ResetPasswordRequest(StrictModel):
    identifier: str = Field(
        min_length=3, max_length=320, description="The same username, email, or mobile number used to request the code"
    )
    code: str = Field(pattern=r"^[0-9]{6,12}$", description="The one-time code from the email or SMS")
    new_password: str = Field(min_length=1, max_length=128)
