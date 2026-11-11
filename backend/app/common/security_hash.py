"""Keyed hashes for citizen lookup values. Never log plaintext mobiles, emails, OTPs, or tokens."""

import hashlib
import hmac
import secrets

from app.config import get_settings


def public_secret() -> bytes:
    settings = get_settings()
    secret = settings.public_security_secret or settings.jwt_secret
    return secret.encode("utf-8")


def keyed_hash(value: str) -> str:
    return hmac.new(public_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_otp(code: str) -> str:
    return keyed_hash(f"otp:{code}")


def generate_otp(length: int) -> str:
    upper = 10**length
    return f"{secrets.randbelow(upper):0{length}d}"


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "••••"
    visible = local[:1] if local else ""
    return f"{visible}••••@{domain}"
