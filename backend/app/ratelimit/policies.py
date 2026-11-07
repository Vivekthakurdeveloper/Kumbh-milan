"""
Rate-limit policies.

Keys: abuse-prone unauthenticated endpoints (login, refresh) are limited per client IP; authenticated endpoints
are limited per user, so officers sharing one NAT address at a control room do not throttle each other.

Failure policy when the shared store is unavailable:
- fail_closed (login, refresh, password change, forgot/reset password): reject with 503. Failing open would remove brute-force
  protection exactly when an attacker might be overloading the store.
- otherwise: fall back to this replica's in-memory limiter (degraded, per-process) and record the error.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.config import Settings

KeyBy = Literal["ip", "user_or_ip"]


@dataclass(frozen=True)
class Policy:
    name: str
    limit: int
    window_seconds: int
    key_by: KeyBy
    fail_closed: bool = False


@dataclass(frozen=True)
class _Rule:
    method: str
    pattern: re.Pattern[str]
    policy: Policy


EXEMPT_PREFIXES = ("/health", "/ready", "/metrics")


def build_rules(settings: Settings) -> tuple[list[_Rule], Policy]:
    s = settings
    login = Policy("login", s.rate_limit_login_per_minute, 60, "ip", fail_closed=True)
    refresh = Policy("refresh", s.rate_limit_refresh_per_minute, 60, "ip", fail_closed=True)
    password = Policy("password_change", s.rate_limit_password_per_hour, 3600, "user_or_ip", fail_closed=True)
    forgot = Policy("forgot_password", s.rate_limit_forgot_password_per_hour, 3600, "ip", fail_closed=True)
    reset = Policy("reset_password", s.rate_limit_reset_password_per_hour, 3600, "ip", fail_closed=True)
    upload = Policy("upload", s.rate_limit_upload_per_minute, 60, "user_or_ip")
    create = Policy("case_create", s.rate_limit_case_create_per_minute, 60, "user_or_ip")
    search = Policy("search", s.rate_limit_search_per_minute, 60, "user_or_ip")
    general = Policy("general", s.rate_limit_per_minute, 60, "user_or_ip")
    case_types = r"(missing-persons|found-persons|lost-items|found-items)"
    rules = [
        _Rule("POST", re.compile(r"^/api/v1/auth/login$"), login),
        _Rule("POST", re.compile(r"^/api/v1/auth/refresh$"), refresh),
        _Rule("POST", re.compile(r"^/api/v1/auth/change-password$"), password),
        _Rule("POST", re.compile(r"^/api/v1/auth/forgot-password$"), forgot),
        _Rule("POST", re.compile(r"^/api/v1/auth/reset-password$"), reset),
        _Rule("POST", re.compile(r"^/api/v1/users/[^/]+/reset-password$"), password),
        _Rule("POST", re.compile(r"^/api/v1/cases/[^/]+/attachments$"), upload),
        _Rule("POST", re.compile(rf"^/api/v1/cases/{case_types}$"), create),
        _Rule("POST", re.compile(r"^/api/v1/sync/push$"), create),
        _Rule("GET", re.compile(rf"^/api/v1/cases(/{case_types}|/nearby|/map)?$"), search),
        _Rule("GET", re.compile(r"^/api/v1/sync/pull$"), search),
    ]
    return rules, general


def policies_for(rules: list[_Rule], general: Policy, method: str, path: str) -> list[Policy]:
    """Every API request counts against `general`; specific routes add their own policy on top."""
    if not path.startswith("/api/") or path.startswith(EXEMPT_PREFIXES):
        return []
    matched = [r.policy for r in rules if r.method == method and r.pattern.match(path)]
    return [*matched, general]
