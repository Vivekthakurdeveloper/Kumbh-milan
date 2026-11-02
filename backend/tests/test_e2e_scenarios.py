"""Repeatable backend scenarios. They use the disposable test database, not operational data."""

from sqlalchemy import text

from app.database import SessionLocal
from app.notifications import MockProvider, ProviderError
from app.services import notifications as notification_service
from tests.helpers import API, FOUND_ITEM, FOUND_PERSON, LOST_ITEM, MISSING_PERSON, create_case

EVIDENCE = "Officer compared the photo identification and recorded the guardian's acknowledgement."


def test_scenario_missing_person_review_and_audit(client, actors):
    created = create_case(client, actors.helpdesk, "missing-persons", MISSING_PERSON)
    assert client.get(f"{API}/cases/{created['id']}", headers=actors.helpdesk_b.headers).status_code == 404
    updated = client.patch(
        f"{API}/cases/missing-persons/{created['id']}",
        json={"clothing_description": "red kurta and blue shorts, white cap"},
        headers=actors.helpdesk.headers,
    )
    assert updated.status_code == 200, updated.text
    assigned = client.post(
        f"{API}/cases/missing-persons/{created['id']}/assign",
        json={"assignee_user_id": str(actors.police.id), "reason": "Sector duty"},
        headers=actors.police.headers,
    )
    assert assigned.status_code == 200 and assigned.json()["status"] == "ASSIGNED"
    found = create_case(client, actors.helpdesk, "found-persons", FOUND_PERSON)
    proposed = client.post(
        f"{API}/cases/{created['id']}/matches",
        json={"other_case_id": found["id"], "rationale": "Same clothing and age; review the photograph."},
        headers=actors.police.headers,
    )
    assert proposed.status_code == 201, proposed.text
    reviewed = client.post(
        f"{API}/cases/{created['id']}/matches/{proposed.json()['id']}/review",
        json={"decision": "CONFIRMED", "evidence": EVIDENCE},
        headers=actors.police2.headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert client.get(f"{API}/cases/{created['id']}", headers=actors.police.headers).json()["status"] == "MATCH_FOUND"
    history = client.get(f"{API}/cases/{created['id']}/history", headers=actors.police.headers).json()
    assert "ASSIGNED" in [row["to_status"] for row in history["status_history"]]


def test_scenario_found_person_safeguarding(client, actors):
    case = create_case(
        client, actors.helpdesk, "found-persons",
        {**FOUND_PERSON, "safeguarding_review_required": True, "communication_status": "NON_VERBAL"},
    )
    assert "contact_details" not in case
    client.post(
        f"{API}/cases/found-persons/{case['id']}/assign",
        json={"assignee_user_id": str(actors.police.id)},
        headers=actors.police.headers,
    )
    blocked = client.post(
        f"{API}/cases/found-persons/{case['id']}/status",
        json={"status": "RESOLVED", "reason": "Appears to know the adult"},
        headers=actors.police.headers,
    )
    assert blocked.json()["error"]["code"] == "safeguarding_evidence_required"
    resolved = client.post(
        f"{API}/cases/found-persons/{case['id']}/status",
        json={"status": "RESOLVED", "reason": "Guardian identified", "verification_evidence": EVIDENCE},
        headers=actors.police.headers,
    )
    assert resolved.status_code == 200, resolved.text


def test_scenario_lost_and_found_item_custody(client, actors):
    lost = create_case(client, actors.helpdesk, "lost-items", LOST_ITEM)
    found = create_case(client, actors.helpdesk, "found-items", FOUND_ITEM)
    match = client.post(
        f"{API}/cases/{lost['id']}/matches",
        json={"other_case_id": found["id"], "rationale": "Same bag colour and last location."},
        headers=actors.police.headers,
    )
    assert match.status_code == 201, match.text
    base = f"{API}/cases/found-items/{found['id']}/custody"
    moved = client.post(
        base,
        json={"custody_status": "TRANSFERRED", "reason": "Central store", "storage_location": "Central locker 4"},
        headers=actors.police.headers,
    )
    assert moved.status_code == 200, moved.text
    released = client.post(
        base,
        json={
            "custody_status": "RELEASED",
            "reason": "Owner described the contents",
            "recipient_acknowledgement": "Owner signed the desk register",
        },
        headers=actors.police.headers,
    )
    assert released.status_code == 200, released.text
    assert released.json()["details"]["handover_acknowledgement"] == "Owner signed the desk register"


def test_scenario_media_and_mock_notification(client, actors, storage, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "notification_retry_base_seconds", 0)
    case = create_case(client, actors.helpdesk, "missing-persons")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    uploaded = client.post(
        f"{API}/cases/{case['id']}/attachments",
        data={"media_type": "PHOTO"},
        files={"file": ("photo.png", png, "image/png")},
        headers=actors.helpdesk.headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    assert client.get(
        f"{API}/cases/{case['id']}/attachments/{uploaded.json()['id']}/download",
        headers=actors.helpdesk_b.headers,
    ).status_code == 404
    queued = client.post(
        f"{API}/cases/{case['id']}/notifications",
        json={
            "template": "CASE_REGISTERED",
            "recipient_phone": "9876543210",
            "consent_statement": "Father agreed verbally at desk 3",
        },
        headers=actors.helpdesk.headers,
    )
    assert queued.status_code == 202, queued.text
    provider = MockProvider()
    provider.fail = ProviderError("temporary outage", permanent=False)
    with SessionLocal() as db:
        first = notification_service.deliver_due(db, provider, limit=10)
        provider.fail = None
        second = notification_service.deliver_due(db, provider, limit=10)
    assert first == {"sent": 0, "retry": 1, "failed": 0}
    assert second == {"sent": 1, "retry": 0, "failed": 0}
    assert provider.deliveries and "98765" not in str(provider.deliveries)


def test_scenario_retention_hold_blocks_plan(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    closed = client.post(
        f"{API}/cases/missing-persons/{case['id']}/status",
        json={"status": "CLOSED", "reason": "Registered twice by mistake"},
        headers=actors.police.headers,
    )
    assert closed.status_code == 200, closed.text
    hold = client.post(
        f"{API}/cases/{case['id']}/legal-holds",
        json={"reason": "Inquiry remains open", "reference": "HOLD-1"},
        headers=actors.admin.headers,
    )
    assert hold.status_code == 201, hold.text
    with SessionLocal() as db:
        db.execute(
            text("UPDATE cases SET closed_at = now() - make_interval(days => 200) WHERE id = :id"),
            {"id": case["id"]},
        )
        db.commit()
    run = client.post(f"{API}/retention/runs", json={"policy": "ARCHIVE_CLOSED_CASES"}, headers=actors.admin.headers)
    assert run.status_code == 201, run.text
    assert run.json()["plan_report"]["excluded_by_legal_hold"] >= 1
    assert case["id"] not in run.json()["plan_report"].get("items", [])
