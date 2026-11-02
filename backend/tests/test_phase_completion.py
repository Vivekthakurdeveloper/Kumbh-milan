"""Coverage added for readiness, exports, handover, safeguarding, MFA, and reconciliation."""

import uuid
from datetime import timedelta

from sqlalchemy import text

from app.auth.mfa import decode_secret, totp
from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal
from app.database.revision import EXPECTED_SCHEMA_REVISION
from app.services.attachments import reconcile_objects
from app.workers.runtime import REQUIRED_WORKERS, record_heartbeat
from tests.helpers import API, FOUND_PERSON, create_case


def test_database_revision_matches_application(db):
    assert db.execute(text("SELECT version_num FROM alembic_version")).scalar() == EXPECTED_SCHEMA_REVISION


def test_readiness_rejects_schema_mismatch_without_leaking_details(client, monkeypatch):
    monkeypatch.setattr("app.api.health.EXPECTED_SCHEMA_REVISION", "not-this-build")
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["schema"] == "incompatible"
    assert "postgresql" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_readiness_requires_fresh_worker_heartbeats(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "workers_required", True)
    missing = client.get("/ready")
    assert missing.status_code == 503
    assert missing.json()["checks"]["worker_scan"] == "unavailable"
    for name in REQUIRED_WORKERS:
        record_heartbeat(name)
    ready = client.get("/ready")
    assert ready.status_code == 200, ready.text
    assert all(ready.json()["checks"][f"worker_{name}"] == "ok" for name in REQUIRED_WORKERS)


def test_case_export_is_scoped_bounded_and_audited(client, actors):
    own = create_case(client, actors.helpdesk, "missing-persons")
    other = create_case(client, actors.helpdesk_b, "missing-persons")
    start = (utcnow() - timedelta(days=1)).isoformat()
    end = (utcnow() + timedelta(days=1)).isoformat()
    wide = client.get(
        f"{API}/reports/cases.csv",
        params={"created_from": (utcnow() - timedelta(days=40)).isoformat(), "created_to": end},
        headers=actors.police.headers,
    )
    assert wide.status_code == 422 and wide.json()["error"]["code"] == "range_too_large"
    exported = client.get(
        f"{API}/reports/cases.csv", params={"created_from": start, "created_to": end}, headers=actors.helpdesk.headers,
    )
    assert exported.status_code == 200, exported.text
    assert own["case_number"] in exported.text
    assert other["case_number"] not in exported.text
    assert "contact" not in exported.text.lower()
    assert exported.headers["x-export-truncated"] == "false"
    audit = client.get(f"{API}/audit-events", params={"action": "CASE_EXPORTED"}, headers=actors.admin.headers)
    assert audit.status_code == 200 and audit.json()["total"] >= 1


def test_item_release_requires_acknowledgement(client, actors):
    item = create_case(client, actors.helpdesk, "found-items")
    url = f"{API}/cases/found-items/{item['id']}/custody"
    rejected = client.post(url, json={"custody_status": "RELEASED", "reason": "Owner verified"}, headers=actors.police.headers)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "handover_required"


def test_safeguarding_flag_requires_verification_evidence(client, actors):
    case = create_case(
        client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "safeguarding_review_required": True},
    )
    assign = client.post(
        f"{API}/cases/found-persons/{case['id']}/assign",
        json={"assignee_user_id": str(actors.police.id), "reason": "Safeguarding desk"},
        headers=actors.police.headers,
    )
    assert assign.status_code == 200, assign.text
    blocked = client.post(
        f"{API}/cases/found-persons/{case['id']}/status",
        json={"status": "RESOLVED", "reason": "Family arrived at the desk"},
        headers=actors.police.headers,
    )
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "safeguarding_evidence_required"
    resolved = client.post(
        f"{API}/cases/found-persons/{case['id']}/status",
        json={
            "status": "RESOLVED",
            "reason": "Family arrived at the desk",
            "verification_evidence": "Officer compared the guardian's photo identification at the desk.",
        },
        headers=actors.police.headers,
    )
    assert resolved.status_code == 200, resolved.text


def test_totp_step_up_gates_privileged_password_reset(client, actors, monkeypatch):
    monkeypatch.setattr(get_settings(), "mfa_mode", "totp")
    enrolled = client.post(f"{API}/auth/mfa/enroll", headers=actors.admin.headers)
    assert enrolled.status_code == 200, enrolled.text
    secret = decode_secret(enrolled.json()["secret"])
    assert client.post(
        f"{API}/auth/mfa/confirm", json={"code": totp(secret)}, headers=actors.admin.headers,
    ).status_code == 204
    payload = {"new_password": "Narmada#Bridge2027!"}
    blocked = client.post(
        f"{API}/users/{actors.helpdesk.id}/reset-password", json=payload, headers=actors.admin.headers,
    )
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "step_up_required"
    allowed = client.post(
        f"{API}/users/{actors.helpdesk.id}/reset-password",
        json=payload,
        headers={**actors.admin.headers, "X-Step-Up-Code": totp(secret)},
    )
    assert allowed.status_code == 204, allowed.text


def test_mock_provider_records_masked_delivery_and_can_fail():
    from app.models.enums import NotificationChannel
    from app.notifications import MockProvider, ProviderError

    provider = MockProvider()
    delivery = provider.send(channel=NotificationChannel.SMS, to="+919876543210", body="hello", idempotency_key="k" * 20)
    assert delivery.provider_message_id.startswith("mock-")
    assert "98765" not in provider.deliveries[0]["to"]
    provider.fail = ProviderError("temporary outage", permanent=False)
    try:
        provider.send(channel=NotificationChannel.SMS, to="+919876543210", body="hello", idempotency_key="k" * 20)
        raise AssertionError("expected provider failure")
    except ProviderError as exc:
        assert exc.permanent is False


def test_reconciliation_reports_missing_objects_without_deleting_rows(client, actors, storage):
    case = create_case(client, actors.helpdesk, "missing-persons")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    uploaded = client.post(
        f"{API}/cases/{case['id']}/attachments",
        data={"media_type": "PHOTO"},
        files={"file": ("photo.png", png, "image/png")},
        headers=actors.helpdesk.headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    with SessionLocal() as db:
        from app.models import CaseAttachment

        attachment = db.get(CaseAttachment, uuid.UUID(uploaded.json()["id"]))
        storage.delete(attachment.storage_key)
        report = reconcile_objects(db, storage)
        assert report["applied"] is False and report["missing_objects"] >= 1
        assert db.get(CaseAttachment, attachment.id) is not None
