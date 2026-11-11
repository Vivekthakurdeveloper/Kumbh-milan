"""Citizen OTP challenges. Plaintext codes are never stored or audited."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.public_tokens import (
    create_case_status_token,
    create_verified_mobile_token,
)
from app.common.context import client_ip_var
from app.common.errors import TooManyRequests, Unauthenticated, ValidationFailed
from app.common.security_hash import generate_otp, hash_otp, keyed_hash
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseContact, OtpChallenge
from app.models.enums import NotificationChannel, NotificationTemplate, OtpPurpose
from app.notifications import LOCALES, get_provider, mask, normalise_phone, render
from app.notifications import ProviderError


def _normalise(mobile: str) -> str:
    phone = normalise_phone(mobile, get_settings().notification_default_country_code)
    if phone is None:
        raise ValidationFailed("mobile_number is not a valid phone number", code="invalid_phone")
    return phone


def _active_challenge(db: Session, *, mobile_hash: str, purpose: OtpPurpose) -> OtpChallenge | None:
    return db.scalar(
        select(OtpChallenge)
        .where(
            OtpChallenge.mobile_lookup_hash == mobile_hash,
            OtpChallenge.purpose == purpose,
            OtpChallenge.consumed_at.is_(None),
            OtpChallenge.expires_at > utcnow(),
        )
        .order_by(OtpChallenge.created_at.desc())
    )


def request_otp(
    db: Session,
    *,
    purpose: OtpPurpose,
    mobile_number: str,
    locale: str = "hi",
    case_number: str | None = None,
) -> dict:
    """
    Issues an OTP. For CASE_STATUS, always returns the same acknowledgement whether or not the
    case/mobile association exists (anti-enumeration).
    """
    settings = get_settings()
    if locale not in LOCALES:
        raise ValidationFailed(f"locale must be one of {', '.join(LOCALES)}", code="unsupported_locale")
    phone = _normalise(mobile_number)
    mobile_hash = keyed_hash(phone)
    case: Case | None = None
    if purpose is OtpPurpose.CASE_STATUS:
        if not case_number:
            raise ValidationFailed("case_id is required", code="case_required")
        case = db.scalar(select(Case).where(Case.case_number == case_number.strip()))
        contact = db.get(CaseContact, case.id) if case is not None else None
        associated = (
            case is not None
            and contact is not None
            and contact.mobile_lookup_hash == mobile_hash
        )
        # Always acknowledge; only send when associated.
        if not associated:
            record_audit(
                db,
                action=AuditAction.PUBLIC_OTP_REQUESTED,
                entity_type="otp_challenge",
                actor_user_id=None,
                after={"purpose": purpose.value, "sent": False},
            )
            db.commit()
            return {
                "status": "OTP_SENT",
                "message": "If the details match our records, a verification code has been sent",
                "expires_in_seconds": settings.otp_ttl_seconds,
            }

    existing = _active_challenge(db, mobile_hash=mobile_hash, purpose=purpose)
    if existing is not None and existing.created_at > utcnow() - timedelta(seconds=settings.otp_resend_seconds):
        raise TooManyRequests(
            "Please wait before requesting another code",
            code="otp_resend_cooldown",
            details={"retry_after": settings.otp_resend_seconds},
        )

    code = generate_otp(settings.otp_length)
    challenge = OtpChallenge(
        id=uuid.uuid4(),
        purpose=purpose,
        mobile_lookup_hash=mobile_hash,
        mobile_hint=mask(phone),
        code_hash=hash_otp(code),
        case_id=case.id if case else None,
        case_number=case.case_number if case else case_number,
        attempts=0,
        max_attempts=settings.otp_max_attempts,
        expires_at=utcnow() + timedelta(seconds=settings.otp_ttl_seconds),
        client_ip=client_ip_var.get(),
        locale=locale,
    )
    db.add(challenge)
    record_audit(
        db,
        action=AuditAction.PUBLIC_OTP_REQUESTED,
        entity_type="otp_challenge",
        entity_id=challenge.id,
        actor_user_id=None,
        case=case,
        after={"purpose": purpose.value, "sent": True, "recipient": mask(phone)},
    )
    db.commit()

    body = render(NotificationTemplate.OTP_CODE, locale, otp=code, case_number="", station="")
    provider = get_provider()
    if provider.enabled:
        try:
            provider.send(
                channel=NotificationChannel.SMS,
                to=phone,
                body=body,
                idempotency_key=str(challenge.id),
                template=NotificationTemplate.OTP_CODE,
                locale=locale,
                extras={"otp": code},
            )
        except ProviderError:
            # Challenge remains valid for mock/offline tests; delivery failure is not an enumeration signal.
            pass

    return {
        "status": "OTP_SENT",
        "message": "If the details match our records, a verification code has been sent"
        if purpose is OtpPurpose.CASE_STATUS
        else "Verification code sent",
        "expires_in_seconds": settings.otp_ttl_seconds,
    }


def verify_otp(
    db: Session,
    *,
    purpose: OtpPurpose,
    mobile_number: str,
    code: str,
    case_number: str | None = None,
) -> dict:
    phone = _normalise(mobile_number)
    mobile_hash = keyed_hash(phone)
    challenge = _active_challenge(db, mobile_hash=mobile_hash, purpose=purpose)
    if challenge is None:
        raise Unauthenticated("Invalid or expired verification code", code="otp_invalid")
    if purpose is OtpPurpose.CASE_STATUS:
        if not case_number or challenge.case_number != case_number.strip():
            raise Unauthenticated("Invalid or expired verification code", code="otp_invalid")

    if challenge.attempts >= challenge.max_attempts:
        raise TooManyRequests("Too many incorrect attempts", code="otp_locked")

    if hash_otp(code.strip()) != challenge.code_hash:
        challenge.attempts += 1
        record_audit(
            db,
            action=AuditAction.PUBLIC_OTP_FAILED,
            entity_type="otp_challenge",
            entity_id=challenge.id,
            actor_user_id=None,
            after={"purpose": purpose.value, "attempts": challenge.attempts},
        )
        db.commit()
        if challenge.attempts >= challenge.max_attempts:
            raise TooManyRequests("Too many incorrect attempts", code="otp_locked")
        raise Unauthenticated("Invalid or expired verification code", code="otp_invalid")

    challenge.consumed_at = utcnow()
    record_audit(
        db,
        action=AuditAction.PUBLIC_OTP_VERIFIED,
        entity_type="otp_challenge",
        entity_id=challenge.id,
        actor_user_id=None,
        after={"purpose": purpose.value},
    )

    if purpose is OtpPurpose.CASE_STATUS:
        case = db.get(Case, challenge.case_id)
        if case is None:
            raise Unauthenticated("Invalid or expired verification code", code="otp_invalid")
        token, expires_in = create_case_status_token(
            case_id=case.id, case_number=case.case_number, mobile=phone
        )
        db.commit()
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "case_id": case.case_number,
            "scope": "status:read",
        }

    token, expires_in = create_verified_mobile_token(mobile=phone, purpose=purpose)
    db.commit()
    return {
        "verified_mobile_token": token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "purpose": purpose.value,
    }


def purge_expired(db: Session) -> int:
    deleted = db.execute(
        delete(OtpChallenge).where(OtpChallenge.expires_at < utcnow() - timedelta(days=1))
    ).rowcount
    db.commit()
    return deleted
