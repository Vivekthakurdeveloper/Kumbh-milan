import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth import mfa
from app.auth.passwords import (
    dummy_hash,
    hash_password,
    needs_rehash,
    validate_new_password,
    validate_public_registration_password,
    verify_password,
)
from app.auth.tokens import (
    create_access_token,
    hash_token,
    new_mfa_challenge_token,
    new_refresh_token,
    parse_mfa_challenge_token,
    parse_refresh_token,
)
from app.common.context import client_ip_var
from app.common.errors import Conflict, ServiceUnavailable, TooManyRequests, Unauthenticated, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.models import LoginAttempt, MfaLoginChallenge, RefreshToken, User
from app.models.enums import Role
from app.notifications import normalise_phone
from app.observability.metrics import AUTH_FAILURES

logger = logging.getLogger("kumbh_setu.auth")

INVALID_CREDENTIALS = "Invalid username or password"
INVALID_CHALLENGE = "The sign-in challenge is invalid or has already been used. Sign in again."
TOO_MANY_CODES = "Too many incorrect authentication codes. Sign in again."


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class MfaChallenge:
    """Returned by login instead of tokens when the account has MFA enabled."""

    challenge_token: str
    expires_in: int


def _issue(db: Session, user: User, session: RefreshToken) -> TokenPair:
    refresh, refresh_hash = new_refresh_token(session.id)
    session.previous_token_hash = session.token_hash if session.token_hash else None
    session.token_hash = refresh_hash
    access, expires_in = create_access_token(
        user_id=user.id, role=user.role, session_id=session.id, token_version=user.token_version
    )
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)


def _is_locked_out(db: Session, username: str) -> bool:
    settings = get_settings()
    window_start = utcnow() - timedelta(minutes=settings.login_lockout_minutes)
    last_success = db.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.username == username, LoginAttempt.succeeded.is_(True)
        )
    )
    since = max(window_start, last_success) if last_success else window_start
    failures = db.scalar(
        select(func.count()).where(
            LoginAttempt.username == username,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at > since,
        )
    )
    return (failures or 0) >= settings.login_max_failures


def _normalise_login_username(username: str) -> str:
    """Lowercase staff usernames; canonicalize digit-only citizen mobiles to E.164 without '+'."""
    cleaned = username.strip().lower()
    if cleaned.isdigit():
        try:
            mobile, _ = _canonical_mobile_username(cleaned)
            return mobile
        except ValidationFailed:
            return cleaned
    return cleaned


def _login_username_candidates(username: str) -> tuple[str, list[str]]:
    """Return one lockout key and compatible stored usernames, newest ten-digit form first."""
    cleaned = username.strip().lower()
    lockout_key = _normalise_login_username(cleaned)
    candidates = [cleaned]
    if cleaned.isdigit():
        if lockout_key not in candidates:
            candidates.append(lockout_key)
        country = get_settings().notification_default_country_code.lstrip("+")
        if cleaned.startswith(country) and len(cleaned) - len(country) == 10:
            local_mobile = cleaned[len(country):]
            if local_mobile not in candidates:
                candidates.append(local_mobile)
    return lockout_key, candidates


def login(db: Session, *, username: str, password: str, user_agent: str | None) -> TokenPair | MfaChallenge:
    username, candidates = _login_username_candidates(username)
    ip = client_ip_var.get()
    if _is_locked_out(db, username):
        AUTH_FAILURES.labels("locked_out").inc()
        raise TooManyRequests(
            "Too many failed sign-in attempts. Try again later.", code="account_temporarily_locked"
        )

    users_by_username = {
        candidate.username: candidate
        for candidate in db.scalars(select(User).where(User.username.in_(candidates)))
    }
    user = next(
        (users_by_username[value] for value in candidates if value in users_by_username),
        None,
    )
    password_ok = verify_password(user.password_hash if user else dummy_hash(), password)
    if user is None or not password_ok or not user.is_active:
        db.add(LoginAttempt(username=username, client_ip=ip, succeeded=False))
        record_audit(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            actor_user_id=None,
            entity_id=user.id if user else None,
            after={"username": username, "reason": "inactive" if user and password_ok else "bad_credentials"},
        )
        db.commit()
        AUTH_FAILURES.labels("bad_credentials").inc()
        raise Unauthenticated(INVALID_CREDENTIALS, code="invalid_credentials")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    if user.totp_enabled:
        # The password alone is not enough: no session exists until verify_mfa_login succeeds, and the
        # successful LoginAttempt is recorded only then, so earlier failures still count toward lockout.
        challenge = _issue_mfa_challenge(db, user, login_key=username)
        db.commit()
        return challenge
    pair = _create_session(db, user, user_agent=user_agent)
    db.add(LoginAttempt(username=username, client_ip=ip, succeeded=True))
    record_audit(db, action=AuditAction.LOGIN_SUCCEEDED, entity_type="user", actor_user_id=user.id, entity_id=user.id)
    db.commit()
    return pair


def _issue_mfa_challenge(db: Session, user: User, *, login_key: str) -> MfaChallenge:
    settings = get_settings()
    now = utcnow()
    db.execute(
        update(MfaLoginChallenge)
        .where(MfaLoginChallenge.user_id == user.id, MfaLoginChallenge.consumed_at.is_(None))
        .values(consumed_at=now, consumed_reason="superseded")
    )
    row = MfaLoginChallenge(
        id=uuid.uuid4(), user_id=user.id, token_hash="", login_key=login_key, token_version=user.token_version,
        attempts=0, expires_at=now + timedelta(seconds=settings.mfa_login_challenge_ttl_seconds),
        client_ip=client_ip_var.get(),
    )
    token, row.token_hash = new_mfa_challenge_token(row.id)
    db.add(row)
    record_audit(
        db, action=AuditAction.MFA_CHALLENGE_ISSUED, entity_type="user", actor_user_id=user.id, entity_id=user.id,
        after={"challenge_id": row.id, "method": "totp"},
    )
    return MfaChallenge(challenge_token=token, expires_in=settings.mfa_login_challenge_ttl_seconds)


def _consume(challenge: MfaLoginChallenge, reason: str) -> None:
    challenge.consumed_at, challenge.consumed_reason = utcnow(), reason


def verify_mfa_login(db: Session, *, challenge_token: str, code: str, user_agent: str | None) -> TokenPair:
    """
    Second login step. The challenge row and the user row are locked, so concurrent requests with the same
    challenge (or the same code on two challenges) cannot both succeed. A code's time step is remembered and
    never accepted twice. Wrong codes count toward the per-challenge limit and the account lockout.
    """
    settings = get_settings()
    challenge_id = parse_mfa_challenge_token(challenge_token)
    challenge = db.scalar(
        select(MfaLoginChallenge).where(MfaLoginChallenge.id == challenge_id).with_for_update()
    ) if challenge_id else None
    if challenge is None or not hmac.compare_digest(hash_token(challenge_token), challenge.token_hash):
        AUTH_FAILURES.labels("mfa_challenge_invalid").inc()
        raise Unauthenticated(INVALID_CHALLENGE, code="mfa_challenge_invalid")
    if challenge.consumed_at is not None:
        AUTH_FAILURES.labels("mfa_challenge_invalid").inc()
        if challenge.consumed_reason == "too_many_attempts":
            raise TooManyRequests(TOO_MANY_CODES, code="mfa_attempts_exceeded")
        raise Unauthenticated(INVALID_CHALLENGE, code="mfa_challenge_invalid")
    if challenge.expires_at <= utcnow():
        _consume(challenge, "expired")
        db.commit()
        AUTH_FAILURES.labels("mfa_challenge_expired").inc()
        raise Unauthenticated("The sign-in challenge has expired. Sign in again.", code="mfa_challenge_expired")

    user = db.get(User, challenge.user_id, with_for_update=True)
    if (
        user is None
        or not user.is_active
        or not user.totp_enabled
        or user.token_version != challenge.token_version
    ):
        _consume(challenge, "invalidated")
        db.commit()
        AUTH_FAILURES.labels("mfa_challenge_invalid").inc()
        raise Unauthenticated(INVALID_CHALLENGE, code="mfa_challenge_invalid")
    if _is_locked_out(db, challenge.login_key):
        _consume(challenge, "locked_out")
        db.commit()
        AUTH_FAILURES.labels("locked_out").inc()
        raise TooManyRequests(
            "Too many failed sign-in attempts. Try again later.", code="account_temporarily_locked"
        )

    try:
        secret = mfa.user_secret(user)
    except Exception as exc:  # noqa: BLE001 - an unreadable secret must fail closed without leaking details
        logger.error("MFA secret unreadable", extra={"user_id": str(user.id), "error": type(exc).__name__})
        raise ServiceUnavailable(
            "Two-step sign-in is temporarily unavailable. Contact an administrator.", code="mfa_unavailable"
        ) from exc
    step = mfa.match_totp_step(secret, code)
    replayed = step is not None and user.totp_last_used_step is not None and step <= user.totp_last_used_step
    if step is None or replayed:
        challenge.attempts += 1
        db.add(LoginAttempt(username=challenge.login_key, client_ip=client_ip_var.get(), succeeded=False))
        record_audit(
            db, action=AuditAction.MFA_LOGIN_FAILED, entity_type="user", actor_user_id=None, entity_id=user.id,
            after={"challenge_id": challenge.id, "attempt": challenge.attempts, "reason": "replayed" if replayed else "wrong_code"},
        )
        AUTH_FAILURES.labels("mfa_code_invalid").inc()
        if challenge.attempts >= settings.mfa_login_max_attempts:
            _consume(challenge, "too_many_attempts")
            record_audit(
                db, action=AuditAction.MFA_CHALLENGE_BURNED, entity_type="user", actor_user_id=None,
                entity_id=user.id, after={"challenge_id": challenge.id, "reason": "too_many_attempts"},
            )
            db.commit()
            raise TooManyRequests(TOO_MANY_CODES, code="mfa_attempts_exceeded")
        db.commit()
        raise Unauthenticated("The authentication code is incorrect", code="mfa_code_invalid")

    user.totp_last_used_step = step
    _consume(challenge, "used")
    pair = _create_session(db, user, user_agent=user_agent)
    db.add(LoginAttempt(username=challenge.login_key, client_ip=client_ip_var.get(), succeeded=True))
    record_audit(
        db, action=AuditAction.LOGIN_SUCCEEDED, entity_type="user", actor_user_id=user.id, entity_id=user.id,
        after={"mfa": "totp"},
    )
    db.commit()
    return pair


def refresh(db: Session, *, refresh_token: str) -> TokenPair:
    session_id = parse_refresh_token(refresh_token)
    session = db.scalar(select(RefreshToken).where(RefreshToken.id == session_id).with_for_update())
    if session is None:
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token")
    presented = hash_token(refresh_token)

    if session.previous_token_hash and hmac.compare_digest(presented, session.previous_token_hash):
        # A rotated-out token came back: treat the session as stolen.
        if session.revoked_at is None:
            session.revoked_at = utcnow()
            session.revoked_reason = "refresh_token_reuse"
            record_audit(
                db,
                action=AuditAction.TOKEN_REUSE_DETECTED,
                entity_type="session",
                actor_user_id=session.user_id,
                entity_id=session.id,
            )
            db.commit()
        AUTH_FAILURES.labels("refresh_token_reuse").inc()
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token")

    if not hmac.compare_digest(presented, session.token_hash):
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token")
    if session.revoked_at is not None or session.expires_at <= utcnow():
        raise Unauthenticated("Session is no longer valid", code="session_revoked")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise Unauthenticated("Session is no longer valid", code="session_revoked")

    pair = _issue(db, user, session)
    session.rotation_count += 1
    session.last_used_at = utcnow()
    db.commit()
    return pair


def logout(db: Session, *, user: User, session_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == session_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason="logout")
    )
    record_audit(db, action=AuditAction.LOGOUT, entity_type="session", actor_user_id=user.id, entity_id=session_id)
    db.commit()


def revoke_all_sessions(db: Session, user: User, reason: str) -> None:
    """Invalidates every refresh token and, via token_version, every outstanding access token."""
    user.token_version += 1
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )


def change_password(db: Session, *, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise ValidationFailed("Current password is incorrect", code="invalid_current_password")
    if current_password == new_password:
        raise ValidationFailed("New password must differ from the current password", code="weak_password")
    validate_new_password(new_password, username=user.username)
    user.password_hash = hash_password(new_password)
    revoke_all_sessions(db, user, "password_changed")
    record_audit(db, action=AuditAction.PASSWORD_CHANGED, entity_type="user", actor_user_id=user.id, entity_id=user.id)
    db.commit()


def _canonical_mobile_username(username: str) -> tuple[str, str]:
    """Return the legacy E.164-digit login key and E.164 phone for a mobile input."""
    settings = get_settings()
    digits = "".join(ch for ch in username.strip() if ch.isdigit())
    if len(digits) < 10 or len(digits) > 15:
        raise ValidationFailed("username must be a valid mobile number", code="invalid_phone")
    country = settings.notification_default_country_code.lstrip("+")
    candidates: list[str] = []
    # Prefer an already-international form so stored usernames (e.g. 9198…) do not get a second country prefix.
    if digits.startswith(country) and len(digits) > len(country) + 6:
        candidates.append(f"+{digits}")
    candidates.append(digits)
    for candidate in candidates:
        phone = normalise_phone(candidate, settings.notification_default_country_code)
        if phone is not None:
            return phone.lstrip("+"), phone
    raise ValidationFailed("username must be a valid mobile number", code="invalid_phone")


def _create_session(db: Session, user: User, *, user_agent: str | None) -> TokenPair:
    now = utcnow()
    user.last_login_at = now
    session = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash="",
        rotation_count=0,
        expires_at=now + timedelta(days=get_settings().refresh_token_ttl_days),
        last_used_at=now,
        user_agent=(user_agent or "")[:255] or None,
        client_ip=client_ip_var.get(),
    )
    db.add(session)
    return _issue(db, user, session)


def register_public_user(
    db: Session,
    *,
    username: str,
    password: str,
    full_name: str,
    first_name: str,
    middle_name: str | None,
    last_name: str,
    phone_number: str,
    email: str | None,
    address: str | None,
    user_agent: str | None,
) -> TokenPair:
    """Self-register a PUBLIC citizen account and issue the same token pair as login."""
    if len(username) != 10 or not username.isdigit() or username != phone_number:
        raise ValidationFailed(
            "username and phone_number must contain the same valid 10-digit mobile number",
            code="validation_error",
        )
    validate_public_registration_password(password)
    legacy_username, _ = _canonical_mobile_username(username)
    normalized_phone = func.right(func.regexp_replace(User.phone_number, r"[^0-9]", "", "g"), 10)
    if db.scalar(
        select(User.id).where(
            (normalized_phone == phone_number)
            | User.username.in_((username, legacy_username))
        )
    ):
        raise Conflict("Mobile number is already registered", code="username_taken")
    if email and db.scalar(select(User.id).where(func.lower(User.email) == email.lower())):
        raise Conflict("Email address is already registered", code="email_taken")

    user = User(
        id=uuid.uuid4(),
        full_name=full_name.strip(),
        first_name=first_name.strip(),
        middle_name=middle_name.strip() if middle_name is not None else None,
        last_name=last_name.strip(),
        username=username,
        phone_number=phone_number,
        email=email,
        address=address.strip() if address is not None else None,
        password_hash=hash_password(password),
        role=Role.PUBLIC,
        station_id=None,
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()  # Persist the user before audit FK / session rows reference user.id.
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_users_email":
            raise Conflict("Email address is already registered", code="email_taken") from exc
        if constraint in {"uq_users_username", "uq_users_public_mobile_normalized"}:
            raise Conflict("Mobile number is already registered", code="username_taken") from exc
        raise Conflict("Registration conflicts with an existing account", code="registration_conflict") from exc

    record_audit(
        db,
        action=AuditAction.PUBLIC_USER_REGISTERED,
        entity_type="user",
        entity_id=user.id,
        actor_user_id=user.id,
        after={"username": username, "role": Role.PUBLIC, "source": "self_registration"},
    )
    pair = _create_session(db, user, user_agent=user_agent)
    db.add(LoginAttempt(username=legacy_username, client_ip=client_ip_var.get(), succeeded=True))
    record_audit(
        db, action=AuditAction.LOGIN_SUCCEEDED, entity_type="user", actor_user_id=user.id, entity_id=user.id
    )
    db.commit()
    return pair
