"""Media type detection from file signatures. Client-supplied names and MIME types are never trusted."""

import re
import unicodedata
from dataclasses import dataclass

from app.models.enums import MediaType

_M4A_BRANDS = {b"M4A ", b"M4B ", b"mp42", b"isom"}


@dataclass(frozen=True)
class DetectedMedia:
    media_type: MediaType
    content_type: str
    extension: str


def detect_media(head: bytes) -> DetectedMedia | None:
    if head.startswith(b"\xff\xd8\xff"):
        return DetectedMedia(MediaType.PHOTO, "image/jpeg", "jpg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedMedia(MediaType.PHOTO, "image/png", "png")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return DetectedMedia(MediaType.PHOTO, "image/webp", "webp")
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return DetectedMedia(MediaType.AUDIO, "audio/wav", "wav")
    if head.startswith(b"OggS"):
        return DetectedMedia(MediaType.AUDIO, "audio/ogg", "ogg")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return DetectedMedia(MediaType.AUDIO, "audio/webm", "webm")
    if head[4:8] == b"ftyp" and head[8:12] in _M4A_BRANDS:
        return DetectedMedia(MediaType.AUDIO, "audio/mp4", "m4a")
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return DetectedMedia(MediaType.AUDIO, "audio/mpeg", "mp3")
    return None


def declared_type_conflicts(declared: str | None, detected: DetectedMedia) -> bool:
    if not declared or declared in ("application/octet-stream", "binary/octet-stream"):
        return False
    family = declared.split("/", 1)[0].lower()
    expected = "image" if detected.media_type is MediaType.PHOTO else "audio"
    return family != expected and not (expected == "audio" and declared.lower() in ("video/webm", "video/mp4"))


_UNSAFE = re.compile(r"[^\w.\- ()]+", re.UNICODE)


def safe_display_filename(name: str | None, fallback_ext: str) -> str:
    """For display and download headers only; storage keys never contain client input."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")
    base = _UNSAFE.sub("_", base).strip(" .")[:200]
    return base or f"attachment.{fallback_ext}"
