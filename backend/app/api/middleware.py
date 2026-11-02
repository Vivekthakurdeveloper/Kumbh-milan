import json
import logging
import re
import time
import uuid

import jwt
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.context import client_ip_var, request_id_var, step_up_code_var
from app.config import Settings
from app.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.ratelimit import RateLimiter, build_limiter
from app.ratelimit.client_ip import parse_networks, resolve_client_ip

access_logger = logging.getLogger("kumbh_setu.access")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_STEP_UP_RE = re.compile(r"^[0-9]{6}$")
_DOCS_PATHS = ("/docs", "/openapi.json")
_UNMETERED = ("/metrics",)

_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'"
)


class RequestGuardMiddleware:
    """Request ids, trusted client address, body-size limit, shared rate limits, security headers, access log."""

    def __init__(self, app: ASGIApp, settings: Settings, limiter: RateLimiter | None = None) -> None:
        self.app = app
        self.settings = settings
        self.limiter = limiter or build_limiter(settings)
        self.trusted = parse_networks(settings.trusted_proxies)

    def _user_id(self, headers: Headers) -> str | None:
        """Identity for per-user limits only. Authorisation still happens in the route dependency."""
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        try:
            claims = jwt.decode(
                auth[7:].strip(), self.settings.jwt_secret, algorithms=[self.settings.jwt_algorithm],
                issuer=self.settings.jwt_issuer, options={"require": ["sub", "exp"]},
            )
        except jwt.PyJWTError:
            return None
        return str(claims.get("sub")) if claims.get("typ") == "access" else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        headers = Headers(scope=scope)
        incoming_id = headers.get("x-request-id", "")
        request_id = incoming_id if _REQUEST_ID_RE.match(incoming_id) else uuid.uuid4().hex
        peer = scope.get("client")
        client_ip = resolve_client_ip(peer[0] if peer else None, headers.get("x-forwarded-for"), self.trusted)
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET")
        rid_token = request_id_var.set(request_id)
        ip_token = client_ip_var.set(client_ip)
        raw_step = headers.get("x-step-up-code", "")
        step_token = step_up_code_var.set(raw_step if _STEP_UP_RE.match(raw_step) else None)
        status_holder = {"status": 500}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = request_id
                h["X-Content-Type-Options"] = "nosniff"
                h["X-Frame-Options"] = "DENY"
                h["Referrer-Policy"] = "no-referrer"
                h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                h["Cross-Origin-Opener-Policy"] = "same-origin"
                h["Content-Security-Policy"] = _DOCS_CSP if path.startswith(_DOCS_PATHS) else _API_CSP
                if path.startswith("/api/"):
                    h["Cache-Control"] = "no-store"
                if self.settings.app_env in ("staging", "production"):
                    h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
            await send(message)

        try:
            max_bytes = self.settings.max_request_bytes
            declared = headers.get("content-length")
            if declared is not None and (not declared.isdigit() or int(declared) > max_bytes):
                await _send_error(send_with_headers, 413, "payload_too_large", "Request body is too large", request_id)
                return

            verdict = await run_in_threadpool(
                self.limiter.check, method, path, client_ip=client_ip, user_id=self._user_id(headers)
            )
            if verdict.status == 429:
                await _send_error(
                    send_with_headers, 429, "too_many_requests", "Rate limit exceeded; retry later", request_id,
                    extra_headers={"retry-after": str(verdict.retry_after)}, details={"policy": verdict.policy},
                )
                return
            if verdict.status == 503:
                await _send_error(
                    send_with_headers, 503, "rate_limiter_unavailable",
                    "Temporarily unavailable; please retry shortly", request_id,
                    extra_headers={"retry-after": str(verdict.retry_after)},
                )
                return

            received = 0

            async def limited_receive() -> Message:
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > max_bytes:
                        raise HTTPException(status_code=413, detail="Request body is too large")
                return message

            await self.app(scope, limited_receive, send_with_headers)
        finally:
            if not path.startswith(_UNMETERED):
                route = scope.get("route")
                template = getattr(route, "path", None) or "unmatched"
                elapsed = time.perf_counter() - started
                status = status_holder["status"]
                HTTP_REQUESTS.labels(method, template, str(status)).inc()
                HTTP_LATENCY.labels(method, template).observe(elapsed)
                access_logger.info(
                    "request",
                    extra={"method": method, "route": template, "status": status, "duration_ms": round(elapsed * 1000, 1)},
                )
            request_id_var.reset(rid_token)
            client_ip_var.reset(ip_token)
            step_up_code_var.reset(step_token)


async def _send_error(
    send: Send, status: int, code: str, message: str, request_id: str,
    *, extra_headers: dict[str, str] | None = None, details: dict | None = None,
) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "request_id": request_id, "details": details}}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    for name, value in (extra_headers or {}).items():
        headers.append((name.encode(), value.encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
