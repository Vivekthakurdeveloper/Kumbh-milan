import ipaddress
import os
import uuid

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware import RequestGuardMiddleware
from app.config import Settings
from app.ratelimit import MemoryBackend, RateLimiter, RedisBackend
from app.ratelimit.client_ip import parse_networks, resolve_client_ip
from tests.conftest import _read_dotenv


def _redis_url() -> str | None:
    explicit = os.environ.get("TEST_REDIS_URL")
    if explicit:
        return explicit
    password = os.environ.get("REDIS_PASSWORD") or _read_dotenv().get("REDIS_PASSWORD")
    return f"redis://:{password}@localhost:6380/15" if password else None


def _redis_available(url: str | None) -> bool:
    if not url:
        return False
    try:
        redis.Redis.from_url(url, socket_connect_timeout=0.5).ping()
        return True
    except redis.RedisError:
        return False


REDIS_URL = _redis_url()
needs_redis = pytest.mark.skipif(
    not _redis_available(REDIS_URL), reason="Redis not reachable (docker compose up -d redis, or set TEST_REDIS_URL)"
)


def _settings(**overrides) -> Settings:
    base = dict(
        rate_limit_per_minute=1000, rate_limit_login_per_minute=3, rate_limit_upload_per_minute=2,
        rate_limit_search_per_minute=1000, rate_limit_case_create_per_minute=1000,
    )
    return Settings(**{**base, **overrides})


def _app(settings: Settings, limiter: RateLimiter) -> FastAPI:
    mini = FastAPI()
    mini.add_middleware(RequestGuardMiddleware, settings=settings, limiter=limiter)

    @mini.post("/api/v1/auth/login")
    def login():
        return {"ok": True}

    @mini.get("/api/v1/ping")
    def ping():
        return {"ok": True}

    return mini


# --- client address resolution -------------------------------------------------------------------


def test_forwarded_for_ignored_from_untrusted_peer():
    trusted = parse_networks(["10.0.0.0/8"])
    assert resolve_client_ip("203.0.113.9", "1.1.1.1", trusted) == "203.0.113.9"


def test_forwarded_for_walks_right_to_left_through_trusted_proxies():
    trusted = parse_networks(["10.0.0.0/8"])
    # Client-supplied spoof on the left, real client appended by the edge proxy, then an internal hop.
    assert resolve_client_ip("10.0.0.5", "6.6.6.6, 198.51.100.7, 10.0.1.1", trusted) == "198.51.100.7"


def test_forwarded_for_garbage_falls_back_to_peer():
    trusted = parse_networks(["10.0.0.0/8"])
    assert resolve_client_ip("10.0.0.5", "not-an-ip", trusted) == "10.0.0.5"
    assert ipaddress.ip_address(resolve_client_ip("10.0.0.5", "", trusted))


# --- policies ------------------------------------------------------------------------------------


def test_login_limited_per_ip_with_retry_after():
    settings = _settings()
    with TestClient(_app(settings, RateLimiter(settings, MemoryBackend()))) as c:
        codes = [c.post("/api/v1/auth/login").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        limited = c.post("/api/v1/auth/login")
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
        assert limited.json()["error"]["code"] == "too_many_requests"
        assert limited.json()["error"]["details"]["policy"] == "login"
        assert c.get("/api/v1/ping").status_code == 200  # other routes keep their own budget


def test_users_behind_one_nat_get_separate_budgets():
    import jwt as pyjwt

    from app.common.time import utcnow

    settings = _settings(rate_limit_per_minute=2)
    limiter = RateLimiter(settings, MemoryBackend())

    def token(sub: str) -> dict[str, str]:
        claims = {"sub": sub, "typ": "access", "iss": settings.jwt_issuer, "exp": int(utcnow().timestamp()) + 60}
        return {"Authorization": f"Bearer {pyjwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)}"}

    a, b = token(str(uuid.uuid4())), token(str(uuid.uuid4()))
    with TestClient(_app(settings, limiter)) as c:
        assert [c.get("/api/v1/ping", headers=a).status_code for _ in range(3)] == [200, 200, 429]
        assert c.get("/api/v1/ping", headers=b).status_code == 200


def test_forged_token_does_not_buy_a_fresh_budget():
    settings = _settings(rate_limit_per_minute=2)
    with TestClient(_app(settings, RateLimiter(settings, MemoryBackend()))) as c:
        codes = [c.get("/api/v1/ping", headers={"Authorization": f"Bearer forged-{i}"}).status_code for i in range(3)]
        assert codes == [200, 200, 429]


def test_spoofed_forwarded_for_does_not_bypass_login_limit():
    settings = _settings(trusted_proxies=[])
    with TestClient(_app(settings, RateLimiter(settings, MemoryBackend()))) as c:
        codes = [
            c.post("/api/v1/auth/login", headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(4)
        ]
        assert codes[-1] == 429


# --- shared Redis limiter ------------------------------------------------------------------------


@needs_redis
def test_two_app_instances_share_one_redis_budget():
    settings = _settings(rate_limit_backend="redis", redis_url=REDIS_URL)
    prefix = f"kumbh:test:{uuid.uuid4().hex}"
    first = RateLimiter(settings, RedisBackend(REDIS_URL, 500, prefix=prefix))
    second = RateLimiter(settings, RedisBackend(REDIS_URL, 500, prefix=prefix))
    with TestClient(_app(settings, first)) as a, TestClient(_app(settings, second)) as b:
        codes = [
            a.post("/api/v1/auth/login").status_code,
            b.post("/api/v1/auth/login").status_code,
            a.post("/api/v1/auth/login").status_code,
            b.post("/api/v1/auth/login").status_code,
        ]
    assert codes == [200, 200, 200, 429]


@needs_redis
def test_redis_limit_is_atomic_under_concurrency():
    from concurrent.futures import ThreadPoolExecutor

    backend = RedisBackend(REDIS_URL, 1000, prefix=f"kumbh:test:{uuid.uuid4().hex}")
    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(lambda _: backend.hit("burst", 25, 60).allowed, range(100)))
    assert sum(results) == 25


def test_auth_routes_fail_closed_when_redis_is_down():
    dead = "redis://localhost:1/0"
    settings = _settings(rate_limit_backend="redis", redis_url=dead)
    limiter = RateLimiter(settings, RedisBackend(dead, 100))
    with TestClient(_app(settings, limiter)) as c:
        r = c.post("/api/v1/auth/login")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "rate_limiter_unavailable"
        assert "Retry-After" in r.headers
        # Fail-open policies degrade to per-instance limits instead of taking the API down.
        assert c.get("/api/v1/ping").status_code == 200


def test_forgot_and_reset_password_have_their_own_fail_closed_ip_limits():
    from app.ratelimit.policies import build_rules, policies_for

    rules, general = build_rules(_settings(rate_limit_forgot_password_per_hour=4, rate_limit_reset_password_per_hour=9))
    forgot = policies_for(rules, general, "POST", "/api/v1/auth/forgot-password")[0]
    reset = policies_for(rules, general, "POST", "/api/v1/auth/reset-password")[0]
    assert (forgot.name, forgot.limit, forgot.window_seconds, forgot.key_by, forgot.fail_closed) == (
        "forgot_password", 4, 3600, "ip", True,
    )
    assert (reset.name, reset.limit, reset.key_by, reset.fail_closed) == ("reset_password", 9, "ip", True)
