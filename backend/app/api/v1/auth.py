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
    MfaChallengeResponse,
    MfaConfirmRequest,
    MfaEnrollResponse,
    MfaVerifyLoginRequest,
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
    Self-register with the same exact 10-digit mobile in username and phone_number. Role is always PUBLIC
    and cannot be chosen. All supplied profile fields are persisted and the existing login token pair is returned.
    """
    pair = auth_service.register_public_user(
        db,
        username=payload.username,
        password=payload.password,
        full_name=payload.full_name,
        first_name=payload.first_name,
        middle_name=payload.middle_name,
        last_name=payload.last_name,
        phone_number=payload.phone_number,
        email=str(payload.email) if payload.email is not None else None,
        address=payload.address,
        user_agent=request.headers.get("user-agent"),
    )
    return _tokens(pair)


@router.post(
    "/login",
    response_model=TokenResponse | MfaChallengeResponse,
    responses={
        200: {
            "description": (
                "Either the token pair (MFA not enabled) or an MFA challenge (MFA enabled). "
                "Tell them apart by `mfa_required`."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "tokens": {
                            "summary": "MFA not enabled: signed in",
                            "value": {
                                "access_token": "eyJhbGciOiJIUzI1NiIs...",
                                "refresh_token": "6f1c...e2.Q0xJRU5U...",
                                "token_type": "bearer",
                                "expires_in": 900,
                            },
                        },
                        "mfa_required": {
                            "summary": "MFA enabled: step 2 needed",
                            "value": {
                                "mfa_required": True,
                                "challenge_token": "mfa.0b7e...c1.sR3x...",
                                "message": "MFA verification required.",
                                "challenge_expires_in": 300,
                            },
                        },
                    }
                }
            },
        },
        **errors(401, 422, 429),
    },
)
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse | MfaChallengeResponse:
    """
    Step 1 of sign-in. Exchange username and password for a 15-minute access token and a rotating refresh token.

    When the account has MFA enabled, no tokens and no session are issued. The answer is instead
    `{"mfa_required": true, "challenge_token", "message", "challenge_expires_in"}`: show the 6-digit code input
    and call POST /auth/mfa/verify-login (step 2) with the challenge token. A wrong password gives the same
    `401 invalid_credentials` whether or not the account exists or uses MFA.
    """
    result = auth_service.login(
        db, username=payload.username, password=payload.password, user_agent=request.headers.get("user-agent")
    )
    if isinstance(result, auth_service.MfaChallenge):
        return MfaChallengeResponse(challenge_token=result.challenge_token, challenge_expires_in=result.expires_in)
    return _tokens(result)


@router.post(
    "/mfa/verify-login",
    response_model=TokenResponse,
    responses=errors(401, 422, 429, 503),
    summary="Step 2 of sign-in for MFA accounts: exchange the challenge and a 6-digit code for tokens",
)
def verify_mfa_login(payload: MfaVerifyLoginRequest, request: Request, db: DbSession) -> TokenResponse:
    """
    Completes sign-in after POST /auth/login answered `mfa_required: true`. Returns the same token pair as a
    normal login. No bearer token is needed or accepted here.

    - `401 mfa_code_invalid`: wrong code (or a code already used); retry with the next code.
    - `401 mfa_challenge_expired` / `401 mfa_challenge_invalid`: the challenge is expired, already used, or
      superseded by a newer login; sign in again.
    - `429 mfa_attempts_exceeded`: too many wrong codes for this challenge (default 5); sign in again.
    - `429 account_temporarily_locked`: wrong codes count toward the same lockout as wrong passwords.
    - `429 too_many_requests`: per-IP rate limit, with `Retry-After`.

    The challenge is single-use and lasts `MFA_LOGIN_CHALLENGE_TTL_SECONDS` (default 300).
    """
    pair = auth_service.verify_mfa_login(
        db, challenge_token=payload.challenge_token, code=payload.mfa_code,
        user_agent=request.headers.get("user-agent"),
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
    """
    Start TOTP enrollment (profile screen). The secret is returned once: show it as a QR code /
    `otpauth://totp/...?secret=...&digits=6&period=30` for an authenticator app. Disabled (`409 mfa_disabled`)
    unless MFA_MODE=totp; `409 mfa_already_enabled` once enabled.
    """
    return MfaEnrollResponse(secret=mfa.begin_enrollment(db, principal.user))


@router.post("/mfa/confirm", status_code=status.HTTP_204_NO_CONTENT, responses=errors(401, 409, 422))
def confirm_mfa(payload: MfaConfirmRequest, principal: CurrentPrincipal, db: DbSession) -> None:
    """
    Enable TOTP after the caller proves the current code (`422 mfa_code_invalid` otherwise). From then on
    POST /auth/login answers `mfa_required: true` and sign-in needs POST /auth/mfa/verify-login. Every other
    session of this user is signed out; the calling session stays signed in. This is not phishing-resistant MFA.
    """
    mfa.confirm_enrollment(db, principal.user, payload.code, keep_session_id=principal.session_id)


@router.get("/me", response_model=MeResponse, responses=errors(401))
def me(principal: CurrentPrincipal) -> MeResponse:
    user = principal.user
    return MeResponse(
        **UserRead.model_validate(user).model_dump(),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)]),
        mfa_enabled=bool(user.totp_enabled),
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
