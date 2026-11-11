"""Short-lived citizen tokens. Distinct from staff access tokens (`typ=access`)."""

import uuid
from datetime import timedelta
from typing import Any, Literal

import jwt

from app.common.errors import Unauthenticated
from app.common.security_hash import public_secret
from app.common.time import utcnow
from app.config import get_settings
from app.models.enums import OtpPurpose

PublicTokenType = Literal["verified_mobile", "case_status"]


def create_verified_mobile_token(*, mobile: str, purpose: OtpPurpose) -> tuple[str, int]:
    settings = get_settings()
    ttl = timedelta(minutes=settings.public_verified_mobile_token_ttl_minutes)
    now = utcnow()
    claims = {
        "typ": "verified_mobile",
        "purpose": purpose.value,
        "mobile": mobile,
        "iss": settings.jwt_issuer,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, public_secret(), algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def create_case_status_token(*, case_id: uuid.UUID, case_number: str, mobile: str) -> tuple[str, int]:
    settings = get_settings()
    ttl = timedelta(minutes=settings.public_status_token_ttl_minutes)
    now = utcnow()
    claims = {
        "typ": "case_status",
        "scope": "status:read",
        "case_id": str(case_id),
        "case_number": case_number,
        "mobile": mobile,
        "iss": settings.jwt_issuer,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, public_secret(), algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_public_token(token: str, *, expected_typ: PublicTokenType) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            public_secret(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthenticated("Verification session has expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthenticated("Invalid verification token", code="invalid_token") from exc
    if claims.get("typ") != expected_typ:
        raise Unauthenticated("Invalid verification token", code="invalid_token")
    return claims
