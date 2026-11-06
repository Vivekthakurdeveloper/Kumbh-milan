"""Phase 3 offline synchronization."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text

from app.database import SessionLocal
from app.models import AuditEvent, Case, CaseAttachment, CaseNote, SyncDevice
from tests.helpers import (
    API,
    FOUND_ITEM,
    FOUND_PERSON,
    LOST_ITEM,
    MISSING_PERSON,
    Actors,
    create_case,
    make_actors,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
DEVICE = "android-test-device-1"


@pytest.fixture
def actors(client) -> Actors:
    return make_actors(client)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def push(client, actor, records, device_id=DEVICE, **extra):
    body = {"device_id": device_id, "client_id": "app-1", "records": records, **extra}
    return client.post(f"{API}/sync/push", json=body, headers=actor.headers)


def pull(client, actor, *, cursor=None, limit=50, device_id=DEVICE):
    params = {"device_id": device_id, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    return client.get(f"{API}/sync/pull", params=params, headers=actor.headers)


def status(client, actor, *, device_id=DEVICE, pending=None, failed=None):
    params = {"device_id": device_id}
    if pending is not None:
        params["pending_count"] = pending
    if failed is not None:
        params["failed_count"] = failed
    return client.get(f"{API}/sync/status", params=params, headers=actor.headers)


def _create_record(case_type: str, data: dict, client_record_id: str | None = None) -> dict:
    return {
        "client_record_id": client_record_id or f"cr-{uuid.uuid4().hex[:12]}",
        "operation": "CREATE_CASE",
        "client_created_at": _now(),
        "case_type": case_type,
        "data": data,
    }


def test_offline_missing_person_registration(client, actors):
    record = _create_record("MISSING_PERSON", MISSING_PERSON, "mp-offline-1")
    response = push(client, actors.helpdesk, [record])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["synced"] == 1 and body["failed"] == 0
    result = body["results"][0]
    assert result["outcome"] == "SYNCED"
    assert result["case_id"] and result["case_number"].startswith("KSLF-MP-")
    with SessionLocal() as db:
        case = db.get(Case, uuid.UUID(result["case_id"]))
        assert case.client_record_id == "mp-offline-1"
        assert case.device_id == DEVICE
        assert case.sync_status == "SYNCED"
        assert case.server_synced_at is not None


def test_offline_found_person_registration(client, actors):
    response = push(client, actors.helpdesk, [_create_record("FOUND_PERSON", FOUND_PERSON, "fp-1")])
    assert response.status_code == 200
    assert response.json()["results"][0]["outcome"] == "SYNCED"
    assert response.json()["results"][0]["case_number"].startswith("KSLF-FP-")


def test_lost_item_synchronization(client, actors):
    response = push(client, actors.helpdesk, [_create_record("LOST_ITEM", LOST_ITEM, "li-1")])
    assert response.status_code == 200
    assert response.json()["results"][0]["outcome"] == "SYNCED"
    assert response.json()["results"][0]["case_number"].startswith("KSLF-LI-")


def test_found_item_synchronization(client, actors):
    response = push(client, actors.helpdesk, [_create_record("FOUND_ITEM", FOUND_ITEM, "fi-1")])
    assert response.status_code == 200
    assert response.json()["results"][0]["outcome"] == "SYNCED"
    assert response.json()["results"][0]["case_number"].startswith("KSLF-FI-")


def test_duplicate_sync_request_and_retry_without_duplicates(client, actors):
    record = _create_record("MISSING_PERSON", MISSING_PERSON, "dup-1")
    first = push(client, actors.helpdesk, [record])
    second = push(client, actors.helpdesk, [record])
    third = push(client, actors.helpdesk, [record])
    assert first.json()["results"][0]["outcome"] == "SYNCED"
    assert second.json()["results"][0]["outcome"] == "DUPLICATE"
    assert third.json()["results"][0]["outcome"] == "DUPLICATE"
    case_id = first.json()["results"][0]["case_id"]
    assert second.json()["results"][0]["case_id"] == case_id
    assert third.json()["results"][0]["case_id"] == case_id
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Case).where(Case.client_record_id == "dup-1")) == 1


def test_concurrent_duplicate_push_creates_one_case(client, actors):
    record = _create_record("MISSING_PERSON", MISSING_PERSON, "race-1")

    def once():
        return push(client, actors.helpdesk, [record]).json()["results"][0]["case_id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: once(), range(4)))
    assert len(set(ids)) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Case).where(Case.client_record_id == "race-1")) == 1


def test_successful_synchronization_status_and_audit(client, actors):
    record = _create_record("MISSING_PERSON", MISSING_PERSON, "ok-1")
    pushed = push(client, actors.helpdesk, [record], pending_count=3, failed_count=1)
    assert pushed.status_code == 200 and pushed.json()["synced"] == 1
    st = status(client, actors.helpdesk, pending=2, failed=0)
    assert st.status_code == 200, st.text
    body = st.json()
    assert body["pending_count"] == 2
    assert body["failed_count"] == 0
    assert body["last_successful_push_at"] is not None
    assert body["server_cursor"] is not None
    with SessionLocal() as db:
        actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.action.like("SYNC_%"))))
        assert "SYNC_PUSH" in actions
        device = db.scalar(select(SyncDevice).where(SyncDevice.device_id == DEVICE))
        assert device is not None and device.last_successful_push_at is not None


def test_failed_validation_does_not_block_other_records(client, actors):
    good = _create_record("MISSING_PERSON", MISSING_PERSON, "good-1")
    bad = _create_record("LOST_ITEM", {"item_name": "x"}, "bad-1")  # missing required item_category
    response = push(client, actors.helpdesk, [bad, good])
    assert response.status_code == 200
    results = {r["client_record_id"]: r for r in response.json()["results"]}
    assert results["bad-1"]["outcome"] == "FAILED"
    assert results["bad-1"]["error_code"] in {"validation_failed", "missing"}
    assert results["good-1"]["outcome"] == "SYNCED"
    assert response.json()["synced"] == 1 and response.json()["failed"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Case)) == 1


def test_conflicting_record_updates_and_resolution(client, actors):
    created = push(client, actors.helpdesk, [_create_record("MISSING_PERSON", MISSING_PERSON, "conf-1")])
    case_id = created.json()["results"][0]["case_id"]
    version = created.json()["results"][0]["version"]

    # Server-side update bumps version.
    patch = client.patch(
        f"{API}/cases/missing-persons/{case_id}",
        json={"clothing_description": "updated on server", "expected_version": version},
        headers=actors.police.headers,
    )
    assert patch.status_code == 200, patch.text
    new_version = patch.json()["version"]
    assert new_version != version

    conflict = push(
        client,
        actors.helpdesk,
        [
            {
                "client_record_id": "conf-upd-1",
                "operation": "UPDATE_CASE",
                "client_created_at": _now(),
                "case_id": case_id,
                "base_version": version,
                "data": {"clothing_description": "stale offline edit"},
            }
        ],
    )
    assert conflict.status_code == 200
    result = conflict.json()["results"][0]
    assert result["outcome"] == "CONFLICT"
    assert result["error_code"] == "case_changed"
    assert result["version"] == new_version
    assert result["case"]["details"]["clothing_description"] == "updated on server"

    resolved = push(
        client,
        actors.helpdesk,
        [
            {
                "client_record_id": "conf-upd-2",
                "operation": "UPDATE_CASE",
                "client_created_at": _now(),
                "case_id": case_id,
                "base_version": new_version,
                "data": {"clothing_description": "resolved offline edit"},
            }
        ],
    )
    assert resolved.json()["results"][0]["outcome"] == "SYNCED"
    detail = client.get(f"{API}/cases/missing-persons/{case_id}", headers=actors.helpdesk.headers)
    assert detail.json()["details"]["clothing_description"] == "resolved offline edit"

    # Retry of the same update client_record_id is a duplicate, not a false conflict.
    retry = push(
        client,
        actors.helpdesk,
        [
            {
                "client_record_id": "conf-upd-2",
                "operation": "UPDATE_CASE",
                "client_created_at": _now(),
                "case_id": case_id,
                "base_version": new_version,
                "data": {"clothing_description": "resolved offline edit"},
            }
        ],
    )
    assert retry.json()["results"][0]["outcome"] == "DUPLICATE"
    with SessionLocal() as db:
        assert "SYNC_CONFLICT" in set(db.scalars(select(AuditEvent.action)))


def test_unauthorized_station_synchronization(client, actors):
    # Help desk at station B cannot push a case for station A.
    record = _create_record(
        "MISSING_PERSON",
        {**MISSING_PERSON, "station_id": str(actors.station_a)},
        "foreign-1",
    )
    # Non-admin sending another station_id is forbidden by create_case.
    response = push(client, actors.helpdesk_b, [record])
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["outcome"] == "FAILED"
    assert result["error_code"] in {"station_scope", "forbidden", "permission_denied"}

    # Station A creates a case; station B must not see it on pull.
    push(client, actors.helpdesk, [_create_record("MISSING_PERSON", MISSING_PERSON, "scope-a")])
    pull_b = pull(client, actors.helpdesk_b)
    assert pull_b.status_code == 200
    ids = {item["case"]["id"] for item in pull_b.json()["items"]}
    with SessionLocal() as db:
        a_case = db.scalar(select(Case).where(Case.client_record_id == "scope-a"))
        assert a_case is not None
        assert str(a_case.id) not in ids


def test_photograph_upload_association_and_retry(client, actors):
    created = push(client, actors.helpdesk, [_create_record("FOUND_PERSON", FOUND_PERSON, "photo-case")])
    case_id = created.json()["results"][0]["case_id"]
    form = {
        "media_type": "PHOTO",
        "device_id": DEVICE,
        "client_record_id": "photo-1",
        "client_created_at": _now(),
    }
    first = client.post(
        f"{API}/cases/{case_id}/attachments",
        data=form,
        files={"file": ("child.png", PNG, "image/png")},
        headers=actors.helpdesk.headers,
    )
    assert first.status_code in (200, 201), first.text
    attachment_id = first.json()["id"]
    assert first.json()["case_id"] == case_id

    second = client.post(
        f"{API}/cases/{case_id}/attachments",
        data=form,
        files={"file": ("child.png", PNG, "image/png")},
        headers={**actors.helpdesk.headers, "Idempotency-Key": "photo-retry-key-01"},
    )
    # client_record_id dedupe returns the same attachment.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == attachment_id
    assert second.headers.get("Idempotent-Replayed") == "true"

    third = client.post(
        f"{API}/cases/{case_id}/attachments",
        data=form,
        files={"file": ("child.png", PNG, "image/png")},
        headers={**actors.helpdesk.headers, "Idempotency-Key": "photo-retry-key-01"},
    )
    assert third.json()["id"] == attachment_id

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CaseAttachment)) == 1
        row = db.get(CaseAttachment, uuid.UUID(attachment_id))
        assert row.client_record_id == "photo-1"
        assert row.case_id == uuid.UUID(case_id)

    bad = client.post(
        f"{API}/cases/{case_id}/attachments",
        data={"media_type": "PHOTO"},
        files={"file": ("x.bin", b"MZ\x90\0 not an image", "image/png")},
        headers=actors.helpdesk.headers,
    )
    assert bad.status_code == 415

    huge = client.post(
        f"{API}/cases/{case_id}/attachments",
        data={"media_type": "PHOTO"},
        files={"file": ("big.png", PNG + b"\x00" * (11 * 1024 * 1024), "image/png")},
        headers=actors.helpdesk.headers,
    )
    assert huge.status_code == 413


def test_pull_pagination_and_note_bumps_cursor(client, actors):
    ids = []
    for i in range(5):
        r = push(client, actors.helpdesk, [_create_record("MISSING_PERSON", MISSING_PERSON, f"page-{i}")])
        ids.append(r.json()["results"][0]["case_id"])

    page1 = pull(client, actors.helpdesk, limit=2)
    assert page1.status_code == 200, page1.text
    assert len(page1.json()["items"]) == 2
    assert page1.json()["has_more"] is True
    cursor1 = page1.json()["next_cursor"]
    assert cursor1

    page2 = pull(client, actors.helpdesk, cursor=cursor1, limit=2)
    assert len(page2.json()["items"]) == 2
    assert page2.json()["has_more"] is True

    page3 = pull(client, actors.helpdesk, cursor=page2.json()["next_cursor"], limit=2)
    assert len(page3.json()["items"]) == 1
    assert page3.json()["has_more"] is False

    seen = [item["case"]["id"] for page in (page1, page2, page3) for item in page.json()["items"]]
    assert len(seen) == len(set(seen)) == 5

    # Adding a note bumps sync_changed_at so the case reappears after the previous cursor.
    target = ids[0]
    note = client.post(
        f"{API}/cases/{target}/notes",
        json={
            "note_text": "Seen near the ghat",
            "device_id": DEVICE,
            "client_record_id": "note-1",
            "client_created_at": _now(),
        },
        headers=actors.helpdesk.headers,
    )
    assert note.status_code == 201, note.text

    # Retry note must not duplicate.
    note2 = client.post(
        f"{API}/cases/{target}/notes",
        json={
            "note_text": "Seen near the ghat",
            "device_id": DEVICE,
            "client_record_id": "note-1",
            "client_created_at": _now(),
        },
        headers=actors.helpdesk.headers,
    )
    assert note2.status_code == 201
    assert note2.json()["id"] == note.json()["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CaseNote).where(CaseNote.client_record_id == "note-1")) == 1

    after_note = pull(client, actors.helpdesk, cursor=page3.json()["next_cursor"], limit=10)
    bumped_ids = {item["case"]["id"] for item in after_note.json()["items"]}
    assert target in bumped_ids

    with SessionLocal() as db:
        assert "SYNC_PULL" in set(db.scalars(select(AuditEvent.action)))


def test_sync_push_note_linked_by_case_client_record_id(client, actors):
    case_record = _create_record("MISSING_PERSON", MISSING_PERSON, "parent-case")
    note_record = {
        "client_record_id": "child-note",
        "operation": "ADD_NOTE",
        "client_created_at": _now(),
        "case_client_record_id": "parent-case",
        "data": {"note_text": "Offline note from the same batch"},
    }
    response = push(client, actors.helpdesk, [case_record, note_record])
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results[0]["outcome"] == "SYNCED"
    assert results[1]["outcome"] == "SYNCED"
    assert results[1]["case_id"] == results[0]["case_id"]
    assert results[1]["note_id"]


def test_sync_migration_upgrade_downgrade_preserves_cases(client, actors, migrated_database):
    """Additive migration must not destroy existing case rows (upgrade already applied by fixture)."""
    case = create_case(client, actors.helpdesk, "missing-persons")
    with SessionLocal() as db:
        row = db.get(Case, uuid.UUID(case["id"]))
        assert row.sync_changed_at is not None
        assert row.client_record_id is None
        # Columns exist and are nullable for legacy rows.
        cols = db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'cases' AND column_name IN "
                "('client_record_id', 'device_id', 'sync_changed_at', 'sync_status')"
            )
        ).scalars().all()
        assert set(cols) == {"client_record_id", "device_id", "sync_changed_at", "sync_status"}
        assert db.execute(text("SELECT to_regclass('sync_devices')")).scalar() == "sync_devices"
