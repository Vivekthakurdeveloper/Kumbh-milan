"""
Self-service password reset by emailed or texted one-time code.

Design points:
- Requests never reveal whether an account exists: the caller always gets the same answer, and the message is
  sent after the response so timing does not differ either.
- Only a keyed hash of the code is stored. A code lasts PASSWORD_RESET_TTL_MINUTES, works once, is replaced by a
  newer request, and is burned after PASSWORD_RESET_MAX_ATTEMPTS wrong guesses.
- A successful reset revokes every session, so a thief holding a stolen session is signed out too.
- This is separate from the admin route POST /users/{id}/reset-password.
"""

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.passwords import hash_password, validate_new_password
from app.auth.service import revoke_all_sessions
from app.common.context import client_ip_var
from app.common.errors import ServiceUnavailable, ValidationFailed
from app.common.time import utcnow
from app.config import Settings, get_settings
from app.models import LoginAttempt, PasswordResetToken, User
from app.models.enums import NotificationChannel
from app.notifications import get_provider, mask, normalise_phone
from app.notifications.email import OutgoingEmail, get_email_sender

logger = logging.getLogger("kumbh_setu.password_reset")

EMAIL, SMS = "EMAIL", "SMS"
INVALID_CODE = "The code is invalid or has expired"
_DIGITS_ONLY = re.compile(r"\D")


@dataclass(frozen=True)
class Delivery:
    """Everything the background task needs to send one message. Lives in memory only; never persisted."""

    channel: str
    destination: str
    subject: str
    body: str
    idempotency_key: str


def channels_available() -> dict[str, bool]:
    return {EMAIL: get_email_sender().enabled, SMS: get_provider().enabled}


def _code_hash(settings: Settings, token_id: uuid.UUID, code: str) -> str:
    key = hmac.new(settings.jwt_secret.encode(), b"kumbh-setu/password-reset/v1", hashlib.sha256).digest()
    return hmac.new(key, f"{token_id}:{code}".encode(), hashlib.sha256).hexdigest()


# --- Finding the account -------------------------------------------------------------------------


def _find_user(db: Session, identifier: str, default_cc: str) -> tuple[User | None, str]:
    """Returns the active user and how they were identified: 'email', 'phone', or 'username'."""
    ident = identifier.strip()
    if "@" in ident:
        user = db.scalar(select(User).where(func.lower(User.email) == ident.lower()))
        return (user, "email") if user and user.is_active else (None, "email")
    user = db.scalar(select(User).where(User.username == ident.lower()))
    if user is not None:
        return (user if user.is_active else None), "username"
    phone = normalise_phone(ident, default_cc)
    if phone is None:
        return None, "phone"
    tail = _DIGITS_ONLY.sub("", phone)[-9:]
    candidates = db.scalars(
        select(User).where(
            User.is_active.is_(True), User.phone_number.is_not(None),
            func.regexp_replace(User.phone_number, r"\D", "", "g").like(f"%{tail}"),
        )
    )
    matches = [u for u in candidates if normalise_phone(u.phone_number or "", default_cc) == phone]
    # A number shared by several accounts is ambiguous: refuse rather than guess whose password to reset.
    return (matches[0], "phone") if len(matches) == 1 else (None, "phone")


def _pick_channel(user: User, how: str, requested: str | None, available: dict[str, bool], default_cc: str) -> str | None:
    can_email = bool(user.email) and available[EMAIL]
    can_sms = available[SMS] and normalise_phone(user.phone_number or "", default_cc) is not None
    if requested == EMAIL:
        return EMAIL if can_email else None
    if requested == SMS:
        return SMS if can_sms else None
    if how == "email":
        return EMAIL if can_email else None
    if how == "phone":
        return SMS if can_sms else None
    return EMAIL if can_email else SMS if can_sms else None


# --- Request ---------------------------------------------------------------------------------------


def _email_body(user: User, code: str, minutes: int) -> str:
    return (
        f"Hello {user.full_name},\n\n"
        f"Your Kumbh Setu password reset code is: {code}\n"
        f"It is valid for {minutes} minutes and can be used once.\n\n"
        "If you did not ask for this, ignore this email. Your password has not changed.\n\n"
        f"नमस्ते {user.full_name}, आपका कुंभ सेतु पासवर्ड रीसेट कोड: {code}\n"
        f"यह {minutes} मिनट तक मान्य है। यदि आपने अनुरोध नहीं किया, तो इसे अनदेखा करें।\n"
    )


def request_reset(
    db: Session, *, identifier: str, requested_channel: str | None
) -> Delivery | None:
    """
    Creates a reset code and returns the message to send, or None when nothing should be sent. The caller
    answers identically either way. Raises only when the deployment cannot send any message at all.
    """
    settings = get_settings()
    available = channels_available()
    if not any(available.values()):
        raise ServiceUnavailable(
            "Password reset by email or SMS is not set up. Ask an administrator to reset your password.",
            code="password_reset_unavailable",
        )
    cc = settings.notification_default_country_code
    user, how = _find_user(db, identifier, cc)
    if user is None:
        return None
    channel = _pick_channel(user, how, requested_channel, available, cc)
    if channel is None:
        logger.info("Password reset requested but no deliverable channel", extra={"user_id": str(user.id)})
        return None

    now = utcnow()
    recent = db.scalar(
        select(func.count()).select_from(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.created_at > now - timedelta(hours=1)
        )
    )
    if (recent or 0) >= settings.password_reset_max_requests_per_hour:
        logger.warning("Password reset throttled for account", extra={"user_id": str(user.id)})
        return None

    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.consumed_at.is_(None))
        .values(consumed_at=now, consumed_reason="superseded")
    )
    code = f"{secrets.randbelow(10 ** settings.password_reset_code_length):0{settings.password_reset_code_length}d}"
    token = PasswordResetToken(
        id=uuid.uuid4(), user_id=user.id, channel=channel, code_hash="", client_ip=client_ip_var.get(),
        expires_at=now + timedelta(minutes=settings.password_reset_ttl_minutes),
    )
    token.code_hash = _code_hash(settings, token.id, code)
    db.add(token)

    minutes = settings.password_reset_ttl_minutes
    if channel == EMAIL:
        destination = user.email or ""
        subject = "Kumbh Setu password reset code"
        body = _email_body(user, code, minutes)
        hint = destination.split("@")[-1]
    else:
        destination = normalise_phone(user.phone_number or "", cc) or ""
        subject = ""
        body = f"Kumbh Setu: password reset code {code}. Valid {minutes} min. Do not share it with anyone."
        hint = mask(destination)
    record_audit(
        db, action=AuditAction.PASSWORD_RESET_REQUESTED, entity_type="user", entity_id=user.id,
        actor_user_id=None, after={"channel": channel, "sent_to": hint},
    )
    db.commit()
    return Delivery(channel, destination, subject, body, idempotency_key=f"pwreset-{token.id}")


def deliver(delivery: Delivery) -> None:
    """Runs after the response. A failure is logged without the code; the user can simply ask again."""
    try:
        if delivery.channel == EMAIL:
            get_email_sender().send(OutgoingEmail(delivery.destination, delivery.subject, delivery.body))
        else:
            get_provider().send(
                channel=NotificationChannel.SMS, to=delivery.destination, body=delivery.body,
                idempotency_key=delivery.idempotency_key,
            )
    except Exception as exc:  # noqa: BLE001 - never surface provider details or the message to anyone
        logger.error(
            "Password reset message could not be sent",
            extra={"channel": delivery.channel, "error": type(exc).__name__},
        )


# --- Completion --------------------------------------------------------------------------------------


def _invalid() -> ValidationFailed:
    return ValidationFailed(INVALID_CODE, code="invalid_reset_code")


def reset_password(db: Session, *, identifier: str, code: str, new_password: str) -> Delivery | None:
    """Verifies the code and sets the new password. Returns an optional 'your password changed' notice."""
    settings = get_settings()
    cc = settings.notification_default_country_code
    user, _ = _find_user(db, identifier, cc)
    token = None
    if user is not None:
        token = db.scalar(
            select(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.consumed_at.is_(None),
                PasswordResetToken.expires_at > utcnow(),
            )
            .order_by(PasswordResetToken.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    if user is None or token is None:
        # Same work as a real check, so the answer and its timing do not reveal whether the account exists.
        hmac.compare_digest(_code_hash(settings, uuid.uuid4(), code), "0" * 64)
        raise _invalid()

    if not hmac.compare_digest(_code_hash(settings, token.id, code), token.code_hash):
        token.attempts += 1
        if token.attempts >= settings.password_reset_max_attempts:
            token.consumed_at, token.consumed_reason = utcnow(), "too_many_attempts"
            record_audit(
                db, action=AuditAction.PASSWORD_RESET_CODE_BURNED, entity_type="user", entity_id=user.id,
                actor_user_id=None, after={"reason": "too_many_attempts"},
            )
        db.commit()
        raise _invalid()

    # The code is right. Check the password before spending it, so a weak choice can be corrected.
    validate_new_password(new_password, username=user.username)

    now = utcnow()
    user.password_hash = hash_password(new_password)
    revoke_all_sessions(db, user, "password_reset")
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.id != token.id,
        )
        .values(consumed_at=now, consumed_reason="superseded")
    )
    token.consumed_at, token.consumed_reason = now, "used"
    # Proving control of the registered channel is stronger evidence than the failed guesses that locked the account.
    db.execute(delete(LoginAttempt).where(LoginAttempt.username == user.username, LoginAttempt.succeeded.is_(False)))
    record_audit(
        db, action=AuditAction.PASSWORD_RESET_COMPLETED, entity_type="user", entity_id=user.id,
        actor_user_id=user.id, after={"channel": token.channel},
    )
    db.commit()

    available = channels_available()
    if user.email and available[EMAIL]:
        return Delivery(
            EMAIL, user.email, "Your Kumbh Setu password was changed",
            f"Hello {user.full_name},\n\nYour Kumbh Setu password was just changed using a reset code. "
            "If this was not you, contact an administrator immediately.\n",
            idempotency_key=f"pwchanged-{token.id}",
        )
    return None
