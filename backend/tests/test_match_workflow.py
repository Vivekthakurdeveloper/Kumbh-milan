"""Regression tests for match confirmation, revocation, concurrency, and workflow idempotency."""

import threading
import uuid

import pytest
from sqlalchemy import func, select

from app.common.errors import Conflict
from app.database import SessionLocal
from app.models import AuditEvent, CaseMatch, CaseStatusHistory, User
from app.models.enums import MatchStatus
from app.schemas.workflow import MatchReviewRequest
from app.services import workflow as workflow_service
from tests.helpers import API, FOUND_PERSON, create_case

EVIDENCE = "Father identified the child in person; birth mark matched"


def _propose(client, actor, left_id, right_id):
    r = client.post(
        f"{API}/cases/{left_id}/matches",
        json={"other_case_id": right_id, "rationale": "Same clothing and age at nearby ghat"},
        headers=actor.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _review(client, actor, case_id, match_id, decision="CONFIRMED", **extra):
    return client.post(
        f"{API}/cases/{case_id}/matches/{match_id}/review",
        json={"decision": decision, "evidence": EVIDENCE, **extra},
        headers=actor.headers,
    )


def _status(client, actor, case_id):
    return client.get(f"{API}/cases/{case_id}", headers=actor.headers).json()["status"]


def _person_pair(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    fp = create_case(client, actors.helpdesk, "found-persons")
    return mp, fp


def test_open_missing_person_enters_match_found_through_confirmation(client, actors):
    mp, fp = _person_pair(client, actors)
    assert mp["status"] == fp["status"] == "OPEN"
    match = _propose(client, actors.police, mp["id"], fp["id"])
    r = _review(client, actors.police2, mp["id"], match["id"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED"
    assert _status(client, actors.police, mp["id"]) == "MATCH_FOUND"
    assert _status(client, actors.police, fp["id"]) == "MATCH_FOUND"

    history = client.get(f"{API}/cases/{mp['id']}/history", headers=actors.police.headers).json()["status_history"]
    last = history[-1]
    assert (last["from_status"], last["to_status"], last["match_id"]) == ("OPEN", "MATCH_FOUND", match["id"])
    audit = client.get(f"{API}/audit-events", params={"action": "MATCH_REVIEWED"}, headers=actors.admin.headers).json()
    assert audit["total"] == 2
    assert {e["case_id"] for e in audit["items"]} == {mp["id"], fp["id"]}


def test_open_found_person_follows_confirmation_then_separate_resolution(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, fp["id"], mp["id"])
    assert _review(client, actors.police2, fp["id"], match["id"]).status_code == 200
    assert _status(client, actors.police, fp["id"]) == "MATCH_FOUND"
    # Confirmation never resolves: reunification is its own verified step.
    r = client.post(
        f"{API}/cases/found-persons/{fp['id']}/status",
        json={"status": "RESOLVED", "reason": "Handed over to father", "match_id": match["id"]},
        headers=actors.police.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    assert _status(client, actors.police, mp["id"]) == "MATCH_FOUND"


def test_arbitrary_status_jumps_remain_rejected(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    for target, extra in (("MATCH_FOUND", {"match_id": match["id"]}), ("RESOLVED", {"verification_evidence": EVIDENCE})):
        r = client.post(
            f"{API}/cases/missing-persons/{mp['id']}/status",
            json={"status": target, "reason": "Trying to skip steps", **extra},
            headers=actors.police.headers,
        )
        assert r.status_code == 409, (target, r.text)
        assert r.json()["error"]["code"] == "invalid_status_transition"
    assert _status(client, actors.police, mp["id"]) == "OPEN"


def test_unauthorised_users_cannot_confirm(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    assert _review(client, actors.helpdesk, mp["id"], match["id"]).status_code == 403
    assert _review(client, actors.police, mp["id"], match["id"]).json()["error"]["code"] == "separation_of_duties"
    assert _review(client, actors.police_b, mp["id"], match["id"]).status_code == 404
    assert _status(client, actors.police, mp["id"]) == "OPEN"


def test_confirmation_refuses_inactive_cases_and_changes_nothing(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    client.post(
        f"{API}/cases/found-persons/{fp['id']}/status",
        json={"status": "CLOSED", "reason": "Duplicate registration"}, headers=actors.police.headers,
    )
    r = _review(client, actors.police2, mp["id"], match["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "case_not_confirmable"
    matches = client.get(f"{API}/cases/{mp['id']}/matches", headers=actors.police.headers).json()
    assert matches[0]["status"] == "PROPOSED"
    assert _status(client, actors.police, mp["id"]) == "OPEN"


def test_found_person_cannot_be_confirmed_against_two_missing_persons(client, actors):
    mp1, fp = _person_pair(client, actors)
    mp2 = create_case(client, actors.helpdesk, "missing-persons")
    first = _propose(client, actors.police, mp1["id"], fp["id"])
    second = _propose(client, actors.police, mp2["id"], fp["id"])
    assert _review(client, actors.police2, mp1["id"], first["id"]).status_code == 200
    r = _review(client, actors.police2, mp2["id"], second["id"])
    assert r.status_code == 409
    assert r.json()["error"]["code"] in ("case_not_confirmable", "conflicting_confirmed_match")
    assert _status(client, actors.police, mp2["id"]) == "OPEN"


def test_concurrent_confirmations_produce_one_consistent_result(client, actors):
    fp = create_case(client, actors.helpdesk, "found-persons")
    mps = [create_case(client, actors.helpdesk, "missing-persons") for _ in range(2)]
    matches = [_propose(client, actors.admin, mp["id"], fp["id"]) for mp in mps]
    reviewers = [actors.police.id, actors.police2.id]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def confirm(index: int) -> None:
        with SessionLocal() as db:
            user = db.get(User, reviewers[index])
            barrier.wait()
            try:
                workflow_service.review_match(
                    db, user, uuid.UUID(mps[index]["id"]), uuid.UUID(matches[index]["id"]),
                    MatchReviewRequest(decision=MatchStatus.CONFIRMED, evidence=EVIDENCE),
                )
                outcomes.append("ok")
            except Conflict as exc:
                outcomes.append(exc.code)

    threads = [threading.Thread(target=confirm, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes)[-1] == "ok" and outcomes.count("ok") == 1, outcomes
    with SessionLocal() as db:
        confirmed = db.scalar(select(func.count()).where(CaseMatch.status == MatchStatus.CONFIRMED))
        fp_transitions = db.scalar(
            select(func.count()).where(
                CaseStatusHistory.case_id == uuid.UUID(fp["id"]), CaseStatusHistory.to_status == "MATCH_FOUND"
            )
        )
    assert confirmed == 1 and fp_transitions == 1


def test_failed_confirmation_leaves_no_partial_records(client, actors, monkeypatch):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    original = workflow_service._apply_transition
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure mid-confirmation")
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow_service, "_apply_transition", flaky)
    with pytest.raises(RuntimeError):
        _review(client, actors.police2, mp["id"], match["id"])

    with SessionLocal() as db:
        assert db.get(CaseMatch, uuid.UUID(match["id"])).status == MatchStatus.PROPOSED
        assert db.scalar(select(func.count()).where(CaseStatusHistory.to_status == "MATCH_FOUND")) == 0
        assert db.scalar(select(func.count()).where(AuditEvent.action == "MATCH_REVIEWED")) == 0


def test_stale_review_is_rejected_with_conflict(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    seen_left = match["left_case_version"]
    client.patch(f"{API}/cases/missing-persons/{mp['id']}", json={"language": "Hindi"}, headers=actors.police.headers)
    r = _review(client, actors.police2, mp["id"], match["id"], left_case_version=seen_left)
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "case_changed" and error["details"]["current_version"] == seen_left + 1
    fresh = client.get(f"{API}/cases/{mp['id']}/matches", headers=actors.police.headers).json()[0]
    assert fresh["status"] == "PROPOSED"
    ok = _review(client, actors.police2, mp["id"], match["id"], left_case_version=fresh["left_case_version"])
    assert ok.status_code == 200


def test_revoking_a_wrong_match_restores_work_and_allows_another(client, actors):
    mp, fp1 = _person_pair(client, actors)
    fp2 = create_case(client, actors.helpdesk, "found-persons", {**FOUND_PERSON, "found_location": "Sector 9"})
    wrong = _propose(client, actors.police, mp["id"], fp1["id"])
    right = _propose(client, actors.police, mp["id"], fp2["id"])
    assert _review(client, actors.police2, mp["id"], wrong["id"]).status_code == 200

    r = client.post(
        f"{API}/cases/{mp['id']}/matches/{wrong['id']}/revoke",
        json={"reason": "Family saw the child; not their son"}, headers=actors.police.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REVOKED"
    assert _status(client, actors.police, mp["id"]) == "UNDER_REVIEW"
    assert _review(client, actors.police2, mp["id"], right["id"]).status_code == 200
    assert _status(client, actors.police, mp["id"]) == "MATCH_FOUND"
    assert _status(client, actors.police, fp1["id"]) == "UNDER_REVIEW"


def test_revoking_requires_reopening_resolved_cases(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    _review(client, actors.police2, mp["id"], match["id"])
    client.post(
        f"{API}/cases/missing-persons/{mp['id']}/status",
        json={"status": "RESOLVED", "reason": "Reunited", "match_id": match["id"]}, headers=actors.police.headers,
    )
    r = client.post(
        f"{API}/cases/{mp['id']}/matches/{match['id']}/revoke",
        json={"reason": "Identity later disputed"}, headers=actors.police.headers,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "case_inactive"
    assert client.post(
        f"{API}/cases/{mp['id']}/matches/{match['id']}/revoke",
        json={"reason": "Identity later disputed"}, headers=actors.helpdesk.headers,
    ).status_code == 403


def test_assigning_a_matched_case_keeps_match_found(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    _review(client, actors.police2, mp["id"], match["id"])
    r = client.post(
        f"{API}/cases/missing-persons/{mp['id']}/assign",
        json={"assignee_user_id": str(actors.police.id), "reason": "Handle the reunion"}, headers=actors.police.headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "MATCH_FOUND" and r.json()["assigned_to_user_id"] == str(actors.police.id)


def test_status_change_is_idempotent_and_rejects_key_reuse(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    headers = {**actors.helpdesk.headers, "Idempotency-Key": "status-retry-0001"}
    body = {"status": "UNDER_REVIEW", "reason": "Triage at desk"}
    first = client.post(f"{API}/cases/missing-persons/{mp['id']}/status", json=body, headers=headers)
    again = client.post(f"{API}/cases/missing-persons/{mp['id']}/status", json=body, headers=headers)
    assert first.status_code == again.status_code == 200
    assert again.headers.get("Idempotent-Replayed") == "true"
    history = client.get(f"{API}/cases/{mp['id']}/history", headers=actors.helpdesk.headers).json()["status_history"]
    assert [h["to_status"] for h in history] == ["OPEN", "UNDER_REVIEW"]
    other = client.post(
        f"{API}/cases/missing-persons/{mp['id']}/status", json={**body, "reason": "Different"}, headers=headers
    )
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_key_reused"


def test_review_and_note_retries_are_idempotent(client, actors):
    mp, fp = _person_pair(client, actors)
    match = _propose(client, actors.police, mp["id"], fp["id"])
    h = {**actors.police2.headers, "Idempotency-Key": "review-retry-0001"}
    body = {"decision": "CONFIRMED", "evidence": EVIDENCE}
    url = f"{API}/cases/{mp['id']}/matches/{match['id']}/review"
    assert client.post(url, json=body, headers=h).status_code == 200
    replay = client.post(url, json=body, headers=h)
    assert replay.status_code == 200 and replay.headers.get("Idempotent-Replayed") == "true"
    assert replay.json()["status"] == "CONFIRMED"

    nh = {**actors.helpdesk.headers, "Idempotency-Key": "note-retry-0001"}
    note = {"note_text": "Family waiting at desk 3"}
    a = client.post(f"{API}/cases/{mp['id']}/notes", json=note, headers=nh)
    b = client.post(f"{API}/cases/{mp['id']}/notes", json=note, headers=nh)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert client.get(f"{API}/cases/{mp['id']}/notes", headers=actors.helpdesk.headers).json()["total"] == 1


def test_resolved_cases_cannot_be_edited(client, actors):
    li = create_case(client, actors.helpdesk, "lost-items")
    client.post(
        f"{API}/cases/lost-items/{li['id']}/assign", json={"assignee_user_id": str(actors.police.id)},
        headers=actors.police.headers,
    )
    client.post(
        f"{API}/cases/lost-items/{li['id']}/status",
        json={"status": "RESOLVED", "reason": "Returned", "verification_evidence": "Owner described contents exactly"},
        headers=actors.police.headers,
    )
    r = client.patch(f"{API}/cases/lost-items/{li['id']}", json={"item_name": "Changed"}, headers=actors.police.headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "case_closed"


def test_stale_patch_is_rejected(client, actors):
    mp = create_case(client, actors.helpdesk, "missing-persons")
    url = f"{API}/cases/missing-persons/{mp['id']}"
    assert client.patch(url, json={"language": "Hindi", "expected_version": 1}, headers=actors.helpdesk.headers).status_code == 200
    stale = client.patch(url, json={"language": "Awadhi", "expected_version": 1}, headers=actors.helpdesk.headers)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "case_changed"
