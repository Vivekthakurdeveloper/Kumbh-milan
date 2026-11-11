from fastapi import APIRouter, Request, status

from app.api.v1.responses import AUTHED, errors
from app.auth import service as auth_service
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.auth.permissions import ROLE_PERMISSIONS
from app.models.enums import Role
from app.schemas.admin import UserRead
from app.auth import mfa
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    MessageResponse,
    MfaConfirmRequest,
    MfaEnrollResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _tokens(pair: auth_service.TokenPair) -> TokenResponse:
    return TokenResponse(access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses=errors(409, 422, 429),
    summary="Register a PUBLIC citizen account",
)
def register(payload: RegisterRequest, request: Request, db: DbSession) -> TokenResponse:
    """
    Self-register with a mobile number as username. Role is always PUBLIC and cannot be chosen.
    Returns the same token structure as login.
    """
    pair = auth_service.register_public_user(
        db,
        username=payload.username,
        password=payload.password,
        full_name=payload.full_name,
        user_agent=request.headers.get("user-agent"),
    )
    return _tokens(pair)


@router.post("/login", response_model=TokenResponse, responses=errors(401, 422, 429))
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    """Exchange username and password for a 15-minute access token and a rotating refresh token."""
    pair = auth_service.login(
        db, username=payload.username, password=payload.password, user_agent=request.headers.get("user-agent")
    )
    return _tokens(pair)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    responses=errors(422, 429),
    summary="Request a password reset token for a PUBLIC account",
)
def forgot_password(payload: ForgotPasswordRequest, db: DbSession) -> MessageResponse:
    """
    Always returns the same message whether or not the account exists.
    When a PUBLIC account matches, a single-use reset token is stored as a hash and SMS delivery
    is attempted through the configured notification provider. Delivery is a deployment dependency.
    """
    message = auth_service.request_password_reset(db, username=payload.username)
    return MessageResponse(message=message)


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=errors(401, 422, 429),
    summary="Reset a PUBLIC account password with a one-time token",
)
def reset_password(payload: ResetPasswordRequest, db: DbSession) -> None:
    """Validate the reset token, set a new password, and revoke every existing session."""
    auth_service.reset_password(db, token=payload.token, new_password=payload.new_password)


@router.post("/refresh", response_model=TokenResponse, responses=errors(401, 422))
def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    """Rotate the refresh token. Presenting an already-rotated token revokes the whole session."""
    return _tokens(auth_service.refresh(db, refresh_token=payload.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=errors(401))
def logout(principal: CurrentPrincipal, db: DbSession) -> None:
    """Revoke the current session. Its access token stops working immediately."""
    auth_service.logout(db, user=principal.user, session_id=principal.session_id)


@router.post("/mfa/enroll", response_model=MfaEnrollResponse, responses=errors(401, 409))
def enroll_mfa(principal: CurrentPrincipal, db: DbSession) -> MfaEnrollResponse:
    """Start TOTP enrollment. The secret is returned once. Disabled unless MFA_MODE=totp."""
    return MfaEnrollResponse(secret=mfa.begin_enrollment(db, principal.user))


@router.post("/mfa/confirm", status_code=status.HTTP_204_NO_CONTENT, responses=errors(401, 409, 422))
def confirm_mfa(payload: MfaConfirmRequest, principal: CurrentPrincipal, db: DbSession) -> None:
    """Enable TOTP after the caller proves the current code. This is not phishing-resistant MFA."""
    mfa.confirm_enrollment(db, principal.user, payload.code)


@router.get("/me", response_model=MeResponse, responses=errors(401))
def me(principal: CurrentPrincipal) -> MeResponse:
    user = principal.user
    return MeResponse(
        **UserRead.model_validate(user).model_dump(),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)]),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, responses=AUTHED)
def change_password(payload: ChangePasswordRequest, principal: CurrentPrincipal, db: DbSession) -> None:
    """Change your password. Every session, including this one, is revoked; sign in again afterwards."""
    auth_service.change_password(
        db, user=principal.user, current_password=payload.current_password, new_password=payload.new_password
    )
