"""Proxy delivery of private media. The signed token in the path is the credential (see app.storage.media)."""

import re
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.api.v1.responses import errors
from app.auth.dependencies import DbSession
from app.common.errors import NotFound
from app.config import get_settings
from app.models import CaseAttachment, User
from app.services.attachments import is_downloadable
from app.storage import ObjectStorage, get_storage
from app.storage.media import verify_token

router = APIRouter(prefix="/media", tags=["Media"])

_RANGE = re.compile(r"^bytes=(\d+-\d*|-\d+)$")
_INLINE_TYPES = ("image/", "audio/")


@router.get(
    "/{token}",
    responses={
        200: {"description": "The file", "content": {"application/octet-stream": {}}},
        206: {"description": "Part of the file"},
        **errors(404, 410, 416),
    },
    summary="Fetch a file through a short-lived signed link",
)
def get_media(
    token: str,
    db: DbSession,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    """
    No Authorization header is needed, so the URL can be used directly as an `<img src>` or `<audio src>`.
    Links come from GET /matches/{id}/photos/{side} and GET /cases/{id}/attachments/{aid}, last
    S3_PRESIGN_TTL_SECONDS (default 300), and stop working when the user's sessions are revoked.
    """
    claims = verify_token(get_settings(), token)
    user = db.get(User, claims.user_id)
    attachment = db.get(CaseAttachment, claims.attachment_id)
    unavailable = NotFound("Link not found", code="media_link_invalid")
    if user is None or not user.is_active or user.token_version != claims.token_version:
        raise unavailable
    if (
        attachment is None
        or attachment.deleted_at is not None
        or attachment.object_purged_at is not None
        or not is_downloadable(attachment)
    ):
        raise unavailable

    byte_range = range_header if range_header and _RANGE.match(range_header) else None
    stream = storage.open(attachment.storage_key, byte_range=byte_range)
    filename = attachment.original_filename or f"attachment-{attachment.id.hex}"
    inline = attachment.content_type.startswith(_INLINE_TYPES)
    headers = {
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename)}",
        "Content-Length": str(stream.content_length),
        "Accept-Ranges": "bytes",
    }
    if stream.content_range:
        headers["Content-Range"] = stream.content_range
    return StreamingResponse(
        stream.chunks(), status_code=206 if stream.partial else 200,
        media_type=attachment.content_type, headers=headers,
    )
