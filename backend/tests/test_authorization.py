import uuid

import pytest

from tests.helpers import API, create_case


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/users", {"full_name": "X Y", "username": "xy", "password": "Ganga#River2027!", "role": "ADMIN"}),
        ("get", "/users", None),
        ("post", "/stations", {"name": "New", "code": "NEW-1", "kind": "HELP_DESK"}),
        ("get", "/audit-events", None),
    ],
)
def test_help_desk_cannot_use_admin_endpoints(client, actors, method, path, body):
    for actor in (actors.helpdesk, actors.police):
        r = client.request(method.upper(), f"{API}{path}", json=body, headers=actor.headers)
        assert r.status_code == 403, (actor.username, path)
        assert r.json()["error"]["code"] == "permission_denied"


def test_cases_outside_station_are_invisible(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    cid = case["id"]
    for actor in (actors.helpdesk_b, actors.police_b):
        for path in ("", "/notes", "/history", "/attachments", "/matches"):
            assert client.get(f"{API}/cases/{cid}{path}", headers=actor.headers).status_code == 404, (actor.username, path)
        assert client.get(f"{API}/cases/missing-persons/{cid}", headers=actor.headers).status_code == 404
        assert client.get(f"{API}/cases", headers=actor.headers).json()["total"] == 0
        assert client.patch(f"{API}/cases/missing-persons/{cid}", json={"language": "x"}, headers=actor.headers).status_code == 404
        assert client.post(f"{API}/cases/{cid}/notes", json={"note_text": "x"}, headers=actor.headers).status_code == 404
    assert client.get(f"{API}/cases/{cid}", headers=actors.admin.headers).status_code == 200


def test_list_and_search_responses_never_include_contact_details(client, actors):
    create_case(client, actors.helpdesk, "missing-persons")
    create_case(client, actors.helpdesk, "lost-items")
    for url in ("/cases", "/cases/missing-persons", "/cases/lost-items"):
        r = client.get(f"{API}{url}", headers=actors.police.headers)
        assert "98765" not in r.text and "91234" not in r.text and '"contact_details"' not in r.text


def test_contact_reveal_requires_police_and_is_audited(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    url = f"{API}/cases/{case['id']}/contact/reveal"
    assert client.post(url, json={"reason_code": "FAMILY_ENQUIRY"}, headers=actors.helpdesk.headers).status_code == 403
    r = client.post(url, json={"reason_code": "FAMILY_ENQUIRY", "note": "Inform family of reunion"}, headers=actors.police.headers)
    assert r.status_code == 200 and "98765" in r.json()["contact_details"]
    audit = client.get(f"{API}/audit-events", params={"action": "CONTACT_VIEWED"}, headers=actors.admin.headers).json()
    assert audit["total"] == 1 and audit["items"][0]["reason"] == "FAMILY_ENQUIRY"
    assert "Inform family" not in str(audit)
    assert "98765" not in str(audit)


def test_other_station_police_cannot_reveal_contact(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    r = client.post(f"{API}/cases/{case['id']}/contact/reveal", json={"reason_code": "INVESTIGATION"}, headers=actors.police_b.headers)
    assert r.status_code == 404


def test_help_desk_cannot_assign_resolve_close_or_confirm(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    base = f"{API}/cases/missing-persons/{case['id']}"
    h = actors.helpdesk.headers
    assert client.post(f"{base}/assign", json={"assignee_user_id": str(actors.police.id)}, headers=h).status_code == 403
    for target in ("RESOLVED", "CLOSED", "REOPENED", "MATCH_FOUND"):
        assert client.post(f"{base}/status", json={"status": target, "reason": "try"}, headers=h).status_code == 403
    assert client.post(f"{base}/status", json={"status": "UNDER_REVIEW", "reason": "Desk review"}, headers=h).status_code == 200


def test_help_desk_cannot_write_or_read_sensitive_notes(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    url = f"{API}/cases/{case['id']}/notes"
    assert client.post(url, json={"note_text": "secret", "is_sensitive": True}, headers=actors.helpdesk.headers).status_code == 403
    client.post(url, json={"note_text": "Family address confirmed", "is_sensitive": True}, headers=actors.police.headers)
    client.post(url, json={"note_text": "Called announcement booth"}, headers=actors.helpdesk.headers)
    assert client.get(url, headers=actors.helpdesk.headers).json()["total"] == 1
    assert client.get(url, headers=actors.police.headers).json()["total"] == 2


def test_police_can_only_assign_officers_from_own_station(client, actors):
    case = create_case(client, actors.helpdesk, "lost-items")
    r = client.post(
        f"{API}/cases/lost-items/{case['id']}/assign", json={"assignee_user_id": str(actors.police_b.id)},
        headers=actors.police.headers,
    )
    assert r.status_code == 403
    r = client.post(
        f"{API}/cases/lost-items/{case['id']}/assign", json={"assignee_user_id": str(actors.helpdesk.id)},
        headers=actors.police.headers,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_assignee"


def test_assignment_grants_cross_station_visibility(client, actors):
    case = create_case(client, actors.helpdesk, "found-persons")
    assert client.get(f"{API}/cases/{case['id']}", headers=actors.police_b.headers).status_code == 404
    client.post(
        f"{API}/cases/found-persons/{case['id']}/assign", json={"assignee_user_id": str(actors.police_b.id)},
        headers=actors.admin.headers,
    )
    assert client.get(f"{API}/cases/{case['id']}", headers=actors.police_b.headers).status_code == 200
    assert client.get(f"{API}/cases/{case['id']}", headers=actors.helpdesk_b.headers).status_code == 404


def test_all_station_search_is_police_only_and_summary_only(client, actors):
    create_case(client, actors.helpdesk, "missing-persons")
    assert client.get(f"{API}/cases", params={"all_stations": True}, headers=actors.helpdesk_b.headers).status_code == 403
    r = client.get(f"{API}/cases", params={"all_stations": True}, headers=actors.police_b.headers)
    assert r.status_code == 200 and r.json()["total"] == 1
    item = r.json()["items"][0]
    assert item["in_scope"] is False
    assert "details" not in item and "98765" not in r.text


def test_match_link_grants_cross_station_police_visibility(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk_b, "found-persons")
    r = client.post(
        f"{API}/cases/{fp['id']}/matches", json={"other_case_id": mp["id"], "rationale": "Same clothing and age"},
        headers=actors.police_b.headers,
    )
    assert r.status_code == 201
    assert client.get(f"{API}/cases/{mp['id']}", headers=actors.police_b.headers).status_code == 200
    assert client.get(f"{API}/cases/{mp['id']}", headers=actors.helpdesk_b.headers).status_code == 404


def test_help_desk_cannot_propose_match_to_other_station_case(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk_b, "found-persons")
    r = client.post(
        f"{API}/cases/{mp['id']}/matches", json={"other_case_id": fp["id"], "rationale": "Looks the same child"},
        headers=actors.helpdesk.headers,
    )
    assert r.status_code == 404


def test_guessed_ids_return_404(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    for path in (f"/cases/{uuid.uuid4()}/notes", f"/cases/{case['id']}/attachments/{uuid.uuid4()}",
                 f"/cases/{case['id']}/matches/{uuid.uuid4()}/review"):
        method = "POST" if path.endswith("review") else "GET"
        body = {"decision": "REJECTED", "evidence": "nothing to see here"} if method == "POST" else None
        assert client.request(method, f"{API}{path}", json=body, headers=actors.police.headers).status_code == 404
