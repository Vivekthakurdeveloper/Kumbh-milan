"""Phase 1 portal workflows and the backend gaps they depend on."""

import uuid

from tests.helpers import API, FOUND_PERSON, MISSING_PERSON, create_case, login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
EVIDENCE = "Officer compared clothing and birth mark with the reporting parent at the desk"


def _upload_photo(client, actor, case_id, data=PNG, name="photo.png", content_type="image/png"):
    return client.post(
        f"{API}/cases/{case_id}/attachments",
        data={"media_type": "PHOTO"},
        files={"file": (name, data, content_type)},
        headers=actor.headers,
    )


def _propose(client, actor, left_id, right_id):
    r = client.post(
        f"{API}/cases/{left_id}/matches",
        json={"other_case_id": right_id, "rationale": "Same clothing and age at nearby ghat"},
        headers=actor.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _review(client, actor, case_id, match_id, decision="CONFIRMED"):
    return client.post(
        f"{API}/cases/{case_id}/matches/{match_id}/review",
        json={"decision": decision, "evidence": EVIDENCE},
        headers=actor.headers,
    )


def _location(client, admin):
    r = client.post(
        f"{API}/locations",
        json={
            "kind": "GHAT",
            "code": "HKP-P1",
            "name": "Har Ki Pauri Phase1",
            "latitude": 29.9563,
            "longitude": 78.1716,
        },
        headers=admin.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- Workflows -----------------------------------------------------------------------------------


def test_workflow_missing_person_register_upload_and_view(client, actors):
    created = create_case(
        client,
        actors.helpdesk,
        "missing-persons",
        {**MISSING_PERSON, "person_name": "Raju"},
    )
    assert created["case_number"].startswith("KSLF-MP-")
    assert created["details"]["person_name"] == "Raju"

    photo = _upload_photo(client, actors.helpdesk, created["id"])
    assert photo.status_code == 201, photo.text
    assert photo.json()["media_type"] == "PHOTO"
    assert photo.json()["downloadable"] is True

    fetched = client.get(f"{API}/cases/{created['id']}", headers=actors.helpdesk.headers)
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["case_number"] == created["case_number"]
    assert body["details"]["approximate_age"] == 7
    attachments = client.get(f"{API}/cases/{created['id']}/attachments", headers=actors.helpdesk.headers)
    assert attachments.status_code == 200 and attachments.json()["total"] == 1


def test_workflow_found_person_register_upload_and_view(client, actors):
    created = create_case(
        client,
        actors.helpdesk,
        "found-persons",
        {
            **FOUND_PERSON,
            "current_care_location": "HKP help desk tent 2",
            "identifying_marks": "mole on left cheek",
            "language": "Hindi",
        },
    )
    assert created["case_number"].startswith("KSLF-FP-")
    assert created["details"]["current_care_location"] == "HKP help desk tent 2"

    photo = _upload_photo(client, actors.helpdesk, created["id"], name="found.png")
    assert photo.status_code == 201, photo.text

    fetched = client.get(f"{API}/cases/found-persons/{created['id']}", headers=actors.police.headers)
    assert fetched.status_code == 200
    assert fetched.json()["case_number"] == created["case_number"]


def test_workflow_search_note_and_status(client, actors):
    created = create_case(client, actors.helpdesk, "missing-persons")
    search = client.get(
        f"{API}/cases",
        params={"case_number": created["case_number"]},
        headers=actors.helpdesk.headers,
    )
    assert search.status_code == 200
    assert search.json()["total"] == 1
    row = search.json()["items"][0]
    assert row["case_number"] == created["case_number"]
    assert row["approximate_age"] == 7
    assert row["gender"] == "MALE"
    assert row["age_group"] == "CHILD"

    note = client.post(
        f"{API}/cases/{created['id']}/notes",
        json={"note_text": "Parent waiting at desk 3"},
        headers=actors.helpdesk.headers,
    )
    assert note.status_code == 201, note.text

    status = client.post(
        f"{API}/cases/missing-persons/{created['id']}/status",
        json={"status": "UNDER_REVIEW", "reason": "Desk triage started"},
        headers=actors.helpdesk.headers,
    )
    assert status.status_code == 200
    assert status.json()["status"] == "UNDER_REVIEW"

    history = client.get(f"{API}/cases/{created['id']}/history", headers=actors.police.headers)
    assert history.status_code == 200
    to_statuses = [h["to_status"] for h in history.json()["status_history"]]
    assert "UNDER_REVIEW" in to_statuses
    notes = client.get(f"{API}/cases/{created['id']}/notes", headers=actors.helpdesk.headers)
    assert notes.json()["total"] == 1


def test_workflow_match_review_queue_two_person_rule(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk, "found-persons")
    match = _propose(client, actors.police, mp["id"], fp["id"])

    queue = client.get(f"{API}/matches", headers=actors.police.headers)
    assert queue.status_code == 200, queue.text
    body = queue.json()
    assert body["total"] >= 1
    item = next(i for i in body["items"] if i["id"] == match["id"])
    assert item["status"] == "PROPOSED"
    assert item["left_case"]["case_number"] == mp["case_number"]
    assert item["right_case"]["case_number"] == fp["case_number"]
    assert item["left_case"]["approximate_age"] == 7
    assert item["right_case"]["gender"] == "MALE"

    dashboard = client.get(f"{API}/dashboard/summary", headers=actors.police.headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["pending_match_reviews"] == body["total"]

    # Proposer cannot confirm (separation of duties).
    blocked = _review(client, actors.police, mp["id"], match["id"])
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "separation_of_duties"

    # Help desk cannot review.
    assert _review(client, actors.helpdesk, mp["id"], match["id"]).status_code == 403

    # Second officer confirms.
    confirmed = _review(client, actors.police2, mp["id"], match["id"])
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["reviewed_by_user_id"] == str(actors.police2.id)

    audit = client.get(
        f"{API}/audit-events",
        params={"action": "MATCH_REVIEWED", "case_id": mp["id"]},
        headers=actors.admin.headers,
    )
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1
    assert any(e["actor_user_id"] == str(actors.police2.id) for e in audit.json()["items"])

    # Confirmed match leaves the default PROPOSED queue.
    after = client.get(f"{API}/matches", headers=actors.police.headers).json()
    assert all(i["id"] != match["id"] for i in after["items"])


# --- Gap coverage --------------------------------------------------------------------------------


def test_language_over_100_chars_returns_422(client, actors):
    long_language = "x" * 150
    for kind, payload in (
        ("missing-persons", {**MISSING_PERSON, "language": long_language}),
        ("found-persons", {**FOUND_PERSON, "language": long_language}),
    ):
        r = client.post(f"{API}/cases/{kind}", json=payload, headers=actors.helpdesk.headers)
        assert r.status_code == 422, r.text
        error = r.json()["error"]
        assert error["code"] == "validation_failed"
        assert any("language" in d["loc"] for d in error["details"])

    ok = create_case(
        client, actors.helpdesk, "missing-persons", {**MISSING_PERSON, "language": "x" * 100}
    )
    assert ok["details"]["language"] == "x" * 100


def test_case_summary_includes_location_id_age_and_gender(client, actors):
    place = _location(client, actors.admin)
    created = create_case(
        client,
        actors.helpdesk,
        "missing-persons",
        {**MISSING_PERSON, "location_id": place["id"], "person_name": "Sita"},
    )
    search = client.get(
        f"{API}/cases",
        params={"case_number": created["case_number"]},
        headers=actors.helpdesk.headers,
    )
    assert search.status_code == 200
    row = search.json()["items"][0]
    assert row["location_id"] == place["id"]
    assert row["approximate_age"] == 7
    assert row["age_group"] == "CHILD"
    assert row["gender"] == "MALE"
    assert row["title"] == "Sita"

    found = create_case(client, actors.helpdesk, "found-persons")
    found_row = client.get(
        f"{API}/cases",
        params={"case_number": found["case_number"]},
        headers=actors.helpdesk.headers,
    ).json()["items"][0]
    assert found_row["gender"] == "MALE"
    assert found_row["approximate_age"] == 7


def test_user_directory_returns_minimal_fields_for_all_roles(client, actors):
    ids = [str(actors.police.id), str(actors.helpdesk.id), str(uuid.uuid4())]
    for actor in (actors.helpdesk, actors.police, actors.admin):
        r = client.get(f"{API}/users/directory", params=[("ids", i) for i in ids], headers=actor.headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert {row["id"] for row in body} == {str(actors.police.id), str(actors.helpdesk.id)}
        for row in body:
            assert set(row.keys()) == {"id", "full_name", "role", "station_id"}
            assert "email" not in row
            assert "phone_number" not in row
            assert "username" not in row
            assert "police_badge_number" not in row


def test_assignable_officers_include_username_and_role_without_contact_fields(client, actors):
    r = client.get(f"{API}/users/assignable-officers", headers=actors.police.headers)
    assert r.status_code == 200, r.text
    rows = {row["username"]: row for row in r.json()}
    assert set(rows) == {"police", "police2"}  # own station only
    for row in rows.values():
        assert set(row.keys()) == {"id", "full_name", "username", "role", "police_badge_number", "station_id"}
        assert row["role"] == "POLICE_OFFICER"
    assert rows["police"]["police_badge_number"] == "UK-1"

    denied = client.get(f"{API}/users/assignable-officers", headers=actors.helpdesk.headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"


def test_match_queue_redacts_out_of_scope_side_for_helpdesk(client, actors):
    # Own-station missing person + other-station found person, linked by a manual proposal
    # from police (which grants visibility of the counterpart to police, not help desk).
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp_b = create_case(client, actors.helpdesk_b, "found-persons")
    match = _propose(client, actors.police, mp["id"], fp_b["id"])

    queue = client.get(f"{API}/matches", headers=actors.helpdesk.headers)
    assert queue.status_code == 200
    item = next(i for i in queue.json()["items"] if i["id"] == match["id"])
    assert item["left_case"]["case_number"] == mp["case_number"]
    assert item["left_case"]["approximate_age"] == 7
    # Right side is at another station: help desk sees the counterpart exists, not who/where.
    assert item["right_case"]["id"] == fp_b["id"]
    assert item["right_case"]["title"] is None
    assert item["right_case"]["location"] is None
    assert item["right_case"]["location_id"] is None
    assert item["right_case"]["approximate_age"] is None
    assert item["right_case"]["gender"] is None
    assert item["right_case"]["event_at"] is None


# --- Negative cases ------------------------------------------------------------------------------


def test_invalid_login_rejected(client, actors):
    r = client.post(f"{API}/auth/login", json={"username": "police", "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_expired_or_garbage_token_rejected(client, actors):
    assert client.get(f"{API}/dashboard/summary").status_code == 401
    bad = client.get(
        f"{API}/dashboard/summary",
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert bad.status_code == 401


def test_out_of_station_case_is_404_for_helpdesk(client, actors):
    created = create_case(client, actors.helpdesk, "missing-persons")
    assert client.get(f"{API}/cases/{created['id']}", headers=actors.helpdesk_b.headers).status_code == 404


def test_rejected_upload_type(client, actors):
    case = create_case(client, actors.helpdesk, "found-persons")
    r = _upload_photo(
        client,
        actors.helpdesk,
        case["id"],
        data=b"MZ\x90\0 not an image",
        name="virus.exe",
        content_type="image/png",
    )
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_logout_ends_session(client, actors):
    headers, _refresh = login(client, "police2")
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 200
    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401
