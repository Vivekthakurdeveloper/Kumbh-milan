"""Outbound adapters for PA, LED/VMD, social, and government systems (BE-301).

Adapters receive payloads that are already classified as shareable. Live vendor delivery requires a named
destination, credentials, and an allowlisted host; until then destinations use the MOCK adapter.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib import error, request
from urllib.parse import urlparse

from app.common.errors import ValidationFailed
from app.config import get_settings

PHONE = re.compile(r"(?:\+?\d[\s-]?){7,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
REFERENCE = re.compile(r"\b[A-Z]{2,8}-(?:[A-Z]{2,4}-)?\d{4}-\d{6}\b")


def assert_public_safe(*texts: str | None) -> None:
    """Public channels never receive phone numbers, email addresses, or document-like digit runs."""
    for text in texts:
        if not text:
            continue
        text = REFERENCE.sub("", text)
        if EMAIL.search(text):
            raise ValidationFailed("Public messages must not contain email addresses", code="sensitive_content")
        if PHONE.search(text):
            raise ValidationFailed(
                "Public messages must not contain phone numbers or ID numbers", code="sensitive_content"
            )


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str | None, signature: str | None, body: bytes) -> bool:
    if not timestamp or not signature:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > get_settings().integration_signature_tolerance_seconds:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


class AdapterError(Exception):
    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True)
class AdapterResult:
    remote_reference: str | None


@dataclass(frozen=True)
class Target:
    code: str
    system: str
    endpoint_url: str | None
    secret: str | None
    timeout_seconds: int
    config: dict


class MockAdapter:
    """Records deliveries in memory. `config.mock_failure` = transient|permanent simulates vendor errors."""

    deliveries: ClassVar[list[dict[str, Any]]] = []

    def deliver(self, target: Target, *, operation: str, payload: dict, idempotency_key: str) -> AdapterResult:
        failure = target.config.get("mock_failure")
        if failure in ("transient", "permanent"):
            raise AdapterError(f"mock_{failure}", permanent=failure == "permanent")
        if not any(d["key"] == idempotency_key for d in self.deliveries):
            self.deliveries.append(
                {"destination": target.code, "operation": operation, "payload": payload, "key": idempotency_key}
            )
        return AdapterResult(remote_reference=f"mock-{idempotency_key[:32]}")


def _host_allowed(url: str) -> bool:
    settings = get_settings()
    host = (urlparse(url).hostname or "").lower()
    if not settings.integration_allowed_hosts:
        return settings.app_env in ("local", "test")
    return host in {h.lower() for h in settings.integration_allowed_hosts}


class WebhookAdapter:
    """Signed JSON POST. Receivers verify X-Kumbh-Signature = HMAC-SHA256(secret, timestamp + '.' + body)."""

    def deliver(self, target: Target, *, operation: str, payload: dict, idempotency_key: str) -> AdapterResult:
        if not target.endpoint_url or not _host_allowed(target.endpoint_url):
            raise AdapterError("destination_host_not_allowlisted", permanent=True)
        if not target.secret:
            raise AdapterError("destination_secret_missing", permanent=True)
        body = json.dumps({"operation": operation, "payload": payload}, default=str, sort_keys=True).encode()
        timestamp = str(int(time.time()))
        req = request.Request(
            target.endpoint_url,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-kumbh-idempotency-key": idempotency_key,
                "x-kumbh-timestamp": timestamp,
                "x-kumbh-signature": sign(target.secret, timestamp, body),
            },
        )
        try:
            with request.urlopen(req, timeout=target.timeout_seconds) as response:
                raw = response.read(65536).decode("utf-8", "replace")
        except error.HTTPError as exc:
            raise AdapterError(f"http_{exc.code}", permanent=exc.code in (400, 401, 403, 404, 409, 422)) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise AdapterError("unreachable", permanent=False) from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        reference = parsed.get("reference") or parsed.get("id") if isinstance(parsed, dict) else None
        return AdapterResult(remote_reference=str(reference)[:200] if reference else None)


ADAPTERS = {"MOCK": MockAdapter(), "WEBHOOK": WebhookAdapter()}


def adapter_for(name: str):
    return ADAPTERS[name]


GOVERNMENT_SYSTEMS = ("CCTNS", "ERSS_112", "TRACKCHILD", "KHOYA_PAYA", "GENERIC")
PUBLIC_SYSTEMS = ("PA_GENERIC", "VMD_GENERIC", "X", "FACEBOOK", "INSTAGRAM", "YOUTUBE", "GENERIC")
