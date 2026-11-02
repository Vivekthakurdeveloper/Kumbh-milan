import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt

from app.common.errors import Unauthenticated
from app.common.time import utcnow
from app.config import get_settings


def create_access_token(
    *, user_id: uuid.UUID, role: str, session_id: uuid.UUID, token_version: int
) -> tuple[str, int]:
    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    now = utcnow()
    claims = {
        "sub": str(user_id),
        "sid": str(session_id),
        "tv": token_version,
        "role": role,
        "iss": settings.jwt_issuer,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "sid", "tv", "exp", "iat", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthenticated("Access token has expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthenticated("Invalid access token", code="invalid_token") from exc
    if claims.get("typ") != "access":
        raise Unauthenticated("Invalid access token", code="invalid_token")
    return claims


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_refresh_token(session_id: uuid.UUID) -> tuple[str, str]:
    """Returns (opaque token for the client, hash to store). The session id prefix makes lookup O(1)."""
    token = f"{session_id}.{secrets.token_urlsafe(32)}"
    return token, hash_token(token)


def parse_refresh_token(token: str) -> uuid.UUID:
    session_part, _, secret = token.partition(".")
    try:
        session_id = uuid.UUID(session_part)
    except ValueError as exc:
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token") from exc
    if not secret:
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token")
    return session_id
