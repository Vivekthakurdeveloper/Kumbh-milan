"""Time-based one-time passwords for step-up on privileged API actions.

This is not phishing-resistant MFA. Production use stays disabled until an approved
mechanism and recovery procedure are selected.
"""

import base64
import hashlib
import hmac
import os
import struct
import time

from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.common.context import step_up_code_var
from app.common.crypto import decrypt_text, encrypt_text
from app.common.errors import Conflict, Forbidden, ValidationFailed
from app.config import get_settings
from app.models import User

_STEP = 30
_DIGITS = 6


def generate_secret() -> bytes:
    return os.urandom(20)


def encode_secret(secret: bytes) -> str:
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def decode_secret(encoded: str) -> bytes:
    padding = "=" * ((8 - len(encoded) % 8) % 8)
    return base64.b32decode(encoded.upper() + padding, casefold=True)


def totp(secret: bytes, when: float | None = None) -> str:
    counter = int((time.time() if when is None else when) // _STEP)
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 10 ** _DIGITS:0{_DIGITS}d}"


def verify_totp(secret: bytes, code: str, when: float | None = None) -> bool:
    if len(code) != _DIGITS or not code.isdigit():
        return False
    moment = time.time() if when is None else when
    return any(hmac.compare_digest(totp(secret, moment + shift * _STEP), code) for shift in (-1, 0, 1))


def _require_mode() -> None:
    if get_settings().mfa_mode != "totp":
        raise Conflict(
            "MFA enrollment is disabled until an approved mechanism is configured",
            code="mfa_disabled",
        )


def begin_enrollment(db: Session, user: User) -> str:
    _require_mode()
    if user.totp_enabled:
        raise Conflict("MFA is already enabled", code="mfa_already_enabled")
    secret = generate_secret()
    user.totp_secret_ciphertext = encrypt_text(encode_secret(secret))
    user.totp_enabled = False
    record_audit(
        db, action=AuditAction.USER_UPDATED, entity_type="user", entity_id=user.id, actor_user_id=user.id,
        after={"mfa": "enrollment_started"},
    )
    db.commit()
    return encode_secret(secret)


def confirm_enrollment(db: Session, user: User, code: str) -> None:
    _require_mode()
    if user.totp_enabled:
        raise Conflict("MFA is already enabled", code="mfa_already_enabled")
    if not user.totp_secret_ciphertext:
        raise Conflict("Start MFA enrollment first", code="mfa_enrollment_missing")
    secret = decode_secret(decrypt_text(user.totp_secret_ciphertext))
    if not verify_totp(secret, code):
        raise ValidationFailed("The authentication code did not match", code="mfa_code_invalid")
    user.totp_enabled = True
    record_audit(
        db, action=AuditAction.USER_UPDATED, entity_type="user", entity_id=user.id, actor_user_id=user.id,
        after={"mfa": "enabled"},
    )
    db.commit()


def require_step_up(actor: User) -> None:
    """No-op unless MFA_MODE=totp. Does not claim the method is phishing resistant."""
    if get_settings().mfa_mode != "totp":
        return
    if not actor.totp_enabled or not actor.totp_secret_ciphertext:
        raise Forbidden("Enroll MFA before this privileged action", code="mfa_enrollment_required")
    code = step_up_code_var.get() or ""
    secret = decode_secret(decrypt_text(actor.totp_secret_ciphertext))
    if not verify_totp(secret, code):
        raise Forbidden("Step-up authentication failed", code="step_up_required")
