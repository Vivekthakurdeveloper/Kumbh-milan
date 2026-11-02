import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.matching import ENGINE_VERSION
from app.models import AuditEvent, CaseMatch
from tests.helpers import API, FOUND_ITEM, FOUND_PERSON, LOST_ITEM, MISSING_PERSON, create_case

EVIDENCE = "Child identified the father by name at desk 3; matching photo on the family's phone."
SIMILAR_FOUND = {
    **FOUND_PERSON, "approximate_age": 8, "clothing_description": "लाल कुर्ता, blue shorts",
    "identifying_marks": "mole near left cheek", "language": "Bhojpuri", "found_location": "Har Ki Pauri ghat steps",
}


@pytest.fixture
def auto_matching(monkeypatch):
    monkeypatch.setattr(get_settings(), "matching_auto_enabled", True)


def _matches(client, actor, case_id):
    r = client.get(f"{API}/cases/{case_id}/matches", headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_registration_creates_reviewable_suggestion_without_changing_cases(client, actors, auto_matching):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk, "found-persons", SIMILAR_FOUND)
    [match] = _matches(client, actors.police, mp["id"])
    assert match["source"] == "AUTOMATIC" and match["status"] == "PROPOSED"
    assert match["right_case_id"] == fp["id"] and match["proposed_by_user_id"] is None
    assert match["confidence_band"] in ("HIGH", "MEDIUM") and 0 < match["score"] <= 1
    assert match["engine_version"] == ENGINE_VERSION
    assert {r["feature"] for r in match["reasons"]} >= {"gender", "age", "clothing_colours", "identifying_marks"}
    # Suggestions never reunify, change status, or expose contact details.
    for case in (mp, fp):
        body = client.get(f"{API}/cases/{case['id']}", headers=actors.police.headers).json()
        assert body["status"] == "OPEN" and "contact_details" not in body
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "MATCH_SUGGESTED")).all()
    assert len(events) == 2 and all(e.actor_user_id is None for e in events)
    assert {str(e.case_id) for e in events} == {mp["id"], fp["id"]}


def test_contradicting_and_inactive_cases_are_not_suggested(client, actors, auto_matching):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    create_case(client, actors.helpdesk, "found-persons", {**SIMILAR_FOUND, "estimated_gender": "FEMALE"})
    create_case(client, actors.helpdesk, "found-persons", {**SIMILAR_FOUND, "approximate_age": 45, "age_group": "ADULT"})
    assert _matches(client, actors.police, mp["id"]) == []

    closed = create_case(client, actors.helpdesk, "found-persons", {**SIMILAR_FOUND, "approximate_age": 7})
    with SessionLocal() as db:  # remove the suggestion created at registration, then close the found case
        db.execute(CaseMatch.__table__.delete())
        db.commit()
    r = client.post(f"{API}/cases/found-persons/{closed['id']}/status",
                    json={"status": "CLOSED", "reason": "Child handed to family directly"}, headers=actors.police.headers)
    assert r.status_code == 200
    gen = client.post(f"{API}/cases/{mp['id']}/matches/generate", headers=actors.police.headers)
    assert gen.status_code == 200 and gen.json()["created"] == 0


def test_generate_endpoint_is_repeatable_and_rejected_pairs_stay_rejected(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk, "found-persons", SIMILAR_FOUND)
    first = client.post(f"{API}/cases/{mp['id']}/matches/generate", headers=actors.helpdesk.headers)
    assert first.status_code == 200 and first.json()["created"] == 1
    match_id = first.json()["suggestions"][0]["id"]
    again = client.post(f"{API}/cases/{mp['id']}/matches/generate", headers=actors.helpdesk.headers)
    assert again.json()["created"] == 0

    reject = client.post(f"{API}/cases/{mp['id']}/matches/{match_id}/review",
                         json={"decision": "REJECTED", "evidence": "Different child; father checked the photo."},
                         headers=actors.police.headers)
    assert reject.status_code == 200, reject.text
    assert client.post(f"{API}/cases/{fp['id']}/matches/generate", headers=actors.police.headers).json()["created"] == 0
    [only] = _matches(client, actors.police, mp["id"])
    assert only["status"] == "REJECTED"


def test_generate_requires_access_and_active_case(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    assert client.post(f"{API}/cases/{mp['id']}/matches/generate").status_code == 401
    assert client.post(f"{API}/cases/{mp['id']}/matches/generate", headers=actors.helpdesk_b.headers).status_code == 404
    client.post(f"{API}/cases/missing-persons/{mp['id']}/status",
                json={"status": "CLOSED", "reason": "Registered twice by mistake"}, headers=actors.police.headers)
    closed = client.post(f"{API}/cases/{mp['id']}/matches/generate", headers=actors.police.headers)
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "case_not_matchable"


def test_automatic_suggestion_grants_no_cross_station_access_until_confirmed(client, actors, auto_matching):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk_b, "found-persons", SIMILAR_FOUND)
    [match] = _matches(client, actors.police, mp["id"])
    assert match["counterpart"]["in_scope"] is False
    assert client.get(f"{API}/cases/{fp['id']}", headers=actors.police.headers).status_code == 404
    contact = client.post(f"{API}/cases/{fp['id']}/contact/reveal", json={"reason_code": "FAMILY_ENQUIRY"}, headers=actors.police.headers)
    assert contact.status_code == 404
    # Help-desk staff see only that a counterpart exists elsewhere.
    hidden = _matches(client, actors.helpdesk, mp["id"])[0]
    hd = hidden["counterpart"]
    assert hd["title"] is None and hd["location"] is None and hd["case_number"] == fp["case_number"]
    assert hidden["reasons"] is None and hidden["score"] is None and hidden["confidence_band"] is None

    r = client.post(f"{API}/cases/{mp['id']}/matches/{match['id']}/review",
                    json={"decision": "CONFIRMED", "evidence": EVIDENCE}, headers=actors.police.headers)
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/cases/{fp['id']}", headers=actors.police.headers).status_code == 200
    assert client.get(f"{API}/cases/{mp['id']}", headers=actors.police.headers).json()["status"] == "MATCH_FOUND"


def test_edit_rescores_open_suggestion(client, actors, auto_matching):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk, "found-persons", SIMILAR_FOUND)
    before = _matches(client, actors.police, mp["id"])[0]
    r = client.patch(f"{API}/cases/found-persons/{fp['id']}",
                     json={"clothing_description": "green saree", "identifying_marks": "scar on chin"},
                     headers=actors.helpdesk.headers)
    assert r.status_code == 200
    after = _matches(client, actors.police, mp["id"])[0]
    assert after["id"] == before["id"] and after["score"] < before["score"]


def test_matching_failure_never_fails_registration(client, actors, auto_matching, monkeypatch):
    from app.services import matching

    def broken(*args, **kwargs):
        raise RuntimeError("engine bug")

    monkeypatch.setattr(matching, "generate_for_case", broken)
    before = client.get("/metrics").text
    create_case(client, actors.helpdesk, "missing-persons")
    text = client.get("/metrics").text
    count = lambda t: float(next((l.split()[-1] for l in t.splitlines() if l.startswith("kumbh_matching_failures_total ")), 0))  # noqa: E731,E741
    assert count(text) == count(before) + 1


def test_item_matching(client, actors, auto_matching):
    lost = create_case(client, actors.helpdesk, "lost-items", LOST_ITEM)
    create_case(client, actors.helpdesk, "found-items", {**FOUND_ITEM, "item_description": "Wildcraft, left strap torn"})
    create_case(client, actors.helpdesk, "found-items", {**FOUND_ITEM, "item_category": "phone", "item_name": "Black phone"})
    [match] = _matches(client, actors.police, lost["id"])
    assert match["match_domain"] == "ITEM" and match["source"] == "AUTOMATIC"


def test_rematch_cli(client, actors, capsys):
    from app.cli import main

    create_case(client, actors.helpdesk, "missing-persons", MISSING_PERSON)
    create_case(client, actors.helpdesk, "found-persons", SIMILAR_FOUND)
    assert main(["rematch"]) == 0
    assert "stored 1 new suggestions" in capsys.readouterr().out
    with SessionLocal() as db:
        assert db.scalar(select(func.count(CaseMatch.id))) == 1
