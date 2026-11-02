import hashlib
import io
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.common.errors import ServiceUnavailable
from app.database import SessionLocal
from app.models import CaseAttachment
from app.models.enums import MediaType
from app.storage.scanner import DisabledScanner
from app.storage.validation import detect_media, safe_display_filename
from tests.helpers import API, create_case

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 256
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 128
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 128


def _upload(client, actor, case_id, data, media_type="PHOTO", name="photo.png", content_type="image/png"):
    return client.post(
        f"{API}/cases/{case_id}/attachments", data={"media_type": media_type},
        files={"file": (name, data, content_type)}, headers=actor.headers,
    )


@pytest.mark.parametrize(
    "head,expected",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (b"RIFF\0\0\0\0WEBPVP8 ", "image/webp"), (WAV, "audio/wav"),
     (b"OggS\0\2", "audio/ogg"), (b"ID3\4\0", "audio/mpeg"), (b"\0\0\0\x20ftypM4A \0", "audio/mp4"), (WEBM, "audio/webm"),
     (b"%PDF-1.7", None), (b"MZ\x90\0", None), (b"<svg xmlns=", None)],
)
def test_signature_detection(head, expected):
    detected = detect_media(head)
    assert (detected.content_type if detected else None) == expected


def test_display_filename_is_sanitised():
    assert safe_display_filename("../../etc/passwd", "png") == "passwd"
    assert safe_display_filename("a\x00b<script>.png", "png") == "ab_script_.png"
    assert safe_display_filename("", "wav") == "attachment.wav"


def test_photo_and_voice_upload_store_metadata_privately(client, actors, storage):
    case = create_case(client, actors.helpdesk, "found-persons")
    photo = _upload(client, actors.helpdesk, case["id"], PNG)
    assert photo.status_code == 201, photo.text
    body = photo.json()
    assert body["checksum_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert body["file_size_bytes"] == len(PNG) and body["content_type"] == "image/png"
    assert body["scan_status"] == "SKIPPED_DEV" and body["downloadable"] is True
    assert "storage_key" not in body

    voice = _upload(client, actors.helpdesk, case["id"], WEBM, "AUDIO", "voice.webm", "audio/webm")
    assert voice.status_code == 201 and voice.json()["content_type"] == "audio/webm"

    with SessionLocal() as db:
        row = db.scalar(select(CaseAttachment).where(CaseAttachment.id == body["id"]))
    assert row.storage_key == f"cases/{case['id']}/{row.id.hex}"
    obj = storage.client.get_object(Bucket=storage.bucket, Key=row.storage_key)
    assert obj["Body"].read() == PNG
    assert obj["ServerSideEncryption"] == "AES256"
    assert client.get(f"{API}/cases/{case['id']}/attachments", headers=actors.helpdesk.headers).json()["total"] == 2


@pytest.mark.parametrize(
    "data,media_type,content_type,code",
    [
        (b"MZ\x90\0 not an image", "PHOTO", "image/png", "unsupported_media_type"),
        (PNG, "AUDIO", "audio/mpeg", "media_type_mismatch"),
        (PNG, "PHOTO", "audio/mpeg", "content_type_mismatch"),
    ],
)
def test_rejects_disguised_or_mismatched_files(client, actors, data, media_type, content_type, code):
    case = create_case(client, actors.helpdesk, "found-persons")
    r = _upload(client, actors.helpdesk, case["id"], data, media_type, "x.png", content_type)
    assert r.status_code == 415 and r.json()["error"]["code"] == code


def test_rejects_empty_and_oversized_files(client, actors, monkeypatch):
    from app.config import get_settings

    case = create_case(client, actors.helpdesk, "found-persons")
    assert _upload(client, actors.helpdesk, case["id"], b"").status_code == 422
    monkeypatch.setattr(get_settings(), "max_photo_bytes", 100)
    r = _upload(client, actors.helpdesk, case["id"], PNG)
    assert r.status_code == 413


def test_download_url_is_short_lived_signed_and_audited(client, actors):
    case = create_case(client, actors.helpdesk, "found-persons")
    aid = _upload(client, actors.helpdesk, case["id"], JPEG, name="child.jpg", content_type="image/jpeg").json()["id"]
    r = client.get(f"{API}/cases/{case['id']}/attachments/{aid}", headers=actors.helpdesk.headers)
    assert r.status_code == 200
    query = parse_qs(urlparse(r.json()["url"]).query)
    assert query["X-Amz-Expires"] == ["300"] and "X-Amz-Signature" in query
    assert "attachment" in query["response-content-disposition"][0]
    audit = client.get(f"{API}/audit-events", params={"action": "ATTACHMENT_ACCESSED"}, headers=actors.admin.headers).json()
    assert audit["total"] == 1


def test_attachments_cannot_be_reached_through_other_cases_or_stations(client, actors):
    case = create_case(client, actors.helpdesk, "found-persons")
    other = create_case(client, actors.helpdesk, "missing-persons")
    aid = _upload(client, actors.helpdesk, case["id"], PNG).json()["id"]
    assert client.get(f"{API}/cases/{other['id']}/attachments/{aid}", headers=actors.helpdesk.headers).status_code == 404
    assert client.get(f"{API}/cases/{case['id']}/attachments/{aid}", headers=actors.helpdesk_b.headers).status_code == 404
    assert _upload(client, actors.helpdesk_b, case["id"], PNG).status_code == 404


def test_soft_delete_hides_attachment_and_is_police_only(client, actors):
    case = create_case(client, actors.helpdesk, "found-persons")
    aid = _upload(client, actors.helpdesk, case["id"], PNG).json()["id"]
    url = f"{API}/cases/{case['id']}/attachments/{aid}"
    assert client.request("DELETE", url, json={"reason": "wrong child"}, headers=actors.helpdesk.headers).status_code == 403
    assert client.request("DELETE", url, json={"reason": "Wrong child photographed"}, headers=actors.police.headers).status_code == 204
    assert client.get(url, headers=actors.police.headers).status_code == 404
    with SessionLocal() as db:
        assert db.get(CaseAttachment, uuid.UUID(aid)).deleted_at is not None


def test_storage_failure_leaves_no_metadata(client, actors, storage, monkeypatch):
    case = create_case(client, actors.helpdesk, "found-persons")

    def fail(*args, **kwargs):
        raise ServiceUnavailable("down", code="storage_unavailable")

    monkeypatch.setattr(storage, "put", fail)
    r = _upload(client, actors.helpdesk, case["id"], PNG)
    assert r.status_code == 503
    with SessionLocal() as db:
        assert db.scalar(select(CaseAttachment.id)) is None


@pytest.mark.parametrize("failure_point", ["audit", "commit"])
def test_database_failure_after_upload_removes_object(actors, storage, monkeypatch, failure_point):
    from app.models import User
    from app.services import attachments as attachment_service

    with SessionLocal() as db:
        user = db.get(User, actors.helpdesk.id)
        case_id = _make_case(db, user)
        deleted: list[str] = []
        real_delete = storage.delete
        monkeypatch.setattr(storage, "delete", lambda key: (deleted.append(key), real_delete(key)))

        def fail(*args, **kwargs):
            raise RuntimeError(f"{failure_point} failed")

        if failure_point == "audit":
            monkeypatch.setattr(attachment_service, "record_audit", fail)
        else:
            monkeypatch.setattr(db, "commit", fail)
        with pytest.raises(RuntimeError):
            attachment_service.upload_attachment(
                db, storage, user, case_id, media_type=MediaType.PHOTO,
                stream=io.BytesIO(PNG), filename="x.png", declared_content_type="image/png",
                scanner=DisabledScanner(),
            )

    assert len(deleted) == 1 and deleted[0].startswith(f"quarantine/{case_id}/")
    for prefix in (f"quarantine/{case_id}/", f"cases/{case_id}/"):
        assert storage.client.list_objects_v2(Bucket=storage.bucket, Prefix=prefix).get("KeyCount", 0) == 0
    with SessionLocal() as db:
        assert db.scalar(select(CaseAttachment.id)) is None


def _make_case(db, user) -> uuid.UUID:
    from app.schemas.cases import FoundPersonCreate
    from app.services import cases as case_service
    from app.services.case_types import FOUND_PERSON

    case, _ = case_service.create_case(db, user, FOUND_PERSON, FoundPersonCreate(approximate_age=3))
    return case.id
