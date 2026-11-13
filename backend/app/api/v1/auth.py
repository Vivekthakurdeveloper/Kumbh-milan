from fastapi import APIRouter, BackgroundTasks, Request, status

from app.api.v1.responses import AUTHED, errors
from app.auth import mfa, password_reset
from app.auth import service as auth_service
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.auth.permissions import ROLE_PERMISSIONS
from app.config import get_settings
from app.models.enums import Role
from app.schemas.admin import UserRead
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    MeResponse,
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


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ForgotPasswordResponse,
    responses=errors(422, 429, 503),
)
def forgot_password(
    payload: ForgotPasswordRequest, background: BackgroundTasks, db: DbSession
) -> ForgotPasswordResponse:
    """
    Email or text a one-time reset code. No sign-in needed. The answer is the same whether or not the account
    exists, and the message is sent after the response, so neither content nor timing reveals which accounts
    are real. Finish with POST /auth/reset-password. Returns 503 `password_reset_unavailable` only when the
    deployment has neither email nor SMS delivery configured. Limited per IP and per account.
    """
    delivery = password_reset.request_reset(db, identifier=payload.identifier, requested_channel=payload.channel)
    if delivery is not None:
        background.add_task(password_reset.deliver, delivery)
    settings = get_settings()
    return ForgotPasswordResponse(
        message="If an account matches, a reset code has been sent.",
        code_length=settings.password_reset_code_length,
        expires_in_minutes=settings.password_reset_ttl_minutes,
    )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT, responses=errors(422, 429))
def reset_password(payload: ResetPasswordRequest, background: BackgroundTasks, db: DbSession) -> None:
    """
    Set a new password using the code from POST /auth/forgot-password. Every session is revoked, so sign in
    again afterwards. A wrong, expired, or used code is `422 invalid_reset_code`; five wrong guesses burn the
    code. A weak password is `422 weak_password` and does not use up the code. Not the admin route
    POST /users/{id}/reset-password.
    """
    notice = password_reset.reset_password(
        db, identifier=payload.identifier, code=payload.code, new_password=payload.new_password
    )
    if notice is not None:
        background.add_task(password_reset.deliver, notice)
