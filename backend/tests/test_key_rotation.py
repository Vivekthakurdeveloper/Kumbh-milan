"""Encryption key rotation: dual-read with previous keys, resumable re-encryption, and the append-only guard."""

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.common.crypto import decrypt_text, reset_keyrings
from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, Grievance, GrievanceEvent
from app.services import key_rotation
from tests.helpers import API
from tests.test_operations_modules import _grievance
from tests.test_phase4_public import _verify_mobile


@pytest.fixture
def rekey(monkeypatch):
    settings = get_settings()

    def apply(primary: str, previous: list[str]) -> None:
        monkeypatch.setattr(settings, "contact_encryption_key", primary)
        monkeypatch.setattr(settings, "contact_encryption_previous_keys", previous)
        reset_keyrings()

    yield apply
    monkeypatch.undo()
    reset_keyrings()


def _seed(client, actors) -> str:
    token = _verify_mobile(client, mobile="9876500077", purpose="GRIEVANCE")
    created = _grievance(client, token)
    assert created.status_code == 201, created.text
    with SessionLocal() as db:
        grievance_id = db.scalar(select(Grievance.id).where(Grievance.reference == created.json()["reference"]))
    comment = client.post(f"{API}/grievances/{grievance_id}/comments",
                          json={"message": "Called the complainant back", "internal": True},
                          headers=actors.police.headers)
    assert comment.status_code in (200, 201), comment.text
    return str(grievance_id)


def _detail(client, actors, grievance_id):
    response = client.get(f"{API}/grievances/{grievance_id}", headers=actors.police.headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_rotation_dual_reads_then_re_encrypts_resumably(client, actors, rekey):
    old_key = get_settings().contact_encryption_key
    grievance_id = _seed(client, actors)
    before = _detail(client, actors, grievance_id)
    new_key = Fernet.generate_key().decode()

    rekey(new_key, [])
    with SessionLocal() as db:
        blind = key_rotation.rotate(db, apply=False)
    assert blind["grievances.description_ciphertext"].undecryptable

    rekey(new_key, [old_key])
    assert _detail(client, actors, grievance_id)["description"] == before["description"]
    with SessionLocal() as db:
        dry = key_rotation.rotate(db, apply=False)
        untouched = db.scalar(select(Grievance.description_ciphertext).where(Grievance.id == grievance_id))
    assert dry["grievances.description_ciphertext"].rotated >= 1
    assert dry["grievance_events.body_ciphertext"].rotated >= 1
    with pytest.raises(InvalidToken):
        Fernet(new_key.encode()).decrypt(untouched.encode())

    with SessionLocal() as db:
        applied = key_rotation.rotate(db, apply=True, batch_size=2)
    assert applied["grievances.description_ciphertext"].rotated == dry["grievances.description_ciphertext"].rotated
    assert not any(r.undecryptable for r in applied.values())
    with SessionLocal() as db:
        again = key_rotation.rotate(db, apply=False)
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "ENCRYPTION_KEYS_ROTATED"))
    assert sum(r.rotated for r in again.values()) == 0
    assert audit is not None and "grievances.description_ciphertext" in audit.after_state

    rekey(new_key, [])
    after = _detail(client, actors, grievance_id)
    assert after["description"] == before["description"]
    assert [e.get("message") for e in after["events"]] == [e.get("message") for e in before["events"]]


def test_previous_keys_are_validated():
    from app.config.settings import Settings

    key = Fernet.generate_key().decode()
    for bad, message in (
        ({"contact_encryption_previous_keys": ["not-a-key"]}, "invalid Fernet key"),
        ({"contact_encryption_previous_keys": [key]}, "must not contain the current key"),
        ({"contact_encryption_key": "", "contact_encryption_previous_keys": [key]}, "requires"),
    ):
        with pytest.raises(ValueError, match=message):
            Settings(**{"app_env": "test", "contact_encryption_key": key, **bad})


def test_grievance_event_bodies_only_change_during_rotation(client, actors):
    grievance_id = _seed(client, actors)
    with SessionLocal() as db:
        event = db.scalars(select(GrievanceEvent).where(GrievanceEvent.grievance_id == grievance_id,
                                                        GrievanceEvent.body_ciphertext.is_not(None))).first()
        assert decrypt_text(event.body_ciphertext)
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE grievance_events SET body_ciphertext = 'x' WHERE id = :id"), {"id": event.id})
        db.rollback()
        db.execute(text("SELECT set_config('kumbh.key_rotation', 'on', true)"))
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE grievance_events SET body_ciphertext = 'x', is_internal = NOT is_internal "
                            "WHERE id = :id"), {"id": event.id})
        db.rollback()
        db.execute(text("SELECT set_config('kumbh.key_rotation', 'on', true)"))
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE grievance_events SET body_ciphertext = NULL WHERE id = :id"), {"id": event.id})
        db.rollback()
