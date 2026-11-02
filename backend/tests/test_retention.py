import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.database import SessionLocal, engine
from app.models import AuditEvent, Case, CaseAttachment, CaseContact, CaseNote, CaseStatusHistory, MissingPersonDetails, Notification
from app.models.enums import Role
from tests.helpers import API, MISSING_PERSON, Actor, create_case, create_user, login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128


@pytest.fixture
def admin2(client) -> Actor:
    user_id = create_user("admin2", Role.ADMIN, None)
    headers, refresh = login(client, "admin2")
    return Actor(user_id, "admin2", None, headers, refresh)


def _close(client, actors, case, kind="missing-persons"):
    r = client.post(f"{API}/cases/{kind}/{case['id']}/status",
                    json={"status": "CLOSED", "reason": "Family reunited at desk 3"}, headers=actors.police.headers)
    assert r.status_code == 200, r.text


def _age(case_id, *, updated_days=None, closed_days=None, archived_days=None):
    with engine.begin() as conn:
        if updated_days is not None:
            conn.execute(text("UPDATE cases SET updated_at = now() - make_interval(days => :d) WHERE id = :id"),
                         {"d": updated_days, "id": case_id})
        if closed_days is not None:
            conn.execute(text("UPDATE cases SET closed_at = now() - make_interval(days => :d) WHERE id = :id"),
                         {"d": closed_days, "id": case_id})
        if archived_days is not None:
            conn.execute(text("UPDATE cases SET archived_at = now() - make_interval(days => :d) WHERE id = :id"),
                         {"d": archived_days, "id": case_id})


def _plan(client, admin, policy):
    r = client.post(f"{API}/retention/runs", json={"policy": policy}, headers=admin.headers)
    assert r.status_code == 201, r.text
    return r.json()


def _approve_and_execute(client, planner, approver, run):
    assert client.post(f"{API}/retention/runs/{run['id']}/approve", headers=approver.headers).status_code == 200
    r = client.post(f"{API}/retention/runs/{run['id']}/execute", headers=planner.headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_retention_is_admin_only(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    for method, url in [("get", "/retention/policies"), ("get", "/retention/runs"),
                        ("get", f"/cases/{case['id']}/legal-holds")]:
        assert getattr(client, method)(f"{API}{url}", headers=actors.police.headers).status_code == 403
    assert client.post(f"{API}/retention/runs", json={"policy": "ARCHIVE_CLOSED_CASES"}, headers=actors.police.headers).status_code == 403
    policies = client.get(f"{API}/retention/policies", headers=actors.admin.headers).json()
    assert {p["policy"] for p in policies} >= {"ARCHIVE_CLOSED_CASES", "ANONYMIZE_ARCHIVED_CASES", "PURGE_ATTACHMENT_OBJECTS", "AUDIT"}


def test_plan_is_a_dry_run_and_respects_holds_and_status(client, actors):
    old_closed = create_case(client, actors.helpdesk, "missing-persons")
    held = create_case(client, actors.helpdesk, "missing-persons")
    recent_closed = create_case(client, actors.helpdesk, "missing-persons")
    old_open = create_case(client, actors.helpdesk, "missing-persons")
    for case in (old_closed, held, recent_closed):
        _close(client, actors, case)
    for case in (old_closed, held):
        _age(case["id"], closed_days=200)
    _age(old_open["id"], updated_days=200)
    _age(recent_closed["id"], updated_days=200)
    hold = client.post(f"{API}/cases/{held['id']}/legal-holds",
                       json={"reason": "Court order pending in custody dispute", "reference": "CRL-17/2027"},
                       headers=actors.admin.headers)
    assert hold.status_code == 201

    run = _plan(client, actors.admin, "ARCHIVE_CLOSED_CASES")
    report = run["plan_report"]
    assert run["status"] == "PLANNED" and report["eligible"] == 1 and report["excluded_by_legal_hold"] == 1
    assert report["sample_case_numbers"] == [old_closed["case_number"]]
    assert "items" not in report
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).where(Case.archived_at.is_not(None))) == 0


def test_two_person_approval_and_archive(client, actors, admin2):
    case = create_case(client, actors.helpdesk, "missing-persons")
    _close(client, actors, case)
    _age(case["id"], closed_days=200)
    run = _plan(client, actors.admin, "ARCHIVE_CLOSED_CASES")
    early = client.post(f"{API}/retention/runs/{run['id']}/execute", headers=actors.admin.headers)
    assert early.status_code == 409 and early.json()["error"]["code"] == "retention_run_state"
    self_approve = client.post(f"{API}/retention/runs/{run['id']}/approve", headers=actors.admin.headers)
    assert self_approve.status_code == 409 and self_approve.json()["error"]["code"] == "two_person_rule"

    done = _approve_and_execute(client, actors.admin, admin2, run)
    assert done["status"] == "EXECUTED" and done["execution_report"]["done"] == 1
    assert done["approved_by_user_id"] == str(admin2.id)
    again = client.post(f"{API}/retention/runs/{run['id']}/execute", headers=actors.admin.headers)
    assert again.status_code == 409

    # Archived: hidden from help desk, hidden from default lists, still readable by police.
    assert client.get(f"{API}/cases/{case['id']}", headers=actors.helpdesk.headers).status_code == 404
    police_view = client.get(f"{API}/cases/{case['id']}", headers=actors.police.headers).json()
    assert police_view["archived_at"] is not None and police_view["details"]["person_name"] == MISSING_PERSON.get("person_name")
    assert case["id"] not in client.get(f"{API}/cases", headers=actors.police.headers).text
    assert case["id"] in client.get(f"{API}/cases", params={"include_archived": True}, headers=actors.police.headers).text
    with SessionLocal() as db:
        actions = list(db.scalars(select(AuditEvent.action).where(AuditEvent.entity_type == "retention_run")))
        assert actions.count("RETENTION_RUN_PLANNED") == 1 and "RETENTION_RUN_APPROVED" in actions
        assert "RETENTION_RUN_EXECUTED" in actions
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.case_id == uuid.UUID(case["id"]), AuditEvent.action == "CASE_ARCHIVED"))

    reopened = client.post(f"{API}/cases/missing-persons/{case['id']}/status",
                           json={"status": "REOPENED", "reason": "Family reported the child missing again"},
                           headers=actors.police.headers)
    assert reopened.status_code == 200, reopened.text
    assert client.get(f"{API}/cases/{case['id']}", headers=actors.police.headers).json()["archived_at"] is None


def test_eligibility_is_rechecked_at_execution(client, actors, admin2):
    case = create_case(client, actors.helpdesk, "missing-persons")
    _close(client, actors, case)
    _age(case["id"], closed_days=200)
    run = _plan(client, actors.admin, "ARCHIVE_CLOSED_CASES")
    client.post(f"{API}/cases/{case['id']}/legal-holds", json={"reason": "Inquiry opened by SSP Haridwar"},
                headers=admin2.headers)
    done = _approve_and_execute(client, actors.admin, admin2, run)
    assert done["execution_report"] == {"done": 0, "skipped": 1, "failed": 0, "failures": []}
    with SessionLocal() as db:
        assert db.get(Case, uuid.UUID(case["id"])).archived_at is None


def test_legal_hold_release(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    hold = client.post(f"{API}/cases/{case['id']}/legal-holds", json={"reason": "Preserve for inquiry"},
                       headers=actors.admin.headers).json()
    url = f"{API}/cases/{case['id']}/legal-holds/{hold['id']}/release"
    released = client.post(url, json={"reason": "Inquiry closed by order"}, headers=actors.admin.headers)
    assert released.status_code == 200 and released.json()["released_at"]
    assert client.post(url, json={"reason": "again please"}, headers=actors.admin.headers).status_code == 409


def test_cancelled_runs_cannot_be_approved(client, actors, admin2):
    run = _plan(client, actors.admin, "ARCHIVE_CLOSED_CASES")
    r = client.post(f"{API}/retention/runs/{run['id']}/cancel", json={"reason": "Planned by mistake"}, headers=admin2.headers)
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert client.post(f"{API}/retention/runs/{run['id']}/approve", headers=admin2.headers).status_code == 409


def _rich_case(client, actors, storage):
    case = create_case(client, actors.helpdesk, "missing-persons", {**MISSING_PERSON, "person_name": "Raju Kumar",
                                                                     "latitude": 29.9563, "longitude": 78.1716,
                                                                     "coordinate_source": "DEVICE_GPS"})
    note = client.post(f"{API}/cases/{case['id']}/notes", json={"note_text": "Father Ramesh at desk 3, phone 98765"},
                       headers=actors.helpdesk.headers)
    assert note.status_code == 201
    upload = client.post(f"{API}/cases/{case['id']}/attachments", data={"media_type": "PHOTO"},
                         files={"file": ("p.png", PNG, "image/png")}, headers=actors.helpdesk.headers)
    assert upload.status_code == 201, upload.text
    sms = client.post(f"{API}/cases/{case['id']}/notifications", headers=actors.helpdesk.headers,
                      json={"template": "CASE_REGISTERED", "recipient_phone": "9876543210",
                            "consent_statement": "Father agreed verbally at desk 3"})
    assert sms.status_code == 202, sms.text
    _close(client, actors, case)
    _age(case["id"], updated_days=500, archived_days=400)
    return case, upload.json()


def test_anonymisation_removes_personal_data_and_keeps_skeleton_and_audit(client, actors, admin2, storage):
    case, attachment = _rich_case(client, actors, storage)
    case_id = uuid.UUID(case["id"])
    with SessionLocal() as db:
        audit_before = db.scalar(select(func.count(AuditEvent.id)))
        key = db.get(CaseAttachment, uuid.UUID(attachment["id"])).storage_key

    run = _plan(client, actors.admin, "ANONYMIZE_ARCHIVED_CASES")
    assert run["plan_report"]["eligible"] == 1
    done = _approve_and_execute(client, actors.admin, admin2, run)
    assert done["status"] == "EXECUTED", done["execution_report"]

    marker = f"[redacted by retention run {run['id']}]"
    with SessionLocal() as db:
        row = db.get(Case, case_id)
        assert row.anonymized_at and row.case_number == case["case_number"] and row.status == "CLOSED"
        assert row.latitude is None and row.search_text == row.case_number
        detail = db.get(MissingPersonDetails, case_id)
        assert detail.person_name is None and detail.clothing_description is None and detail.identifying_marks is None
        assert detail.age_group == "CHILD" and detail.gender == "MALE"  # statistics kept
        assert db.get(CaseContact, case_id) is None
        [sms] = db.scalars(select(Notification).where(Notification.case_id == case_id)).all()
        assert sms.status == "CANCELLED" and sms.recipient_ciphertext is None and sms.recipient_hint is None
        assert set(db.scalars(select(CaseNote.note_text).where(CaseNote.case_id == case_id))) == {marker}
        reasons = set(db.scalars(select(CaseStatusHistory.reason).where(CaseStatusHistory.case_id == case_id)))
        assert reasons == {marker}
        att = db.get(CaseAttachment, uuid.UUID(attachment["id"]))
        assert att.object_purged_at and att.deleted_at and att.original_filename is None
        # Audit trail only grows.
        assert db.scalar(select(func.count(AuditEvent.id))) > audit_before
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.case_id == case_id, AuditEvent.action == "CASE_ANONYMIZED"))
    assert storage.client.list_objects_v2(Bucket=storage.bucket, Prefix=key).get("KeyCount", 0) == 0
    assert "Raju" not in client.get(f"{API}/cases/{case['id']}", headers=actors.police.headers).text

    reopen = client.post(f"{API}/cases/missing-persons/{case['id']}/status",
                         json={"status": "REOPENED", "reason": "Trying to reopen anonymised case"}, headers=actors.police.headers)
    assert reopen.status_code == 409 and reopen.json()["error"]["code"] == "case_anonymized"


def test_append_only_tables_stay_protected_outside_executing_runs(client, actors, admin2):
    case = create_case(client, actors.helpdesk, "missing-persons")
    client.post(f"{API}/cases/{case['id']}/notes", json={"note_text": "original note"}, headers=actors.helpdesk.headers)
    run = _plan(client, actors.admin, "ANONYMIZE_ARCHIVED_CASES")
    marker = f"[redacted by retention run {run['id']}]"

    def attempt(sql: str, run_id: str | None = None, **params):
        with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                with conn.begin():
                    if run_id:
                        conn.execute(text("SELECT set_config('kumbh.retention_run_id', :r, true)"), {"r": run_id})
                    conn.execute(text(sql), params)

    update_note = "UPDATE case_notes SET note_text = :t"
    attempt(update_note, t=marker)  # no run id
    attempt(update_note, run["id"], t=marker)  # run is only PLANNED
    with engine.begin() as conn:  # simulate an executing anonymisation run
        conn.execute(text("UPDATE retention_runs SET status = 'EXECUTING', approved_by_user_id = :a WHERE id = :id"),
                     {"a": admin2.id, "id": run["id"]})
    attempt(update_note, run["id"], t="rewritten history")  # only the exact marker is allowed
    attempt("UPDATE case_notes SET note_text = :t, is_sensitive = true", run["id"], t=marker)  # other columns frozen
    attempt("DELETE FROM case_notes", run["id"])
    attempt("UPDATE audit_events SET reason = 'x'", run["id"])
    attempt("DELETE FROM audit_events", run["id"])


def test_purge_attachment_objects(client, actors, admin2, storage):
    case = create_case(client, actors.helpdesk, "missing-persons")
    upload = client.post(f"{API}/cases/{case['id']}/attachments", data={"media_type": "PHOTO"},
                         files={"file": ("p.png", PNG, "image/png")}, headers=actors.helpdesk.headers).json()
    client.request("DELETE", f"{API}/cases/{case['id']}/attachments/{upload['id']}",
                   json={"reason": "Wrong child photographed"}, headers=actors.police.headers)
    with engine.begin() as conn:
        conn.execute(text("UPDATE case_attachments SET deleted_at = now() - interval '40 days'"))
    with SessionLocal() as db:
        key = db.get(CaseAttachment, uuid.UUID(upload["id"])).storage_key
    run = _plan(client, actors.admin, "PURGE_ATTACHMENT_OBJECTS")
    assert run["plan_report"]["eligible"] == 1
    assert _approve_and_execute(client, actors.admin, admin2, run)["execution_report"]["done"] == 1
    assert storage.client.list_objects_v2(Bucket=storage.bucket, Prefix=key).get("KeyCount", 0) == 0
    with SessionLocal() as db:
        assert db.get(CaseAttachment, uuid.UUID(upload["id"])).object_purged_at is not None


def test_anonymisation_via_api_is_refused_in_production(client, actors, admin2, monkeypatch):
    from app.config import get_settings

    run = _plan(client, actors.admin, "ANONYMIZE_ARCHIVED_CASES")
    client.post(f"{API}/retention/runs/{run['id']}/approve", headers=admin2.headers)
    monkeypatch.setattr(get_settings(), "app_env", "production")
    r = client.post(f"{API}/retention/runs/{run['id']}/execute", headers=actors.admin.headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "execute_via_retention_task"


def test_retention_execute_cli(client, actors, admin2, storage, monkeypatch):
    from app import cli

    case, _ = _rich_case(client, actors, storage)
    run = _plan(client, actors.admin, "ANONYMIZE_ARCHIVED_CASES")
    client.post(f"{API}/retention/runs/{run['id']}/approve", headers=admin2.headers)
    monkeypatch.setattr(cli, "get_storage", lambda: storage)
    assert cli.main(["retention-execute", run["id"]]) == 0
    with SessionLocal() as db:
        assert db.get(Case, uuid.UUID(case["id"])).anonymized_at is not None
    assert cli.main(["retention-execute", run["id"]]) == 2  # already executed
