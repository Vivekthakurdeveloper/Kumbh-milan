import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent
from tests.helpers import API, create_case


def _status(client, actor, kind, case_id, **body):
    return client.post(f"{API}/cases/{kind}/{case_id}/status", json=body, headers=actor.headers)


def test_assignment_and_reassignment_preserve_history(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    url = f"{API}/cases/missing-persons/{case['id']}/assign"
    first = client.post(url, json={"assignee_user_id": str(actors.police.id), "reason": "On duty"}, headers=actors.police.headers)
    assert first.status_code == 200
    assert first.json()["status"] == "ASSIGNED"
    assert first.json()["assigned_to_user_id"] == str(actors.police.id)

    dup = client.post(url, json={"assignee_user_id": str(actors.police.id)}, headers=actors.police.headers)
    assert dup.status_code == 409

    second = client.post(url, json={"assignee_user_id": str(actors.police2.id), "reason": "Shift change"}, headers=actors.police.headers)
    assert second.json()["assigned_to_user_id"] == str(actors.police2.id)

    history = client.get(f"{API}/cases/{case['id']}/history", headers=actors.police.headers).json()
    assignments = history["assignments"]
    assert [a["is_active"] for a in assignments] == [False, True]
    assert assignments[0]["unassigned_at"] is not None
    assert [s["to_status"] for s in history["status_history"]] == ["OPEN", "ASSIGNED"]


def test_invalid_transitions_are_rejected_with_allowed_list(client, actors):
    case = create_case(client, actors.helpdesk, "lost-items")
    r = _status(client, actors.police, "lost-items", case["id"], status="RESOLVED", reason="Returned")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_status_transition"
    assert set(r.json()["error"]["details"]["allowed"]) == {"UNDER_REVIEW", "ASSIGNED", "CLOSED"}
    r = _status(client, actors.police, "lost-items", case["id"], status="ASSIGNED", reason="No assignee yet")
    assert r.status_code == 409 and r.json()["error"]["code"] == "assignee_required"


def test_close_and_reopen_track_closed_at(client, actors):
    case = create_case(client, actors.helpdesk, "lost-items")
    closed = _status(client, actors.police, "lost-items", case["id"], status="CLOSED", reason="Owner withdrew report")
    assert closed.json()["status"] == "CLOSED" and closed.json()["closed_at"]
    reopened = _status(client, actors.police, "lost-items", case["id"], status="REOPENED", reason="Owner came back")
    assert reopened.json()["status"] == "REOPENED" and reopened.json()["closed_at"] is None


def test_note_corrections_are_append_only(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    url = f"{API}/cases/{case['id']}/notes"
    original = client.post(url, json={"note_text": "Mother called"}, headers=actors.helpdesk.headers).json()
    bad = client.post(url, json={"note_text": "fix", "note_type": "CORRECTION"}, headers=actors.helpdesk.headers)
    assert bad.status_code == 422
    fix = client.post(
        url, json={"note_text": "It was the aunt", "note_type": "CORRECTION", "corrects_note_id": original["id"]},
        headers=actors.helpdesk.headers,
    )
    assert fix.status_code == 201 and fix.json()["corrects_note_id"] == original["id"]
    notes = client.get(url, headers=actors.helpdesk.headers).json()["items"]
    assert [n["note_text"] for n in notes] == ["Mother called", "It was the aunt"]
    other = create_case(client, actors.helpdesk, "missing-persons")
    cross = client.post(
        f"{API}/cases/{other['id']}/notes",
        json={"note_text": "x", "note_type": "CORRECTION", "corrects_note_id": original["id"]},
        headers=actors.helpdesk.headers,
    )
    assert cross.status_code == 404


def test_correcting_a_sensitive_note_stays_sensitive(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    url = f"{API}/cases/{case['id']}/notes"
    secret = client.post(url, json={"note_text": "Address 12 Gali", "is_sensitive": True}, headers=actors.police.headers).json()
    fix = client.post(
        url, json={"note_text": "Address 14 Gali", "note_type": "CORRECTION", "corrects_note_id": secret["id"]},
        headers=actors.police.headers,
    )
    assert fix.json()["is_sensitive"] is True
    assert client.get(url, headers=actors.helpdesk.headers).json()["total"] == 0


def test_person_reunification_requires_human_confirmed_match(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk, "found-persons")
    client.post(f"{API}/cases/missing-persons/{mp['id']}/assign", json={"assignee_user_id": str(actors.police.id)}, headers=actors.police.headers)

    proposed = client.post(
        f"{API}/cases/{mp['id']}/matches", json={"other_case_id": fp["id"], "rationale": "Same age, red kurta"},
        headers=actors.helpdesk.headers,
    ).json()
    assert proposed["status"] == "PROPOSED" and proposed["left_case_id"] == mp["id"]

    early = _status(client, actors.police, "missing-persons", mp["id"], status="RESOLVED", reason="Found", match_id=proposed["id"])
    assert early.status_code == 422 and early.json()["error"]["code"] == "match_not_confirmed"

    by_desk = client.post(
        f"{API}/cases/{mp['id']}/matches/{proposed['id']}/review",
        json={"decision": "CONFIRMED", "evidence": "Mother identified him"}, headers=actors.helpdesk.headers,
    )
    assert by_desk.status_code == 403

    confirmed = client.post(
        f"{API}/cases/{fp['id']}/matches/{proposed['id']}/review",
        json={"decision": "CONFIRMED", "evidence": "Mother identified child in person; ID checked"},
        headers=actors.police.headers,
    )
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "CONFIRMED"
    assert client.get(f"{API}/cases/{mp['id']}", headers=actors.police.headers).json()["status"] == "MATCH_FOUND"

    again = client.post(
        f"{API}/cases/{fp['id']}/matches/{proposed['id']}/review",
        json={"decision": "REJECTED", "evidence": "changed my mind here"}, headers=actors.police2.headers,
    )
    assert again.status_code == 409

    resolved = _status(client, actors.police, "missing-persons", mp["id"], status="RESOLVED", reason="Reunited", match_id=proposed["id"])
    assert resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED"
    history = client.get(f"{API}/cases/{mp['id']}/history", headers=actors.police.headers).json()["status_history"]
    assert history[-1]["match_id"] == proposed["id"]


def test_proposer_cannot_confirm_own_match(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk, "found-persons")
    match = client.post(
        f"{API}/cases/{mp['id']}/matches", json={"other_case_id": fp["id"], "rationale": "Looks the same"},
        headers=actors.police.headers,
    ).json()
    r = client.post(
        f"{API}/cases/{mp['id']}/matches/{match['id']}/review",
        json={"decision": "CONFIRMED", "evidence": "I am sure about it"}, headers=actors.police.headers,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "separation_of_duties"


def test_incompatible_and_duplicate_matches_rejected(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fi = create_case(client, actors.helpdesk, "found-items")
    fp = create_case(client, actors.helpdesk, "found-persons")
    bad = client.post(f"{API}/cases/{mp['id']}/matches", json={"other_case_id": fi["id"], "rationale": "not a person"}, headers=actors.police.headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "incompatible_case_types"
    body = {"other_case_id": fp["id"], "rationale": "Same red kurta"}
    assert client.post(f"{API}/cases/{mp['id']}/matches", json=body, headers=actors.police.headers).status_code == 201
    reverse = client.post(f"{API}/cases/{fp['id']}/matches", json={"other_case_id": mp["id"], "rationale": "Same red kurta"}, headers=actors.police.headers)
    assert reverse.status_code == 409


def test_resolve_without_match_requires_verification_evidence(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    client.post(f"{API}/cases/missing-persons/{mp['id']}/assign", json={"assignee_user_id": str(actors.police.id)}, headers=actors.police.headers)
    r = _status(client, actors.police, "missing-persons", mp["id"], status="RESOLVED", reason="Child walked in")
    assert r.status_code == 422 and r.json()["error"]["code"] == "verification_required"
    ok = _status(
        client, actors.police, "missing-persons", mp["id"], status="RESOLVED", reason="Child walked in",
        verification_evidence="Father identified child at post; Aadhaar matched",
    )
    assert ok.status_code == 200
    desk_view = client.get(f"{API}/cases/{mp['id']}/history", headers=actors.helpdesk.headers).json()
    assert desk_view["status_history"][-1]["verification_evidence"] is None
    police_view = client.get(f"{API}/cases/{mp['id']}/history", headers=actors.police.headers).json()
    assert police_view["status_history"][-1]["verification_evidence"] == (
        "Father identified child at post; Aadhaar matched"
    )
    assert client.get(f"{API}/cases/{mp['id']}", headers=actors.helpdesk.headers).json()["verification_evidence"] is None
    assert client.get(f"{API}/cases/{mp['id']}", headers=actors.police.headers).json()["verification_evidence"] == (
        "Father identified child at post; Aadhaar matched"
    )


def test_found_item_custody_is_separate_and_gates_resolution(client, actors):
    fi = create_case(client, actors.helpdesk, "found-items")
    base = f"{API}/cases/found-items/{fi['id']}"
    client.post(f"{base}/assign", json={"assignee_user_id": str(actors.police.id)}, headers=actors.police.headers)
    early = _status(client, actors.police, "found-items", fi["id"], status="RESOLVED", reason="Returned",
                    verification_evidence="Owner described the torn strap")
    assert early.status_code == 409 and early.json()["error"]["code"] == "custody_not_released"

    assert client.post(f"{base}/custody", json={"custody_status": "TRANSFERRED", "reason": "Move"}, headers=actors.police.headers).status_code == 422
    assert client.post(f"{base}/custody", json={"custody_status": "RELEASED", "reason": "Handover"}, headers=actors.helpdesk.headers).status_code == 403
    moved = client.post(
        f"{base}/custody", json={"custody_status": "TRANSFERRED", "reason": "Central store", "storage_location": "Central lost property"},
        headers=actors.police.headers,
    )
    assert moved.json()["details"]["custody_status"] == "TRANSFERRED"
    assert moved.json()["status"] == "ASSIGNED"
    moved_again = client.post(
        f"{base}/custody",
        json={
            "custody_status": "TRANSFERRED",
            "reason": "Move to station evidence room",
            "storage_location": "Station 2 evidence locker",
        },
        headers=actors.police.headers,
    )
    assert moved_again.json()["details"]["storage_location"] == "Station 2 evidence locker"
    released = client.post(
        f"{base}/custody",
        json={"custody_status": "RELEASED", "reason": "Owner verified", "recipient_acknowledgement": "Owner signed the desk register"},
        headers=actors.police.headers,
    )
    assert released.json()["details"]["custody_status"] == "RELEASED"
    assert client.post(f"{base}/custody", json={"custody_status": "IN_CUSTODY", "reason": "Undo"}, headers=actors.police.headers).status_code == 409

    police_history = client.get(f"{API}/cases/{fi['id']}/history", headers=actors.police.headers)
    assert police_history.status_code == 200
    custody = police_history.json()["custody_history"]
    assert [(event["from_status"], event["to_status"]) for event in custody] == [
        ("IN_CUSTODY", "TRANSFERRED"),
        ("TRANSFERRED", "TRANSFERRED"),
        ("TRANSFERRED", "RELEASED"),
    ]
    assert [event["reason"] for event in custody] == [
        "Central store",
        "Move to station evidence room",
        "Owner verified",
    ]
    assert custody[0]["storage_location"] == "Central lost property"
    assert custody[0]["handover_acknowledgement_recorded"] is False
    assert custody[1]["storage_location"] == "Station 2 evidence locker"
    assert custody[1]["handover_acknowledgement_recorded"] is False
    assert custody[2]["storage_location"] == "Station 2 evidence locker"
    assert custody[2]["handover_acknowledgement_recorded"] is True
    assert all(event["changed_by_user_id"] == str(actors.police.id) for event in custody)
    assert all(event["user_role"] == "POLICE_OFFICER" for event in custody)

    desk_history = client.get(f"{API}/cases/{fi['id']}/history", headers=actors.helpdesk.headers).json()
    assert desk_history["custody_history"] == custody
    assert "Owner signed the desk register" not in str(custody)

    ok = _status(client, actors.police, "found-items", fi["id"], status="RESOLVED", reason="Returned",
                 verification_evidence="Owner described the torn strap")
    assert ok.status_code == 200


def test_custody_history_keeps_legacy_events_without_inventing_locations(client, actors):
    case = create_case(client, actors.helpdesk, "found-items")
    case_id = uuid.UUID(case["id"])
    with SessionLocal() as db:
        db.add(
            AuditEvent(
                actor_user_id=actors.police.id,
                action="CUSTODY_CHANGED",
                entity_type="case",
                entity_id=case_id,
                case_id=case_id,
                case_number=case["case_number"],
                before_state={"custody_status": "IN_CUSTODY"},
                after_state={"custody_status": "TRANSFERRED", "storage_location_changed": True},
                reason="Legacy transfer",
            )
        )
        db.commit()

    history = client.get(f"{API}/cases/{case['id']}/history", headers=actors.police.headers)
    assert history.status_code == 200
    assert history.json()["custody_history"] == [
        {
            "id": history.json()["custody_history"][0]["id"],
            "case_id": case["id"],
            "from_status": "IN_CUSTODY",
            "to_status": "TRANSFERRED",
            "storage_location": None,
            "changed_by_user_id": str(actors.police.id),
            "user_role": "POLICE_OFFICER",
            "reason": "Legacy transfer",
            "handover_acknowledgement_recorded": False,
            "created_at": history.json()["custody_history"][0]["created_at"],
        }
    ]


def test_every_change_writes_audit_events(client, actors):
    case = create_case(client, actors.helpdesk, "lost-items")
    client.patch(f"{API}/cases/lost-items/{case['id']}", json={"item_description": "has laptop"}, headers=actors.helpdesk.headers)
    client.post(f"{API}/cases/lost-items/{case['id']}/assign", json={"assignee_user_id": str(actors.police.id)}, headers=actors.police.headers)
    client.post(f"{API}/cases/{case['id']}/notes", json={"note_text": "Checked CCTV"}, headers=actors.police.headers)
    _status(client, actors.police, "lost-items", case["id"], status="CLOSED", reason="Unrecoverable")

    events = client.get(f"{API}/audit-events", params={"case_id": case["id"]}, headers=actors.admin.headers).json()["items"]
    actions = {e["action"] for e in events}
    assert {"CASE_CREATED", "CASE_UPDATED", "CASE_ASSIGNED", "NOTE_ADDED", "CASE_CLOSED"} <= actions
    update = next(e for e in events if e["action"] == "CASE_UPDATED")
    assert update["after_state"]["changed_fields"] == ["item_description"]
    assert update["after_state"]["field_changes"] == [
        {"field_name": "item_description", "previous_value": None, "updated_value": "has laptop"}
    ]
    assert update["after_state"]["actor_role"] == "HELP_DESK_OPERATOR"
    assert all(e["request_id"] for e in events)
    with SessionLocal() as db:
        assert all(e.case_number == case["case_number"] for e in db.scalars(select(AuditEvent).where(AuditEvent.case_id == case["id"])))


def test_field_history_records_real_changes_with_role_and_protects_contact_values(client, actors):
    case = create_case(
        client,
        actors.helpdesk,
        "missing-persons",
        {
            "person_name": "Mohan",
            "approximate_age": 7,
            "last_seen_location": "Har Ki Pauri",
            "contact_details": "Father: +91 90000 00001",
        },
    )
    url = f"{API}/cases/missing-persons/{case['id']}"
    changed = client.patch(
        url,
        json={
            "person_name": "Mohan Kumar",
            "approximate_age": 8,
            "last_seen_location": "Brahmakund",
            "contact_details": "Father: +91 90000 00002",
        },
        headers=actors.helpdesk.headers,
    )
    assert changed.status_code == 200, changed.text

    desk_history = client.get(f"{API}/cases/{case['id']}/history", headers=actors.helpdesk.headers).json()
    changes = desk_history["field_changes"]
    assert [event["field_name"] for event in changes] == [
        "person_name",
        "approximate_age",
        "last_seen_location",
        "contact_details",
    ]
    assert changes[0]["previous_value"] == "Mohan"
    assert changes[0]["updated_value"] == "Mohan Kumar"
    assert changes[1]["previous_value"] == 7 and changes[1]["updated_value"] == 8
    assert changes[2]["previous_value"] == "Har Ki Pauri"
    assert changes[2]["updated_value"] == "Brahmakund"
    assert changes[3]["previous_value"] == changes[3]["updated_value"] == "[REDACTED]"
    assert all(event["case_id"] == case["id"] for event in changes)
    assert all(event["updated_by_user_id"] == str(actors.helpdesk.id) for event in changes)
    assert all(event["user_role"] == "HELP_DESK_OPERATOR" for event in changes)
    assert all(event["action_type"] == "CASE_UPDATED" and event["timestamp"] for event in changes)

    police_history = client.get(f"{API}/cases/{case['id']}/history", headers=actors.police.headers).json()
    contact = next(event for event in police_history["field_changes"] if event["field_name"] == "contact_details")
    assert contact["previous_value"] == "Father: +91 90000 00001"
    assert contact["updated_value"] == "Father: +91 90000 00002"

    # Sending an unchanged value must not append another audit/history event.
    no_change = client.patch(url, json={"person_name": "Mohan Kumar"}, headers=actors.helpdesk.headers)
    assert no_change.status_code == 200
    again = client.get(f"{API}/cases/{case['id']}/history", headers=actors.police.headers).json()
    assert again["field_changes"] == police_history["field_changes"]
