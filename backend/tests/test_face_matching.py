"""End-to-end face matching against Postgres + moto. Uses the real OpenCV model."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.faces.engine import get_face_engine
from app.models import AuditEvent, CaseMatch, FaceEmbedding, StaffNotification
from app.models.enums import CaseStatus, FaceStatus, MatchSource, MatchStatus
from tests.helpers import API, create_case

BACKEND = Path(__file__).resolve().parents[1]
MODEL_DIR = BACKEND / "models" / "faces"
FIXTURES = BACKEND / "tests" / "fixtures" / "faces"
REQUIRED = ("person_a_1.jpg", "person_a_2.jpg", "person_b_1.jpg", "no_face.jpg")
MODELS = ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx")
EVIDENCE = "Officer compared both photographs side by side with the family present"


def _ready() -> bool:
    return all((MODEL_DIR / name).is_file() for name in MODELS) and all(
        (FIXTURES / name).is_file() for name in REQUIRED
    )


pytestmark = pytest.mark.skipif(not _ready(), reason="Run: python -m app.cli fetch-face-models && fetch-face-fixtures")


@pytest.fixture
def face_enabled(monkeypatch):
    monkeypatch.setenv("FACE_MATCHING_ENABLED", "true")
    get_settings.cache_clear()
    get_face_engine.cache_clear()
    yield
    monkeypatch.setenv("FACE_MATCHING_ENABLED", "false")
    get_settings.cache_clear()
    get_face_engine.cache_clear()


def _photo(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _upload(client, actor, case_id, data: bytes, name="photo.jpg"):
    return client.post(
        f"{API}/cases/{case_id}/attachments",
        data={"media_type": "PHOTO"},
        files={"file": (name, data, "image/jpeg")},
        headers=actor.headers,
    )


def _review(client, actor, case_id, match_id, decision="CONFIRMED"):
    return client.post(
        f"{API}/cases/{case_id}/matches/{match_id}/review",
        json={"decision": decision, "evidence": EVIDENCE},
        headers=actor.headers,
    )


def _assert_no_embedding_leak(payload):
    text = json.dumps(payload)
    assert "embedding" not in text
    assert "ciphertext" not in text


def test_end_to_end_face_candidate_two_person_approval(client, actors, face_enabled):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    found = create_case(client, actors.helpdesk, "found-persons")

    up_m = _upload(client, actors.helpdesk, missing["id"], _photo("person_a_1.jpg"), "missing.jpg")
    assert up_m.status_code == 201, up_m.text
    assert up_m.json()["face_status"] == "FACE_FOUND"

    up_f = _upload(client, actors.helpdesk, found["id"], _photo("person_a_2.jpg"), "found.jpg")
    assert up_f.status_code == 201, up_f.text
    assert up_f.json()["face_status"] == "FACE_FOUND"

    with SessionLocal() as db:
        embeddings = db.scalars(select(FaceEmbedding)).all()
        assert len(embeddings) == 2
        assert all(row.status == FaceStatus.FACE_FOUND and row.embedding_ciphertext for row in embeddings)
        matches = list(db.scalars(select(CaseMatch).where(CaseMatch.source == MatchSource.FACE)))
        assert len(matches) == 1
        match = matches[0]
        assert match.status == MatchStatus.PROPOSED
        assert float(match.face_similarity) >= get_settings().face_similarity_threshold
        assert match.left_attachment_id and match.right_attachment_id
        notifications = list(db.scalars(select(StaffNotification)))
        assert len(notifications) >= 1
        assert {n.recipient_user_id for n in notifications} >= {actors.police.id, actors.police2.id}

    queue = client.get(f"{API}/matches", params={"source": "FACE"}, headers=actors.police.headers)
    assert queue.status_code == 200 and queue.json()["total"] >= 1
    item = queue.json()["items"][0]
    match_id = item["id"]
    assert item["review_stage"] == "AWAITING_FIRST_APPROVAL"
    assert item["face_similarity"] is not None
    _assert_no_embedding_leak(item)

    detail = client.get(f"{API}/matches/{match_id}", headers=actors.police.headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["left_case_id"] == missing["id"] or body["right_case_id"] == missing["id"]
    assert body["face_similarity_threshold"] == get_settings().face_similarity_threshold
    _assert_no_embedding_leak(body)

    left_photo = client.get(f"{API}/matches/{match_id}/photos/left", headers=actors.police.headers)
    right_photo = client.get(f"{API}/matches/{match_id}/photos/right", headers=actors.police.headers)
    assert left_photo.status_code == 200 and right_photo.status_code == 200
    assert "url" in left_photo.json() and left_photo.json()["attachment_id"]

    notes = client.get(f"{API}/staff-notifications", headers=actors.police.headers)
    assert notes.status_code == 200 and notes.json()["total"] >= 1
    note = notes.json()["items"][0]
    assert note["link"] == f"/matches/{match_id}"
    assert client.post(f"{API}/staff-notifications/{note['id']}/read", headers=actors.police.headers).status_code == 200

    first = _review(client, actors.police, missing["id"], match_id)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "PROPOSED"
    assert first.json()["review_stage"] == "AWAITING_SECOND_APPROVAL"
    assert first.json()["first_approved_by_user_id"] == str(actors.police.id)

    same = _review(client, actors.police, missing["id"], match_id)
    assert same.status_code == 403
    assert same.json()["error"]["code"] == "separation_of_duties"

    second = _review(client, actors.police2, missing["id"], match_id)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "CONFIRMED"
    assert second.json()["review_stage"] == "CONFIRMED"

    for case_id in (missing["id"], found["id"]):
        status = client.get(f"{API}/cases/{case_id}", headers=actors.police.headers).json()["status"]
        assert status == CaseStatus.MATCH_FOUND
        assert status != CaseStatus.RESOLVED

    history = client.get(f"{API}/matches/{match_id}/history", headers=actors.police.headers)
    assert history.status_code == 200
    actions = {event["action"] for event in history.json()}
    assert "FACE_MATCH_CANDIDATE_GENERATED" in actions or "MATCH_FIRST_APPROVAL" in actions
    assert "MATCH_FIRST_APPROVAL" in actions
    assert "MATCH_REVIEWED" in actions

    with SessionLocal() as db:
        audit_actions = set(db.scalars(select(AuditEvent.action)).all())
        for required in (
            "FACE_ANALYZED",
            "FACE_COMPARISON_INITIATED",
            "FACE_MATCH_CANDIDATE_GENERATED",
            "MATCH_FIRST_APPROVAL",
            "MATCH_REVIEWED",
        ):
            assert required in audit_actions


def test_no_match_when_different_people(client, actors, face_enabled):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    found = create_case(client, actors.helpdesk, "found-persons")
    assert _upload(client, actors.helpdesk, missing["id"], _photo("person_a_1.jpg")).status_code == 201
    assert _upload(client, actors.helpdesk, found["id"], _photo("person_b_1.jpg")).status_code == 201
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CaseMatch).where(CaseMatch.source == MatchSource.FACE)) == 0
        assert db.scalar(select(func.count()).select_from(FaceEmbedding).where(FaceEmbedding.status == FaceStatus.FACE_FOUND)) == 2


def test_no_face_and_multiple_faces_store_status_without_candidates(client, actors, face_enabled):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    found = create_case(client, actors.helpdesk, "found-persons")
    none = _upload(client, actors.helpdesk, missing["id"], _photo("no_face.jpg"), "none.jpg")
    assert none.status_code == 201 and none.json()["face_status"] == "NO_FACE"

    img_a = cv2.imdecode(np.frombuffer(_photo("person_a_1.jpg"), np.uint8), cv2.IMREAD_COLOR)
    img_b = cv2.imdecode(np.frombuffer(_photo("person_b_1.jpg"), np.uint8), cv2.IMREAD_COLOR)
    height = min(img_a.shape[0], img_b.shape[0])
    img_a = cv2.resize(img_a, (int(img_a.shape[1] * height / img_a.shape[0]), height))
    img_b = cv2.resize(img_b, (int(img_b.shape[1] * height / img_b.shape[0]), height))
    ok, encoded = cv2.imencode(".jpg", np.hstack([img_a, img_b]))
    assert ok
    multi = _upload(client, actors.helpdesk, found["id"], encoded.tobytes(), "group.jpg")
    assert multi.status_code == 201 and multi.json()["face_status"] == "MULTIPLE_FACES"

    with SessionLocal() as db:
        statuses = set(db.scalars(select(FaceEmbedding.status)).all())
        assert FaceStatus.NO_FACE in statuses
        assert FaceStatus.MULTIPLE_FACES in statuses
        assert db.scalar(select(func.count()).select_from(CaseMatch)) == 0


def test_duplicate_candidate_prevention_and_enrichment(client, actors, face_enabled):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    found = create_case(client, actors.helpdesk, "found-persons")
    assert _upload(client, actors.helpdesk, missing["id"], _photo("person_a_1.jpg")).status_code == 201
    assert _upload(client, actors.helpdesk, found["id"], _photo("person_a_2.jpg")).status_code == 201
    with SessionLocal() as db:
        first_count = db.scalar(select(func.count()).select_from(CaseMatch))
        assert first_count == 1
        match_id = db.scalar(select(CaseMatch.id))

    # Re-upload another photo of the same person on the found case: still one pair.
    assert _upload(client, actors.helpdesk, found["id"], _photo("person_a_1.jpg"), "found2.jpg").status_code == 201
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CaseMatch)) == 1
        assert db.scalar(select(CaseMatch.id)) == match_id


def test_rejection_ends_candidate(client, actors, face_enabled):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    found = create_case(client, actors.helpdesk, "found-persons")
    assert _upload(client, actors.helpdesk, missing["id"], _photo("person_a_1.jpg")).status_code == 201
    assert _upload(client, actors.helpdesk, found["id"], _photo("person_a_2.jpg")).status_code == 201
    with SessionLocal() as db:
        match_id = str(db.scalar(select(CaseMatch.id)))
    rejected = _review(client, actors.police, missing["id"], match_id, decision="REJECTED")
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert rejected.json()["review_stage"] == "REJECTED"
    # Pair must never be suggested again.
    assert _upload(client, actors.helpdesk, found["id"], _photo("person_a_1.jpg"), "again.jpg").status_code == 201
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CaseMatch)) == 1
        assert db.scalar(select(CaseMatch.status)) == MatchStatus.REJECTED


def test_unauthorized_access_blocked(client, actors, face_enabled):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    found = create_case(client, actors.helpdesk, "found-persons")
    assert _upload(client, actors.helpdesk, missing["id"], _photo("person_a_1.jpg")).status_code == 201
    assert _upload(client, actors.helpdesk, found["id"], _photo("person_a_2.jpg")).status_code == 201
    with SessionLocal() as db:
        match_id = str(db.scalar(select(CaseMatch.id)))

    assert client.get(f"{API}/matches/{match_id}/photos/left", headers=actors.helpdesk.headers).status_code == 403
    assert _review(client, actors.helpdesk, missing["id"], match_id).status_code == 403
    assert client.get(f"{API}/matches/{match_id}/photos/left", headers=actors.police_b.headers).status_code == 404
    assert client.get(f"{API}/matches/{match_id}", headers=actors.police_b.headers).status_code == 404

    detail = client.get(f"{API}/matches/{match_id}", headers=actors.police.headers).json()
    _assert_no_embedding_leak(detail)


def test_attachment_deletion_removes_embedding(client, actors, face_enabled):
    missing = create_case(client, actors.helpdesk, "missing-persons")
    upload = _upload(client, actors.helpdesk, missing["id"], _photo("person_a_1.jpg"))
    assert upload.status_code == 201
    attachment_id = upload.json()["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(FaceEmbedding)) == 1
    deleted = client.request(
        "DELETE",
        f"{API}/cases/{missing['id']}/attachments/{attachment_id}",
        json={"reason": "Wrong photograph uploaded for this case"},
        headers=actors.police.headers,
    )
    assert deleted.status_code == 204, deleted.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(FaceEmbedding)) == 0
        assert "FACE_EMBEDDING_DELETED" in set(db.scalars(select(AuditEvent.action)).all())


def test_unit_threshold_with_fake_engine(monkeypatch, face_enabled):
    """Isolated comparison logic: fixed vectors, no OpenCV."""
    from app.faces.engine import FaceAnalysis, FaceStatus
    from app.services import faces as face_service

    class FakeEngine:
        name = "fake_engine"

        def analyze(self, image_bytes: bytes) -> FaceAnalysis:
            return FaceAnalysis(FaceStatus.FACE_FOUND, 1, 0.99, np.array([1.0, 0.0], dtype=np.float32))

        def similarity(self, a, b) -> float:
            return float(np.dot(a, b))

    monkeypatch.setattr(face_service, "get_face_engine", lambda: FakeEngine())
    engine = FakeEngine()
    high = engine.similarity(np.array([1.0, 0.0], dtype=np.float32), np.array([1.0, 0.0], dtype=np.float32))
    low = engine.similarity(np.array([1.0, 0.0], dtype=np.float32), np.array([0.0, 1.0], dtype=np.float32))
    assert high >= get_settings().face_similarity_threshold
    assert low < get_settings().face_similarity_threshold
