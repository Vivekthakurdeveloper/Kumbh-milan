import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.common.crypto import decrypt_text
from app.common.time import utcnow
from app.database import SessionLocal
from app.models import AuditEvent, Notification
from app.notifications import ConsoleProvider, Delivery, DisabledProvider, ProviderError, normalise_phone, render
from app.services import notifications as svc
from tests.helpers import API, create_case

BODY = {"template": "CASE_REGISTERED", "recipient_phone": "98765 43210",
        "consent_statement": "Father Ramesh agreed verbally at desk 3"}


class RecordingProvider:
    name = "recording"
    enabled = True

    def __init__(self, failures: list[ProviderError | None] | None = None):
        self.failures = list(failures or [])
        self.sent: list[dict] = []

    def send(self, *, channel, to, body, idempotency_key):
        if self.failures:
            failure = self.failures.pop(0)
            if failure:
                raise failure
        self.sent.append({"channel": channel, "to": to, "body": body, "key": idempotency_key})
        return Delivery(provider_message_id=f"rec-{len(self.sent)}")


def _queue(client, actor, case_id, body=None, key=None):
    headers = {**actor.headers, **({"Idempotency-Key": key} if key else {})}
    return client.post(f"{API}/cases/{case_id}/notifications", json=body or BODY, headers=headers)


def test_queue_encrypts_recipient_masks_output_and_records_consent(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    r = _queue(client, actors.helpdesk, case["id"])
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "PENDING" and body["recipient_hint"] == "••••3210"
    assert "98765" not in r.text and "recipient_phone" not in body
    with SessionLocal() as db:
        row = db.get(Notification, uuid.UUID(body["id"]))
        assert decrypt_text(row.recipient_ciphertext) == "+919876543210"
        assert row.consent_statement == BODY["consent_statement"]
        audit = db.scalar(select(AuditEvent).where(AuditEvent.entity_id == row.id))
        assert audit.action == "NOTIFICATION_QUEUED" and "98765" not in str(audit.after_state)
    listed = client.get(f"{API}/cases/{case['id']}/notifications", headers=actors.police.headers).json()
    assert [n["id"] for n in listed] == [body["id"]] and "98765" not in str(listed)


def test_queue_validation_and_access(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    assert _queue(client, actors.helpdesk, case["id"], {**BODY, "recipient_phone": "12ab-cdef-99"}).json()["error"]["code"] == "invalid_phone"
    assert _queue(client, actors.helpdesk, case["id"], {k: v for k, v in BODY.items() if k != "consent_statement"}).status_code == 422
    assert _queue(client, actors.helpdesk_b, case["id"]).status_code == 404
    assert client.post(f"{API}/cases/{case['id']}/notifications", json=BODY).status_code == 401


def test_queue_is_idempotent(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    first = _queue(client, actors.helpdesk, case["id"], key="sms-retry-0001")
    again = _queue(client, actors.helpdesk, case["id"], key="sms-retry-0001")
    assert first.json()["id"] == again.json()["id"] and again.headers.get("Idempotent-Replayed") == "true"
    other_case = create_case(client, actors.helpdesk, "missing-persons")
    elsewhere = _queue(client, actors.helpdesk, other_case["id"], key="sms-retry-0001")
    assert elsewhere.json()["id"] != first.json()["id"]


def test_templates_carry_no_personal_details():
    for template in ("CASE_REGISTERED", "CASE_UPDATE_VISIT_DESK"):
        for locale in ("en", "hi"):
            text = render(template, locale, case_number="KS-MP-2027-000123", station="Har Ki Pauri desk")
            assert "KS-MP-2027-000123" in text and "Har Ki Pauri desk" in text
            assert "{" not in text


def test_phone_normalisation():
    assert normalise_phone("098765 43210", "+91") == "+919876543210"
    assert normalise_phone("+44 20 7946 0958", "+91") == "+442079460958"
    assert normalise_phone("0091-98765-43210", "+91") == "+919876543210"
    assert normalise_phone("12", "+91") is None


def test_delivery_sends_rendered_template_once(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    notification_id = _queue(client, actors.helpdesk, case["id"]).json()["id"]
    provider = RecordingProvider()
    with SessionLocal() as db:
        assert svc.deliver_due(db, provider) == {"sent": 1, "retry": 0, "failed": 0}
        assert svc.deliver_due(db, provider) == {"sent": 0, "retry": 0, "failed": 0}
    [sent] = provider.sent
    assert sent["to"] == "+919876543210" and case["case_number"] in sent["body"] and sent["key"] == notification_id
    assert "Raju" not in sent["body"] and "kurta" not in sent["body"]
    with SessionLocal() as db:
        row = db.get(Notification, uuid.UUID(notification_id))
        assert row.status == "SENT" and row.provider_message_id == "rec-1" and row.attempts == 1


def test_transient_failures_retry_with_backoff_then_fail(client, actors, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "notification_max_attempts", 2)
    case = create_case(client, actors.helpdesk, "missing-persons")
    notification_id = uuid.UUID(_queue(client, actors.helpdesk, case["id"]).json()["id"])
    provider = RecordingProvider([ProviderError("timeout", permanent=False), ProviderError("timeout", permanent=False)])
    with SessionLocal() as db:
        assert svc.deliver_due(db, provider)["retry"] == 1
        row = db.get(Notification, notification_id, populate_existing=True)
        assert row.status == "PENDING" and row.next_attempt_at > utcnow()
        assert svc.deliver_due(db, provider)["retry"] == 0  # not due yet
        db.execute(update(Notification).values(next_attempt_at=func.now() - timedelta(seconds=1)))
        db.commit()
        assert svc.deliver_due(db, provider)["failed"] == 1
        assert db.get(Notification, notification_id, populate_existing=True).status == "FAILED"
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.entity_id == notification_id, AuditEvent.action == "NOTIFICATION_FAILED"))
    assert provider.sent == []


def test_permanent_failure_stops_immediately(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    _queue(client, actors.helpdesk, case["id"])
    with SessionLocal() as db:
        assert svc.deliver_due(db, RecordingProvider([ProviderError("invalid number", permanent=True)]))["failed"] == 1


def test_disabled_provider_sends_nothing(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    nid = uuid.UUID(_queue(client, actors.helpdesk, case["id"]).json()["id"])
    with SessionLocal() as db:
        assert svc.deliver_due(db, DisabledProvider()) == {"sent": 0, "retry": 0, "failed": 0}
        row = db.get(Notification, nid)
        assert row.status == "PENDING" and row.attempts == 0


def test_console_provider_masks_recipient(client, actors):
    import logging

    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("kumbh_setu.notifications")
    handler = Collect()
    logger.addHandler(handler)
    try:
        ConsoleProvider().send(channel="SMS", to="+919876543210", body="hello", idempotency_key="k")
    finally:
        logger.removeHandler(handler)
    assert records and records[0].to == "••••3210"


def test_console_provider_refused_in_production():
    from pydantic import ValidationError

    from app.config import Settings
    from tests.test_platform import PRODUCTION_OK

    with pytest.raises(ValidationError):
        Settings(**{**PRODUCTION_OK, "notification_provider": "console"})


def test_dashboard_summary_is_scoped(client, actors):
    create_case(client, actors.helpdesk, "missing-persons")
    create_case(client, actors.helpdesk, "found-items")
    create_case(client, actors.helpdesk_b, "lost-items")
    own = client.get(f"{API}/dashboard/summary", headers=actors.helpdesk.headers)
    assert own.status_code == 200, own.text
    body = own.json()
    assert body["active_total"] == 2 and body["active_by_type"] == {"MISSING_PERSON": 1, "FOUND_ITEM": 1}
    assert body["urgent_active"] == 1 and body["unassigned_active"] == 2 and body["registered_today"] == 2
    assert client.get(f"{API}/dashboard/summary", headers=actors.admin.headers).json()["active_total"] == 3
    assert client.get(f"{API}/dashboard/summary").status_code == 401
