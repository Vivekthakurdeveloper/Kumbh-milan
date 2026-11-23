import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.helpers import API, FOUND_ITEM, FOUND_PERSON, LOST_ITEM, MISSING_PERSON, create_case


@pytest.mark.parametrize(
    "kind,payload,prefix",
    [
        ("missing-persons", MISSING_PERSON, "KSLF-MP-"),
        ("found-persons", FOUND_PERSON, "KSLF-FP-"),
        ("lost-items", LOST_ITEM, "KSLF-LI-"),
        ("found-items", FOUND_ITEM, "KSLF-FI-"),
    ],
)
def test_create_and_retrieve_each_case_type(client, actors, kind, payload, prefix):
    created = create_case(client, actors.helpdesk, kind, payload)
    assert created["case_number"].startswith(prefix)
    assert created["status"] == "OPEN"
    assert created["station_id"] == str(actors.station_a)
    assert "contact_details" not in created and "contact_details" not in created["details"]

    by_type = client.get(f"{API}/cases/{kind}/{created['id']}", headers=actors.helpdesk.headers)
    generic = client.get(f"{API}/cases/{created['id']}", headers=actors.helpdesk.headers)
    assert by_type.status_code == generic.status_code == 200
    assert by_type.json() == generic.json()


def test_found_item_starts_in_custody(client, actors):
    created = create_case(client, actors.helpdesk, "found-items")
    assert created["details"]["custody_status"] == "IN_CUSTODY"


def test_unknown_person_can_be_registered_with_no_identifying_fields(client, actors):
    created = create_case(client, actors.helpdesk, "missing-persons", {"age_group": "INFANT"})
    assert created["details"]["person_name"] is None


@pytest.mark.parametrize("actor_name", ["helpdesk", "police", "admin"])
def test_found_person_creation_roles(client, actors, actor_name):
    actor = getattr(actors, actor_name)
    payload = dict(FOUND_PERSON)
    if actor_name == "admin":
        payload["station_id"] = str(actors.station_a)
    response = client.post(
        f"{API}/cases/found-persons",
        json=payload,
        headers=actor.headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["case_type"] == "FOUND_PERSON"


def test_found_person_creation_rejects_unauthorized_station_scope(client, actors):
    response = client.post(
        f"{API}/cases/found-persons",
        json={**FOUND_PERSON, "station_id": str(actors.station_b)},
        headers=actors.police.headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "station_scope"


def test_wrong_type_route_returns_404(client, actors):
    created = create_case(client, actors.helpdesk, "lost-items")
    assert client.get(f"{API}/cases/missing-persons/{created['id']}", headers=actors.helpdesk.headers).status_code == 404


@pytest.mark.parametrize(
    "kind,payload,field",
    [
        ("lost-items", {"item_category": "bag"}, "item_name"),
        ("missing-persons", {"approximate_age": 300}, "approximate_age"),
        ("missing-persons", {"unexpected": "x"}, "unexpected"),
        ("found-persons", {"found_at": (datetime.now(UTC) + timedelta(days=2)).isoformat()}, "found_at"),
        ("found-persons", {"found_at": "2026-01-01T10:00:00"}, "found_at"),
    ],
)
def test_invalid_payloads_are_rejected(client, actors, kind, payload, field):
    r = client.post(f"{API}/cases/{kind}", json=payload, headers=actors.helpdesk.headers)
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "validation_failed"
    assert any(field in d["loc"] for d in error["details"])
    assert all("input" not in d for d in error["details"])


def test_missing_case_returns_404(client, actors):
    r = client.get(f"{API}/cases/{uuid.uuid4()}", headers=actors.police.headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "case_not_found"


def test_non_admin_cannot_register_for_another_station(client, actors):
    payload = {**LOST_ITEM, "station_id": str(actors.station_b)}
    assert client.post(f"{API}/cases/lost-items", json=payload, headers=actors.helpdesk.headers).status_code == 403


def test_admin_must_choose_station(client, actors):
    r = client.post(f"{API}/cases/lost-items", json=LOST_ITEM, headers=actors.admin.headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "station_required"
    ok = client.post(f"{API}/cases/lost-items", json={**LOST_ITEM, "station_id": str(actors.station_b)}, headers=actors.admin.headers)
    assert ok.status_code == 201


def test_idempotent_create_replays_and_rejects_key_reuse(client, actors):
    headers = {**actors.helpdesk.headers, "Idempotency-Key": "retry-key-12345"}
    first = client.post(f"{API}/cases/lost-items", json=LOST_ITEM, headers=headers)
    again = client.post(f"{API}/cases/lost-items", json=LOST_ITEM, headers=headers)
    assert first.status_code == again.status_code == 201
    assert again.headers["Idempotent-Replayed"] == "true"
    assert first.json()["id"] == again.json()["id"]
    listed = client.get(f"{API}/cases/lost-items", headers=actors.helpdesk.headers).json()
    assert listed["total"] == 1

    different = client.post(f"{API}/cases/lost-items", json={**LOST_ITEM, "item_name": "Phone"}, headers=headers)
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "idempotency_key_reused"


def test_list_pagination_sorting_and_filters(client, actors):
    ids = [create_case(client, actors.helpdesk, "lost-items", {**LOST_ITEM, "item_name": f"Item {i}"})["id"] for i in range(5)]
    create_case(client, actors.helpdesk, "lost-items", {**LOST_ITEM, "item_category": "phone", "item_name": "Redmi phone"})

    page1 = client.get(f"{API}/cases/lost-items", params={"limit": 2, "offset": 0}, headers=actors.helpdesk.headers).json()
    page2 = client.get(f"{API}/cases/lost-items", params={"limit": 2, "offset": 2}, headers=actors.helpdesk.headers).json()
    assert page1["total"] == 6 and len(page1["items"]) == 2
    assert not {c["id"] for c in page1["items"]} & {c["id"] for c in page2["items"]}

    oldest = client.get(f"{API}/cases/lost-items", params={"sort": "created_at", "limit": 1}, headers=actors.helpdesk.headers)
    assert oldest.json()["items"][0]["id"] == ids[0]

    phones = client.get(f"{API}/cases/lost-items", params={"item_category": "PHONE"}, headers=actors.helpdesk.headers).json()
    assert phones["total"] == 1

    too_big = client.get(f"{API}/cases/lost-items", params={"limit": 1000}, headers=actors.helpdesk.headers)
    assert too_big.status_code == 422


def test_unified_search_by_text_location_status_and_number(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    create_case(client, actors.helpdesk, "found-items")
    h = actors.helpdesk.headers

    assert client.get(f"{API}/cases", params={"q": "kurta"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/cases", params={"q": mp["case_number"]}, headers=h).json()["items"][0]["id"] == mp["id"]
    assert client.get(f"{API}/cases", params={"location": "pauri"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/cases", params={"case_type": "FOUND_ITEM"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/cases", params={"status": ["OPEN"]}, headers=h).json()["total"] == 2
    assert client.get(f"{API}/cases", params={"status": ["CLOSED"]}, headers=h).json()["total"] == 0
    assert client.get(f"{API}/cases", params={"gender": "MALE", "age_group": "CHILD"}, headers=h).json()["total"] == 1
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert client.get(f"{API}/cases", params={"created_from": future}, headers=h).json()["total"] == 0
    assert client.get(f"{API}/cases", params={"q": "100%_off"}, headers=h).status_code == 200


def test_patch_updates_details_and_rejects_closed_cases(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    r = client.patch(
        f"{API}/cases/missing-persons/{case['id']}", headers=actors.helpdesk.headers,
        json={"person_name": "Raju", "urgency_level": "HIGH"},
    )
    assert r.status_code == 200
    assert r.json()["details"]["person_name"] == "Raju"
    assert r.json()["urgency_level"] == "HIGH"
    assert client.get(f"{API}/cases", params={"q": "Raju"}, headers=actors.helpdesk.headers).json()["total"] == 1

    client.post(f"{API}/cases/missing-persons/{case['id']}/status", headers=actors.police.headers,
                json={"status": "CLOSED", "reason": "Duplicate registration"})
    closed = client.patch(f"{API}/cases/missing-persons/{case['id']}", headers=actors.police.headers, json={"language": "Hindi"})
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "case_closed"


@pytest.mark.parametrize(
    "kind,field,initial",
    [
        ("missing-persons", "person_name", "Mohan"),
        ("lost-items", "item_name", LOST_ITEM["item_name"]),
        ("found-items", "item_name", FOUND_ITEM["item_name"]),
    ],
)
@pytest.mark.parametrize("invalid", [None, "", "   "])
def test_required_case_names_cannot_be_cleared(client, actors, kind, field, initial, invalid):
    payload = None
    if kind == "missing-persons":
        payload = {**MISSING_PERSON, "person_name": initial}
    case = create_case(client, actors.helpdesk, kind, payload)

    response = client.patch(
        f"{API}/cases/{kind}/{case['id']}",
        headers=actors.helpdesk.headers,
        json={field: invalid},
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "validation_error",
        "message": "Enter a valid value.",
        "request_id": response.headers["X-Request-ID"],
        "details": {"field": field, "reason": "blank_or_null"},
    }
    unchanged = client.get(f"{API}/cases/{kind}/{case['id']}", headers=actors.helpdesk.headers).json()
    assert unchanged["details"][field] == initial


@pytest.mark.parametrize(
    "kind,payload,patch,field",
    [
        ("missing-persons", {**MISSING_PERSON, "person_name": "Mohan"}, {"approximate_age": 8}, "person_name"),
        ("lost-items", LOST_ITEM, {"item_description": "Contains school books"}, "item_name"),
        ("found-items", FOUND_ITEM, {"item_description": "Collected after bathing hours"}, "item_name"),
    ],
)
def test_patch_omitting_required_name_preserves_it(client, actors, kind, payload, patch, field):
    case = create_case(client, actors.helpdesk, kind, payload)
    response = client.patch(
        f"{API}/cases/{kind}/{case['id']}",
        headers=actors.helpdesk.headers,
        json=patch,
    )
    assert response.status_code == 200, response.text
    assert response.json()["details"][field] == case["details"][field]
