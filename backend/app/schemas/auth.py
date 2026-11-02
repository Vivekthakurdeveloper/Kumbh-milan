from pydantic import Field

from app.schemas.admin import UserRead
from app.schemas.common import StrictModel


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


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
