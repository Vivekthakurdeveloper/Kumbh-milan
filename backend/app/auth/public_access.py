"""Default-deny route policy for Role.PUBLIC bearer tokens."""

from __future__ import annotations

import re

from app.common.errors import Forbidden

# Auth lifecycle endpoints that PUBLIC may call (authenticated).
_AUTH_ALLOWED = {
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/change-password"),
}

_CASE_TYPES = r"(?:missing-persons|found-persons|lost-items|found-items)"
_UUID = r"[0-9a-fA-F-]{36}"

# Narrow case operations. Ownership is still enforced by case_visibility_clause.
_CASE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("POST", re.compile(rf"^/api/v1/cases/{_CASE_TYPES}$")),
    ("GET", re.compile(r"^/api/v1/cases$")),
    ("GET", re.compile(rf"^/api/v1/cases/{_UUID}$")),
    ("GET", re.compile(rf"^/api/v1/cases/{_UUID}/history$")),
    ("GET", re.compile(rf"^/api/v1/cases/{_UUID}/notes$")),
    ("POST", re.compile(rf"^/api/v1/cases/{_UUID}/notes$")),
    ("GET", re.compile(rf"^/api/v1/cases/{_UUID}/attachments$")),
    ("POST", re.compile(rf"^/api/v1/cases/{_UUID}/attachments$")),
]


def public_may_access(method: str, path: str) -> bool:
    key = (method.upper(), path.rstrip("/") or path)
    if key in _AUTH_ALLOWED or (method.upper(), path) in _AUTH_ALLOWED:
        return True
    # Normalise trailing slash variants for auth paths.
    normalised = path if path.startswith("/") else f"/{path}"
    if (method.upper(), normalised) in _AUTH_ALLOWED:
        return True
    for allowed_method, pattern in _CASE_PATTERNS:
        if method.upper() == allowed_method and pattern.match(normalised):
            return True
    return False


def enforce_public_route_access(method: str, path: str) -> None:
    if not public_may_access(method, path):
        raise Forbidden(
            "PUBLIC accounts cannot access this endpoint",
            code="permission_denied",
        )
