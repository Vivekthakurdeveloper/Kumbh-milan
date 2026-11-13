"""
Browser-reachable links to private media.

The bucket is never public. A link is one of:
- presigned: a direct, signed URL on the object store's public host (S3_PUBLIC_ENDPOINT_URL, or AWS S3 itself);
- proxy: a short-lived signed URL on this API (GET /api/v1/media/{token}) that streams the object. It works
  wherever the console can already reach the API, so no object-store port has to be exposed to officers.

Which one is used is decided by Settings.media_mode. A proxy token names the attachment and the user it was
issued to, never the storage key, and dies with the user's sessions (token_version) or after the TTL.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from fastapi import Depends, Request

from app.common.errors import Gone, NotFound
from app.common.time import utcnow
from app.config import Settings, get_settings
from app.models import CaseAttachment, User
from app.storage.s3 import ObjectStorage, get_storage

AUDIENCE = "kumbh-setu-media"
MEDIA_PATH = "/api/v1/media/"


@dataclass(frozen=True)
class MediaClaims:
    attachment_id: uuid.UUID
    user_id: uuid.UUID
    token_version: int


def _key(settings: Settings) -> bytes:
    # Derived so that a media token can never be accepted as an access token, and vice versa.
    return hmac.new(settings.jwt_secret.encode(), b"kumbh-setu/media-link/v1", hashlib.sha256).digest()


def issue_token(settings: Settings, attachment: CaseAttachment, user: User) -> str:
    now = utcnow()
    return jwt.encode(
        {
            "sub": str(attachment.id), "uid": str(user.id), "tv": user.token_version,
            "aud": AUDIENCE, "iss": settings.jwt_issuer, "iat": now,
            "exp": now + timedelta(seconds=settings.s3_presign_ttl_seconds),
        },
        _key(settings),
        algorithm="HS256",
    )


def verify_token(settings: Settings, token: str) -> MediaClaims:
    try:
        claims = jwt.decode(
            token, _key(settings), algorithms=["HS256"], audience=AUDIENCE, issuer=settings.jwt_issuer,
            options={"require": ["sub", "uid", "tv", "exp"]},
        )
        return MediaClaims(uuid.UUID(claims["sub"]), uuid.UUID(claims["uid"]), int(claims["tv"]))
    except jwt.ExpiredSignatureError as exc:
        raise Gone("This link has expired; request a new one", code="media_link_expired") from exc
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise NotFound("Link not found", code="media_link_invalid") from exc


class MediaLinks:
    """Builds the URL handed to the console. Constructed per request so proxy links use the right origin."""

    def __init__(self, storage: ObjectStorage, settings: Settings, request_base_url: str) -> None:
        self.storage = storage
        self.settings = settings
        self._request_base = request_base_url.rstrip("/")

    @property
    def ttl_seconds(self) -> int:
        return self.settings.s3_presign_ttl_seconds

    def url_for(self, attachment: CaseAttachment, actor: User) -> str:
        filename = attachment.original_filename or f"attachment-{attachment.id.hex}"
        if self.settings.media_mode == "presigned":
            return self.storage.presign_get(
                attachment.storage_key, content_type=attachment.content_type, filename=filename,
                ttl_seconds=self.ttl_seconds,
            )
        base = (self.settings.api_public_base_url or self._request_base).rstrip("/")
        return f"{base}{MEDIA_PATH}{issue_token(self.settings, attachment, actor)}"


def get_media_links(request: Request, storage: ObjectStorage = Depends(get_storage)) -> MediaLinks:
    return MediaLinks(storage, get_settings(), str(request.base_url))
