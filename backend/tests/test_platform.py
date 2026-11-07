import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.middleware import RequestGuardMiddleware
from app.config import Settings, get_settings
from tests.helpers import API


def test_openapi_documents_every_operation(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"]
    paths = spec["paths"]
    for kind in ("missing-persons", "found-persons", "lost-items", "found-items"):
        assert {"get", "post"} <= set(paths[f"{API}/cases/{kind}"])
        assert {"get", "patch"} <= set(paths[f"{API}/cases/{kind}/{{case_id}}"])
        assert "post" in paths[f"{API}/cases/{kind}/{{case_id}}/assign"]
        assert "post" in paths[f"{API}/cases/{kind}/{{case_id}}/status"]
    # Credentials-by-body or credentials-in-path endpoints: the token or code is the credential.
    public = {
        f"{API}/auth/login", f"{API}/auth/refresh", f"{API}/auth/forgot-password", f"{API}/auth/reset-password",
        f"{API}/media/{{token}}",
    }
    for path, ops in paths.items():
        if not path.startswith(API) or path in public:
            continue
        for method, op in ops.items():
            assert op.get("security"), f"{method.upper()} {path} is missing a security requirement"
            assert "401" in op["responses"], f"{method.upper()} {path} does not document 401"


def test_health_and_readiness(client):
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"


def test_security_headers_and_request_id(client, actors):
    r = client.get(f"{API}/auth/me", headers={**actors.police.headers, "X-Request-ID": "trace-abc-12345"})
    assert r.headers["X-Request-ID"] == "trace-abc-12345"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    bad = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert bad.headers["X-Request-ID"] != "bad id\nwith newline"


def test_error_envelope_is_consistent(client, actors):
    r = client.get(f"{API}/cases/not-a-uuid", headers=actors.police.headers)
    assert r.status_code == 422
    assert set(r.json()["error"]) == {"code", "message", "request_id", "details"}
    assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"]
    nf = client.get(f"{API}/nope", headers=actors.police.headers)
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "not_found"


def test_request_body_limit(client):
    r = client.post(
        f"{API}/auth/login", content=b"x" * 10, headers={"Content-Length": str(get_settings().max_request_bytes + 1)}
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


PRODUCTION_OK = dict(
    app_env="production", jwt_secret="Zq8" * 16, contact_encryption_key="k", docs_enabled=False,
    rate_limit_backend="redis", redis_url="rediss://:x@redis.internal:6379/0", metrics_token="m" * 32,
    scan_mode="clamav", clamav_host="clamav.internal", cors_origins=["https://console.example.org"],
    workers_required=True, notification_provider="disabled", mfa_mode="disabled",
)


def test_production_settings_refuse_placeholder_secrets():
    with pytest.raises(ValidationError):
        Settings(**{**PRODUCTION_OK, "jwt_secret": "change-me-local-only-jwt-secret-0000000000"})
    with pytest.raises(ValidationError):
        Settings(**{**PRODUCTION_OK, "contact_encryption_key": ""})
    Settings(**PRODUCTION_OK)


@pytest.mark.parametrize(
    "override",
    [
        {"rate_limit_backend": "memory"},
        {"redis_url": None},
        {"metrics_token": "short"},
        {"metrics_token": None},
        {"scan_mode": "disabled"},
        {"clamav_host": None},
        {"cors_origins": ["*"]},
        {"workers_required": False},
        {"notification_provider": "console"},
        {"notification_provider": "mock"},
    ],
)
def test_production_settings_refuse_unsafe_configuration(override):
    with pytest.raises(ValidationError):
        Settings(**{**PRODUCTION_OK, **override})
