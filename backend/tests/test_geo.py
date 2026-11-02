import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, Case
from app.services import geo
from tests.helpers import API, FOUND_PERSON, MISSING_PERSON, create_case

HKP = (29.9563, 78.1716)  # Har Ki Pauri
NEAR_HKP = (29.9580, 78.1730)  # ~240 m away
TRIVENI = (30.1022, 78.2988)  # Triveni Ghat, Rishikesh (~20 km)


def _location(client, admin, code, name, kind="GHAT", point=HKP, **extra):
    r = client.post(
        f"{API}/locations",
        json={"kind": kind, "code": code, "name": name, "latitude": point[0], "longitude": point[1], **extra},
        headers=admin.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def places(client, actors):
    sector = _location(client, actors.admin, "SEC-01", "Sector 1", kind="SECTOR")
    hkp = _location(client, actors.admin, "HKP-GHAT", "Har Ki Pauri", sector_id=sector["id"], name_local="हर की पौड़ी")
    brahmakund = _location(client, actors.admin, "BRAHMAKUND", "Brahmakund", point=(29.9565, 78.1712), sector_id=sector["id"])
    triveni = _location(client, actors.admin, "TRIVENI", "Triveni Ghat", point=TRIVENI)
    return {"sector": sector, "hkp": hkp, "brahmakund": brahmakund, "triveni": triveni}


def _nearby(client, actor, point=HKP, **params):
    return client.get(f"{API}/cases/nearby", params={"lat": point[0], "lng": point[1], **params}, headers=actor.headers)


def test_location_reference_endpoints(client, actors, places):
    ghats = client.get(f"{API}/locations/ghats", headers=actors.helpdesk.headers)
    assert ghats.status_code == 200
    assert {g["code"] for g in ghats.json()} == {"HKP-GHAT", "BRAHMAKUND", "TRIVENI"}
    in_sector = client.get(f"{API}/locations/ghats", params={"sector_id": places["sector"]["id"]}, headers=actors.helpdesk.headers)
    assert {g["code"] for g in in_sector.json()} == {"HKP-GHAT", "BRAHMAKUND"}
    sectors = client.get(f"{API}/locations/sectors", headers=actors.police.headers).json()
    assert [s["code"] for s in sectors] == ["SEC-01"]
    assert client.get(f"{API}/locations", params={"q": "पौड़ी"}, headers=actors.police.headers).json()[0]["code"] == "HKP-GHAT"
    assert client.get(f"{API}/locations/ghats").status_code == 401


def test_location_management_is_admin_only_and_validated(client, actors, places):
    body = {"kind": "GHAT", "code": "NEW-GHAT", "name": "New", "latitude": 29.9, "longitude": 78.1}
    assert client.post(f"{API}/locations", json=body, headers=actors.police.headers).status_code == 403
    dup = client.post(f"{API}/locations", json={**body, "code": "HKP-GHAT"}, headers=actors.admin.headers)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "location_code_taken"
    bad_parent = client.post(f"{API}/locations", json={**body, "sector_id": places["hkp"]["id"]}, headers=actors.admin.headers)
    assert bad_parent.status_code == 422 and bad_parent.json()["error"]["code"] == "invalid_sector"
    assert client.post(f"{API}/locations", json={**body, "latitude": 95}, headers=actors.admin.headers).status_code == 422
    half = client.patch(f"{API}/locations/{places['hkp']['id']}", json={"latitude": 29.95}, headers=actors.admin.headers)
    assert half.status_code == 422
    off = client.patch(f"{API}/locations/{places['triveni']['id']}", json={"is_active": False}, headers=actors.admin.headers)
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert "TRIVENI" not in {g["code"] for g in client.get(f"{API}/locations/ghats", headers=actors.admin.headers).json()}
    with SessionLocal() as db:
        actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.entity_type == "location")))
    assert {"LOCATION_CREATED", "LOCATION_UPDATED"} <= actions


def test_case_coordinates_are_validated(client, actors, places):
    url = f"{API}/cases/found-persons"
    h = actors.helpdesk.headers
    no_source = client.post(url, json={**FOUND_PERSON, "latitude": HKP[0], "longitude": HKP[1]}, headers=h)
    assert no_source.status_code == 422
    half = client.post(url, json={**FOUND_PERSON, "latitude": HKP[0], "coordinate_source": "DEVICE_GPS"}, headers=h)
    assert half.status_code == 422
    swapped = client.post(
        url, json={**FOUND_PERSON, "latitude": HKP[1], "longitude": HKP[0], "coordinate_source": "DEVICE_GPS"}, headers=h
    )
    assert swapped.status_code == 422 and swapped.json()["error"]["code"] == "coordinates_out_of_area"
    unknown = client.post(url, json={**FOUND_PERSON, "location_id": places["sector"]["id"][:-4] + "0000"}, headers=h)
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "invalid_location"


def test_nearby_uses_own_coordinates_then_location_and_never_guesses(client, actors, places):
    gps = create_case(client, actors.helpdesk, "found-persons", {
        **FOUND_PERSON, "latitude": NEAR_HKP[0], "longitude": NEAR_HKP[1], "coordinate_source": "DEVICE_GPS",
        "coordinate_accuracy_m": 12,
    })
    by_location = create_case(client, actors.helpdesk, "missing-persons", {**MISSING_PERSON, "location_id": places["hkp"]["id"]})
    far = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "location_id": places["triveni"]["id"]})
    unplaced = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "found_location": "Har Ki Pauri"})
    assert gps["latitude"] == NEAR_HKP[0] and gps["coordinate_source"] == "DEVICE_GPS"
    assert by_location["latitude"] is None and by_location["location_id"] == places["hkp"]["id"]

    r = _nearby(client, actors.police, radius_m=1000)
    assert r.status_code == 200, r.text
    items = r.json()
    assert [i["id"] for i in items] == [by_location["id"], gps["id"]]  # nearest first
    assert items[0]["position"]["position_source"] == "location" and items[0]["distance_m"] < 1
    assert items[1]["position"]["position_source"] == "case" and 150 < items[1]["distance_m"] < 350
    ids = {i["id"] for i in items}
    assert far["id"] not in ids and unplaced["id"] not in ids

    wide = _nearby(client, actors.police, radius_m=10000).json()
    assert far["id"] not in {i["id"] for i in wide}
    assert _nearby(client, actors.police, point=TRIVENI, radius_m=500).json()[0]["id"] == far["id"]
    only_missing = _nearby(client, actors.police, radius_m=1000, case_type="MISSING_PERSON").json()
    assert [i["id"] for i in only_missing] == [by_location["id"]]


def test_nearby_limits_and_scope(client, actors, places):
    create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "location_id": places["hkp"]["id"]})
    too_far = _nearby(client, actors.police, radius_m=50_000)
    assert too_far.status_code == 422 and too_far.json()["error"]["code"] == "radius_too_large"
    assert _nearby(client, actors.police_b, radius_m=1000).json() == []
    other = _nearby(client, actors.police_b, radius_m=1000, all_stations=True).json()
    assert len(other) == 1 and other[0]["in_scope"] is False
    assert _nearby(client, actors.helpdesk_b, radius_m=1000, all_stations=True).status_code == 403
    assert client.get(f"{API}/cases/nearby", params={"lat": HKP[0], "lng": HKP[1]}).status_code == 401


def test_closed_cases_excluded_unless_requested(client, actors, places):
    case = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "location_id": places["hkp"]["id"]})
    r = client.post(
        f"{API}/cases/found-persons/{case['id']}/status",
        json={"status": "CLOSED", "reason": "Duplicate registration of the same child"}, headers=actors.police.headers,
    )
    assert r.status_code == 200, r.text
    assert _nearby(client, actors.police, radius_m=1000).json() == []
    closed = _nearby(client, actors.police, radius_m=1000, status="CLOSED").json()
    assert [i["id"] for i in closed] == [case["id"]]


def test_map_bounding_box(client, actors, places):
    near = create_case(client, actors.helpdesk, "found-persons", {
        **FOUND_PERSON, "latitude": NEAR_HKP[0], "longitude": NEAR_HKP[1], "coordinate_source": "MAP_PIN",
    })
    far = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "location_id": places["triveni"]["id"]})
    box = {"min_lat": 29.90, "min_lng": 78.10, "max_lat": 30.00, "max_lng": 78.20}
    r = client.get(f"{API}/cases/map", params=box, headers=actors.police.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["id"] for m in body["items"]] == [near["id"]] and body["truncated"] is False
    marker = body["items"][0]
    assert set(marker) == {"id", "case_number", "case_type", "status", "urgency_level", "station_id", "position", "in_scope"}
    assert far["id"] not in r.text
    big = client.get(f"{API}/cases/map", params={**box, "max_lat": 31.0}, headers=actors.police.headers)
    assert big.status_code == 422 and big.json()["error"]["code"] == "bbox_too_large"
    inverted = client.get(f"{API}/cases/map", params={**box, "min_lat": 30.1}, headers=actors.police.headers)
    assert inverted.status_code == 422 and inverted.json()["error"]["code"] == "invalid_bbox"


def test_map_truncates(client, actors, places, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "geo_map_limit", 1)
    for _ in range(2):
        create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "location_id": places["hkp"]["id"]})
    body = client.get(
        f"{API}/cases/map", params={"min_lat": 29.90, "min_lng": 78.10, "max_lat": 30.00, "max_lng": 78.20},
        headers=actors.police.headers,
    ).json()
    assert len(body["items"]) == 1 and body["truncated"] is True


def test_updating_and_clearing_coordinates_keeps_them_out_of_audit(client, actors, places):
    case = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON})
    url = f"{API}/cases/found-persons/{case['id']}"
    r = client.patch(url, json={"latitude": NEAR_HKP[0], "longitude": NEAR_HKP[1], "coordinate_source": "DEVICE_GPS"},
                     headers=actors.helpdesk.headers)
    assert r.status_code == 200 and r.json()["latitude"] == NEAR_HKP[0]
    assert len(_nearby(client, actors.police, radius_m=500).json()) == 1
    cleared = client.patch(url, json={"latitude": None, "longitude": None}, headers=actors.helpdesk.headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["latitude"] is None and cleared.json()["coordinate_source"] is None
    assert _nearby(client, actors.police, radius_m=500).json() == []
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == case["id"], AuditEvent.action == "CASE_UPDATED")).all()
    assert events and all("29.958" not in str(e.after_state) for e in events)
    assert any((e.after_state or {}).get("coordinates_changed") for e in events)


def test_link_locations_is_exact_dry_run_first_and_never_sets_coordinates(client, actors, places):
    exact = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "found_location": "  brahmakund "})
    local = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "found_location": "हर की पौड़ी"})
    vague = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "found_location": "near the big ghat"})

    with SessionLocal() as db:
        plan = {p.case_id: p for p in geo.plan_location_links(db)}
        assert plan[_uuid(exact)].outcome == "link" and str(plan[_uuid(exact)].location_id) == places["brahmakund"]["id"]
        assert plan[_uuid(local)].outcome == "link" and str(plan[_uuid(local)].location_id) == places["hkp"]["id"]
        assert plan[_uuid(vague)].outcome == "no_match"
        assert db.get(Case, _uuid(exact)).location_id is None  # planning changes nothing
        assert geo.apply_location_links(db, list(plan.values())) == 2

    with SessionLocal() as db:
        linked = db.get(Case, _uuid(exact))
        assert str(linked.location_id) == places["brahmakund"]["id"]
        assert linked.latitude is None and linked.longitude is None and linked.coordinate_source is None
        assert db.get(Case, _uuid(vague)).location_id is None
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.entity_id == linked.id, AuditEvent.action == "CASE_LOCATION_LINKED"))


def test_link_locations_cli_defaults_to_dry_run(client, actors, places, capsys):
    from app.cli import main

    create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "found_location": "Brahmakund"})
    assert main(["link-locations"]) == 0
    assert "Dry run" in capsys.readouterr().out
    with SessionLocal() as db:
        assert db.scalar(select(Case.location_id)) is None


def _uuid(case):
    import uuid

    return uuid.UUID(case["id"])
