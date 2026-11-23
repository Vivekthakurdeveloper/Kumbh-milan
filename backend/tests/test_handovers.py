import uuid

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, CaseHandover
from tests.helpers import API, create_case


def _confirmed_person_match(client, actors):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    found = create_case(client, actors.helpdesk, "found-persons")
    proposed = client.post(
        f"{API}/cases/{missing['id']}/matches",
        json={"other_case_id": found["id"], "rationale": "Claimant and case details align"},
        headers=actors.police.headers,
    )
    assert proposed.status_code == 201, proposed.text
    match = proposed.json()
    confirmed = client.post(
        f"{API}/cases/{missing['id']}/matches/{match['id']}/review",
        json={"decision": "CONFIRMED", "evidence": "Police compared identifying marks and family details"},
        headers=actors.police2.headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return missing, found, match


def _create_handover(client, actor, case_id, match_id, key="handover-create-001"):
    return client.post(
        f"{API}/cases/{case_id}/handovers",
        json={
            "match_id": match_id,
            "claimant_full_name": "Sample Parent",
            "claimant_relationship": "Father",
            "claimant_mobile_number": "9876543210",
            "claimant_address": "Sector 3 camp",
            "identity_document_type": "AADHAAR",
            "identity_document_reference": "XXXX-XXXX-1234",
            "witness_full_name": "Sample Witness",
            "witness_relationship": "Family friend",
            "witness_statement": "Witnessed claimant verification at the police desk",
        },
        headers={**actor.headers, "Idempotency-Key": key},
    )


def test_structured_person_handover_gates_resolution_when_enabled(client, actors, monkeypatch):
    missing, found, match = _confirmed_person_match(client, actors)
    monkeypatch.setattr(get_settings(), "handover_required_for_resolve", True)

    blocked = client.post(
        f"{API}/cases/missing-persons/{missing['id']}/status",
        json={
            "status": "RESOLVED",
            "reason": "Family reunited",
            "match_id": match["id"],
            "verification_evidence": "Claimant identity checked by police",
        },
        headers=actors.police.headers,
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "handover_not_completed"

    created = _create_handover(client, actors.police, missing["id"], match["id"])
    assert created.status_code == 201, created.text
    handover = created.json()
    assert handover["status"] == "CLAIMANT_RECORDED"
    assert handover["counterpart_case_id"] == found["id"]
    assert handover["claimant_full_name"] == "Sample Parent"
    with SessionLocal() as db:
        stored = db.get(CaseHandover, uuid.UUID(handover["id"]))
        assert "Sample Parent" not in stored.claimant_ciphertext
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "case_handover",
                    AuditEvent.entity_id == stored.id,
                )
            )
        )
        assert events
        assert "Sample Parent" not in str(events)
        assert "XXXX-XXXX-1234" not in str(events)

    replay = _create_handover(client, actors.police, missing["id"], match["id"])
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == handover["id"]

    assert client.get(
        f"{API}/cases/{missing['id']}/handovers",
        headers=actors.helpdesk.headers,
    ).status_code == 403

    verified = client.post(
        f"{API}/cases/{missing['id']}/handovers/{handover['id']}/verify",
        json={
            "decision": "VERIFIED",
            "verification_method": "GOVERNMENT_ID",
            "evidence": "Aadhaar reference and family answers matched the case record",
        },
        headers={**actors.police2.headers, "Idempotency-Key": "handover-verify-001"},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED"

    completed = client.post(
        f"{API}/cases/{missing['id']}/handovers/{handover['id']}/complete",
        json={
            "handover_location": "Har Ki Pauri police help desk",
            "handover_at": "2026-11-07T13:00:00+05:30",
            "acknowledgement": "Claimant and witness signed the handover register",
        },
        headers={**actors.police.headers, "Idempotency-Key": "handover-complete-001"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    completed_replay = client.post(
        f"{API}/cases/{missing['id']}/handovers/{handover['id']}/complete",
        json={
            "handover_location": "Har Ki Pauri police help desk",
            "handover_at": "2026-11-07T13:00:00+05:30",
            "acknowledgement": "Claimant and witness signed the handover register",
        },
        headers={**actors.police.headers, "Idempotency-Key": "handover-complete-001"},
    )
    assert completed_replay.status_code == 200
    assert completed_replay.headers["Idempotent-Replayed"] == "true"

    counterpart = client.get(
        f"{API}/cases/{found['id']}/handovers/{handover['id']}",
        headers=actors.police.headers,
    )
    assert counterpart.status_code == 200
    assert counterpart.json()["primary_case_id"] == missing["id"]

    history = client.get(
        f"{API}/cases/{found['id']}/handovers/{handover['id']}/history",
        headers=actors.police.headers,
    )
    assert [event["action"] for event in history.json()] == [
        "HANDOVER_CREATED",
        "HANDOVER_VERIFIED",
        "HANDOVER_COMPLETED",
    ]

    duplicate = _create_handover(
        client,
        actors.police,
        missing["id"],
        match["id"],
        key="handover-create-duplicate",
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "handover_already_completed"

    for slug, case_id in (("missing-persons", missing["id"]), ("found-persons", found["id"])):
        resolved = client.post(
            f"{API}/cases/{slug}/{case_id}/status",
            json={
                "status": "RESOLVED",
                "reason": "Structured reunification completed",
                "match_id": match["id"],
                "verification_evidence": "Claimant verification and signed handover recorded",
            },
            headers=actors.police.headers,
        )
        assert resolved.status_code == 200, resolved.text


def test_handover_rejection_is_terminal_and_new_session_can_start(client, actors):
    missing, _, match = _confirmed_person_match(client, actors)
    created = _create_handover(client, actors.police, missing["id"], match["id"], key="handover-reject-create")
    handover_id = created.json()["id"]
    rejected = client.post(
        f"{API}/cases/{missing['id']}/handovers/{handover_id}/verify",
        json={
            "decision": "REJECTED",
            "evidence": "Claimant answers and document reference did not match the case",
        },
        headers=actors.police.headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["rejection_reason"]

    complete = client.post(
        f"{API}/cases/{missing['id']}/handovers/{handover_id}/complete",
        json={
            "handover_location": "Har Ki Pauri police help desk",
            "handover_at": "2026-11-07T13:00:00+05:30",
            "acknowledgement": "This completion must be rejected because verification failed",
        },
        headers=actors.police.headers,
    )
    assert complete.status_code == 409
    assert complete.json()["error"]["code"] == "handover_state"

    replacement = _create_handover(
        client,
        actors.police,
        missing["id"],
        match["id"],
        key="handover-replacement-create",
    )
    assert replacement.status_code == 201
    assert replacement.json()["id"] != handover_id


def test_matchless_found_item_handover_requires_custody_release(client, actors):
    found = create_case(client, actors.helpdesk, "found-items")
    created = _create_handover(
        client,
        actors.police,
        found["id"],
        None,
        key="item-handover-create",
    )
    assert created.status_code == 201, created.text
    handover_id = created.json()["id"]
    verified = client.post(
        f"{API}/cases/{found['id']}/handovers/{handover_id}/verify",
        json={
            "decision": "VERIFIED",
            "verification_method": "PHOTO_OR_MARKS",
            "evidence": "Owner described the unique damage and contents",
        },
        headers=actors.police.headers,
    )
    assert verified.status_code == 200

    completion_payload = {
        "handover_location": "Har Ki Pauri help desk",
        "handover_at": "2026-11-07T13:00:00+05:30",
        "acknowledgement": "Owner signed the item return register",
    }
    blocked = client.post(
        f"{API}/cases/{found['id']}/handovers/{handover_id}/complete",
        json=completion_payload,
        headers=actors.police.headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "custody_not_released"

    released = client.post(
        f"{API}/cases/found-items/{found['id']}/custody",
        json={
            "custody_status": "RELEASED",
            "reason": "Verified owner collected item",
            "recipient_acknowledgement": "Owner signed the custody register",
        },
        headers=actors.police.headers,
    )
    assert released.status_code == 200
    completed = client.post(
        f"{API}/cases/{found['id']}/handovers/{handover_id}/complete",
        json=completion_payload,
        headers=actors.police.headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"
