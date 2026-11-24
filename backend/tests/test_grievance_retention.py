"""Grievance attachments (scan pipeline reuse, privacy, limits) and grievance retention (holds, anonymisation)."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.database import SessionLocal, engine
from app.models import AuditEvent, CaseAttachment, Grievance, GrievanceEvent
from tests.helpers import API
from tests.test_operations_modules import _grievance
from tests.test_phase4_public import _verify_mobile
from tests.test_retention import _approve_and_execute, _plan, admin2  # noqa: F401 - fixture
from tests.test_scanning import INFECTED_PNG, PNG, _keys, _scanner, clamd, use_scanner  # noqa: F401 - fixtures

MOBILE = "9876500011"


def _submit(client):
    token = _verify_mobile(client, mobile=MOBILE, purpose="GRIEVANCE")
    created = _grievance(client, token)
    assert created.status_code == 201, created.text
    reference = created.json()["reference"]
    with SessionLocal() as db:
        grievance_id = db.scalar(select(Grievance.id).where(Grievance.reference == reference))
    return token, reference, grievance_id


def _citizen_upload(client, token, reference, data=PNG):
    return client.post(
        f"{API}/public/grievances/{reference}/attachments", data={"media_type": "PHOTO"},
        files={"file": ("evidence.png", data, "image/png")}, headers={"X-Verified-Mobile-Token": token},
    )


def _staff_upload(client, actor, grievance_id, data=PNG):
    return client.post(
        f"{API}/grievances/{grievance_id}/attachments", data={"media_type": "PHOTO"},
        files={"file": ("cctv.png", data, "image/png")}, headers=actor.headers,
    )


def _close(client, actors, grievance_id):
    for status_, message in (("RESOLVED", "Desk staff counselled; apology issued"), ("CLOSED", None)):
        body = {"status": status_, **({"message": message} if message else {})}
        r = client.post(f"{API}/grievances/{grievance_id}/status", json=body, headers=actors.police.headers)
        assert r.status_code == 200, r.text


def test_citizen_and_staff_attachments_are_scanned_scoped_and_private(client, actors, storage, clamd, use_scanner):
    use_scanner(_scanner(clamd))
    token, reference, grievance_id = _submit(client)

    first = _citizen_upload(client, token, reference)
    assert first.status_code == 201, first.text
    assert first.json()["scan_status"] == "CLEAN"
    replay = _citizen_upload(client, token, reference)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]

    infected = _citizen_upload(client, token, reference, INFECTED_PNG)
    assert infected.status_code == 422 and infected.json()["error"]["code"] == "malware_detected"
    assert _citizen_upload(client, token, reference, INFECTED_PNG).status_code == 422
    assert _keys(storage, f"quarantine/grievances/{grievance_id}") == []

    other = _verify_mobile(client, mobile="9876500012", purpose="GRIEVANCE")
    assert _citizen_upload(client, other, reference).status_code == 404

    assert _staff_upload(client, actors.helpdesk_b, grievance_id).status_code == 404
    staff = _staff_upload(client, actors.police, grievance_id, PNG + b"staff")
    assert staff.status_code == 201, staff.text
    assert staff.json()["by_citizen"] is False and staff.json()["downloadable"] is True

    public = client.get(f"{API}/public/grievances/{reference}", headers={"X-Verified-Mobile-Token": token}).json()
    assert [a["id"] for a in public["attachments"]] == [first.json()["id"]]

    detail = client.get(f"{API}/grievances/{grievance_id}", headers=actors.helpdesk.headers).json()
    by_status = {a["id"]: a["scan_status"] for a in detail["attachments"]}
    assert by_status[first.json()["id"]] == "CLEAN" and by_status[staff.json()["id"]] == "CLEAN"
    assert sorted(by_status.values()) == ["CLEAN", "CLEAN", "REJECTED"]
    assert detail["legal_hold_active"] is False and detail["anonymized_at"] is None

    url = f"{API}/grievances/{grievance_id}/attachments/{first.json()['id']}"
    assert client.get(url, headers=actors.helpdesk_b.headers).status_code == 404
    download = client.get(url, headers=actors.helpdesk.headers)
    assert download.status_code == 200, download.text
    assert download.json()["url"] and download.json()["content_type"] == "image/png"

    with SessionLocal() as db:
        row = db.get(CaseAttachment, uuid.UUID(first.json()["id"]))
        assert row.storage_key == f"grievances/{grievance_id}/{row.id.hex}"
        actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.entity_id.in_([grievance_id, row.id]))))
    assert {"GRIEVANCE_ATTACHMENT_ADDED", "ATTACHMENT_SCAN_CLEAN", "ATTACHMENT_ACCESSED"} <= actions


def test_attachment_limit_and_closed_grievances(client, actors, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "grievance_max_attachments", 1)
    token, reference, grievance_id = _submit(client)
    assert _citizen_upload(client, token, reference).status_code == 201
    over = _citizen_upload(client, token, reference, PNG + b"second")
    assert over.status_code == 409 and over.json()["error"]["code"] == "attachment_limit"

    _close(client, actors, grievance_id)
    closed = _staff_upload(client, actors.police, grievance_id, PNG + b"late")
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "grievance_closed"


def test_grievance_legal_hold_blocks_anonymisation(client, actors, admin2):
    _token, _reference, grievance_id = _submit(client)
    _close(client, actors, grievance_id)
    with engine.begin() as conn:
        conn.execute(text("UPDATE grievances SET closed_at = now() - interval '400 days'"))

    holds = f"{API}/grievances/{grievance_id}/legal-holds"
    assert client.post(holds, json={"reason": "RTI appeal pending"}, headers=actors.police.headers).status_code == 403
    hold = client.post(holds, json={"reason": "RTI appeal pending", "reference": "RTI/2026/44"},
                       headers=actors.admin.headers)
    assert hold.status_code == 201, hold.text
    assert hold.json()["grievance_id"] == str(grievance_id) and hold.json()["case_id"] is None
    assert client.get(f"{API}/grievances/{grievance_id}", headers=actors.admin.headers).json()["legal_hold_active"]

    run = _plan(client, actors.admin, "ANONYMIZE_CLOSED_GRIEVANCES")
    assert run["plan_report"]["eligible"] == 0 and run["plan_report"]["excluded_by_legal_hold"] == 1

    release = client.post(f"{holds}/{hold.json()['id']}/release", json={"reason": "Appeal disposed"},
                          headers=actors.admin.headers)
    assert release.status_code == 200 and release.json()["released_at"]
    again = client.post(f"{holds}/{hold.json()['id']}/release", json={"reason": "Appeal disposed"},
                        headers=actors.admin.headers)
    assert again.status_code == 409
    assert len(client.get(holds, headers=actors.admin.headers).json()) == 1
    assert _plan(client, actors.admin, "ANONYMIZE_CLOSED_GRIEVANCES")["plan_report"]["eligible"] == 1


def test_grievance_anonymisation_removes_personal_data(client, actors, admin2, storage):
    token, reference, grievance_id = _submit(client)
    upload = _citizen_upload(client, token, reference)
    assert upload.status_code == 201
    client.post(f"{API}/public/grievances/{reference}/comments", json={"message": "My number is 9876500011"},
                headers={"X-Verified-Mobile-Token": token})
    _close(client, actors, grievance_id)
    _recent_token, _recent_ref, recent_id = _submit(client)
    _close(client, actors, recent_id)
    with engine.begin() as conn:
        conn.execute(text("UPDATE grievances SET closed_at = now() - interval '400 days' WHERE id = :id"),
                     {"id": grievance_id})
        key = conn.execute(text("SELECT storage_key FROM case_attachments WHERE id = :id"),
                           {"id": upload.json()["id"]}).scalar_one()

    run = _plan(client, actors.admin, "ANONYMIZE_CLOSED_GRIEVANCES")
    assert run["plan_report"]["eligible"] == 1 and run["plan_report"]["sample_case_numbers"] == [reference]
    done = _approve_and_execute(client, actors.admin, admin2, run)
    assert done["status"] == "EXECUTED" and done["execution_report"]["done"] == 1, done

    marker = f"[redacted by retention run {run['id']}]"
    with SessionLocal() as db:
        row = db.get(Grievance, grievance_id)
        assert row.anonymized_at and row.subject == marker and row.reference == reference
        assert row.mobile_ciphertext is None and row.mobile_lookup_hash is None and row.mobile_hint is None
        assert row.status == "CLOSED" and row.category == "DELAY"
        bodies = set(db.scalars(select(GrievanceEvent.body_ciphertext).where(GrievanceEvent.grievance_id == grievance_id)))
        assert bodies == {None}
        attachment = db.get(CaseAttachment, uuid.UUID(upload.json()["id"]))
        assert attachment.object_purged_at and attachment.original_filename is None
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.entity_id == grievance_id,
                                                     AuditEvent.action == "GRIEVANCE_ANONYMIZED"))
        recent = db.get(Grievance, recent_id)
        assert recent.anonymized_at is None and recent.mobile_lookup_hash is not None
    assert _keys(storage, key) == []

    detail = client.get(f"{API}/grievances/{grievance_id}", headers=actors.admin.headers).json()
    assert detail["description"] == marker and "9876500011" not in str(detail)
    assert client.get(f"{API}/public/grievances/{reference}", headers={"X-Verified-Mobile-Token": token}).status_code == 404
    reopen = client.post(f"{API}/grievances/{grievance_id}/status", json={"status": "REOPENED"},
                         headers=actors.admin.headers)
    assert reopen.status_code == 409 and reopen.json()["error"]["code"] == "grievance_anonymized"


def test_grievance_events_stay_append_only_outside_executing_runs(client, actors, admin2):
    _submit(client)
    run = _plan(client, actors.admin, "ANONYMIZE_CLOSED_GRIEVANCES")

    def attempt(sql: str, run_id: str | None = None):
        with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                with conn.begin():
                    if run_id:
                        conn.execute(text("SELECT set_config('kumbh.retention_run_id', :r, true)"), {"r": run_id})
                    conn.execute(text(sql))

    attempt("UPDATE grievance_events SET body_ciphertext = NULL")
    attempt("UPDATE grievance_events SET body_ciphertext = NULL", run["id"])  # run is only PLANNED
    with engine.begin() as conn:
        conn.execute(text("UPDATE retention_runs SET status = 'EXECUTING', approved_by_user_id = :a WHERE id = :id"),
                     {"a": admin2.id, "id": run["id"]})
    attempt("UPDATE grievance_events SET body_ciphertext = 'rewritten'", run["id"])
    attempt("UPDATE grievance_events SET body_ciphertext = NULL, is_internal = true", run["id"])
    attempt("DELETE FROM grievance_events", run["id"])
