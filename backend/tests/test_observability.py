import json
import logging

from app.config import get_settings
from app.observability.logging import JsonFormatter
from tests.helpers import API, create_case


def _metric(text: str, name: str, **labels) -> float:
    want = ",".join(f'{k}="{v}"' for k, v in labels.items())
    for line in text.splitlines():
        if line.startswith(f"{name}{{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
        if not labels and line.startswith(f"{name} "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{name}{{{want}}} not exported")


def test_metrics_require_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "s" * 32)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": f"Bearer {'s' * 32}"})
    assert ok.status_code == 200 and "kumbh_http_requests_total" in ok.text


def test_domain_metrics_and_route_templates(client, actors):
    before = client.get("/metrics").text
    created_before = _metric(before, "kumbh_cases_created_total", case_type="FOUND_PERSON") if "kumbh_cases_created_total{" in before else 0
    case = create_case(client, actors.helpdesk, "found-persons")
    client.post(f"{API}/auth/login", json={"username": "nobody", "password": "wrong-password-123"})
    client.get(f"{API}/cases/{case['id']}", headers=actors.police.headers)

    text = client.get("/metrics").text
    assert _metric(text, "kumbh_cases_created_total", case_type="FOUND_PERSON") == created_before + 1
    assert _metric(text, "kumbh_auth_failures_total", reason="bad_credentials") >= 1
    # Paths are labelled by template, never by the concrete id.
    assert case["id"] not in text
    assert 'route="/api/v1/cases/{case_id}"' in text
    assert _metric(text, "kumbh_database_up") == 1


def test_rolled_back_transition_is_not_counted(client, actors, monkeypatch):
    import pytest

    from app.database import SessionLocal
    from app.models import User
    from app.schemas.workflow import StatusChangeRequest
    from app.services import workflow
    from app.services.case_types import FOUND_PERSON

    case = create_case(client, actors.helpdesk, "found-persons")
    before = client.get("/metrics").text.count('to_status="UNDER_REVIEW"')

    def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(workflow, "record_audit", boom)
    with SessionLocal() as db, pytest.raises(RuntimeError):
        workflow.change_status(
            db, db.get(User, actors.police.id), FOUND_PERSON, case["id"],
            StatusChangeRequest(status="UNDER_REVIEW", reason="checking the desk records"),
        )
    monkeypatch.undo()
    assert client.get("/metrics").text.count('to_status="UNDER_REVIEW"') == before


def test_readiness_reports_unreachable_rate_limiter(client, monkeypatch):
    from app.api import health

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:1/0")
    health._redis.cache_clear()
    try:
        r = client.get("/ready")
    finally:
        health._redis.cache_clear()
    assert r.status_code == 503
    body = r.json()
    assert body["checks"]["rate_limiter"] == "unavailable" and body["checks"]["database"] == "ok"
    assert body["informational"]["malware_scanner"] == "disabled_development_mode"


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def test_access_log_has_no_query_string_or_ip(client, actors):
    collector = _Collect()
    access = logging.getLogger("kumbh_setu.access")
    access.addHandler(collector)
    try:
        client.get(f"{API}/cases?q=Sunita+Devi", headers=actors.police.headers)
    finally:
        access.removeHandler(collector)
    records = collector.records
    assert records
    line = JsonFormatter().format(records[-1])
    assert "Sunita" not in line and "q=" not in line
    payload = json.loads(line)
    assert payload["route"] == "/api/v1/cases" and payload["status"] == 200
    assert "client_ip" not in payload
