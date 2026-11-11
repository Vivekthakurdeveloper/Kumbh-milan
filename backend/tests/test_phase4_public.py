"""Phase 4 public portal, OTP, sightings, providers, and public information."""

from __future__ import annotations

import io
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.common.security_hash import generate_otp, hash_otp
from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal
from app.models import Case, Notification, OtpChallenge, PublicInformationItem, PushDeviceToken
from app.models.enums import NotificationStatus, NotificationTemplate, OtpPurpose, PublicReviewStatus
from app.notifications import MockProvider, get_provider, reset_provider_cache
from app.services import notifications as notification_service
from app.services import otp as otp_service
from tests.helpers import API, configure_public_intake

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a"
    b"\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00"
    b"\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01"
    b"\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n"
    b"\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00"
    b"\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91"
    b"\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789"
    b":CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93"
    b"\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3"
    b"\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3"
    b"\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1"
    b"\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00"
    b"\xfb\xd5s\x1f\xff\xd9"
)


def _otp_request(client, mobile="9876543210", purpose="REPORT_MISSING"):
    return client.post(
        f"{API}/public/otp/request",
        json={"mobile_number": mobile, "purpose": purpose, "locale": "hi"},
    )


def _set_challenge_code(mobile_hash_purpose: OtpPurpose, code: str = "123456"):
    with SessionLocal() as db:
        row = db.scalar(
            select(OtpChallenge)
            .where(OtpChallenge.purpose == mobile_hash_purpose, OtpChallenge.consumed_at.is_(None))
            .order_by(OtpChallenge.created_at.desc())
        )
        assert row is not None
        row.code_hash = hash_otp(code)
        db.commit()
        return code


def _verify_mobile(client, mobile="9876543210", purpose="REPORT_MISSING", code="123456"):
    requested = _otp_request(client, mobile=mobile, purpose=purpose)
    assert requested.status_code == 200, requested.text
    _set_challenge_code(OtpPurpose(purpose), code)
    r = client.post(
        f"{API}/public/otp/verify",
        json={"mobile_number": mobile, "purpose": purpose, "code": code},
    )
    assert r.status_code == 200, r.text
    return r.json()["verified_mobile_token"]


def _public_report(**overrides):
    body = {
        "person_name": "Raju",
        "approximate_age": 8,
        "gender": "MALE",
        "last_seen_location": "Har Ki Pauri",
        "last_seen_at": "2026-11-07T10:00:00+05:30",
        "clothing_description": "red kurta",
        "reporter_name": "Ramesh",
        "reporter_mobile_number": "9876543210",
        "additional_description": "near the steps",
        "preferred_locale": "hi",
        "notification_consent": True,
    }
    body.update(overrides)
    return body


def _submit_missing(client, token, report=None, key="public-submit-1", photo=False):
    data = {"report": json.dumps(report or _public_report())}
    files = None
    if photo:
        files = {"photo": ("face.jpg", io.BytesIO(TINY_JPEG), "image/jpeg")}
    return client.post(
        f"{API}/public/missing-persons",
        data=data,
        files=files,
        headers={"X-Verified-Mobile-Token": token, "Idempotency-Key": key},
    )


def test_public_missing_person_submission_success(client, actors):
    token = _verify_mobile(client)
    r = _submit_missing(client, token, photo=True)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "PENDING_VERIFICATION"
    assert body["case_id"].startswith(get_settings().case_number_prefix)
    with SessionLocal() as db:
        case = db.scalar(select(Case).where(Case.case_number == body["case_id"]))
        assert case.source == "PUBLIC"
        assert case.public_review_status == PublicReviewStatus.PENDING_VERIFICATION
        queued = db.scalars(select(Notification).where(Notification.case_id == case.id)).all()
        assert any(n.template == NotificationTemplate.CASE_SUBMITTED for n in queued)


def test_public_missing_person_missing_fields(client, actors):
    token = _verify_mobile(client)
    bad = _public_report()
    del bad["person_name"]
    r = _submit_missing(client, token, report=bad)
    assert r.status_code == 422


def test_public_duplicate_submission_retry(client, actors):
    token = _verify_mobile(client)
    first = _submit_missing(client, token, key="retry-key-001")
    second = _submit_missing(client, token, key="retry-key-001")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert first.json()["case_id"] == second.json()["case_id"]


def test_otp_generation_and_verification(client, actors):
    assert _otp_request(client).status_code == 200
    code = _set_challenge_code(OtpPurpose.REPORT_MISSING, "654321")
    ok = client.post(
        f"{API}/public/otp/verify",
        json={"mobile_number": "9876543210", "purpose": "REPORT_MISSING", "code": code},
    )
    assert ok.status_code == 200
    assert "verified_mobile_token" in ok.json()


def test_expired_otp(client, actors):
    _otp_request(client)
    with SessionLocal() as db:
        db.execute(update(OtpChallenge).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    bad = client.post(
        f"{API}/public/otp/verify",
        json={"mobile_number": "9876543210", "purpose": "REPORT_MISSING", "code": "123456"},
    )
    assert bad.status_code == 401


def test_incorrect_otp_and_rate_limits(client, actors, monkeypatch):
    monkeypatch.setattr(get_settings(), "otp_max_attempts", 2)
    _otp_request(client)
    _set_challenge_code(OtpPurpose.REPORT_MISSING, "111111")
    for _ in range(2):
        r = client.post(
            f"{API}/public/otp/verify",
            json={"mobile_number": "9876543210", "purpose": "REPORT_MISSING", "code": "000000"},
        )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "otp_locked"


def test_unauthorized_case_status_access(client, actors):
    token = _verify_mobile(client)
    created = _submit_missing(client, token).json()
    r = client.get(f"{API}/public/cases/{created['case_id']}/status")
    assert r.status_code == 401


def test_valid_case_status_retrieval(client, actors):
    token = _verify_mobile(client)
    created = _submit_missing(client, token).json()
    req = client.post(
        f"{API}/public/status/request-otp",
        json={"case_id": created["case_id"], "mobile_number": "9876543210", "locale": "en"},
    )
    assert req.status_code == 200
    # Non-matching mobile still returns OTP_SENT (anti-enumeration)
    ghost = client.post(
        f"{API}/public/status/request-otp",
        json={"case_id": created["case_id"], "mobile_number": "9111111111", "locale": "en"},
    )
    assert ghost.status_code == 200
    code = _set_challenge_code(OtpPurpose.CASE_STATUS, "222222")
    verified = client.post(
        f"{API}/public/status/verify-otp",
        json={"case_id": created["case_id"], "mobile_number": "9876543210", "code": code},
    )
    assert verified.status_code == 200, verified.text
    access = verified.json()["access_token"]
    status = client.get(
        f"{API}/public/cases/{created['case_id']}/status",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["case_id"] == created["case_id"]
    assert body["status"] == "PENDING_VERIFICATION"
    assert "message" in body and "updated_at" in body
    assert "contact" not in status.text.lower()
    assert "medical" not in status.text.lower()


def test_sighting_report_and_photo(client, actors):
    token = _verify_mobile(client, purpose="REPORT_SIGHTING")
    report = {
        "sighting_location": "Sector 4",
        "sighting_at": "2026-11-07T12:00:00+05:30",
        "description": "Child in red kurta near tent",
        "reporter_mobile_number": "9876543210",
    }
    r = client.post(
        f"{API}/public/sightings",
        data={"report": json.dumps(report)},
        files={"photo": ("s.jpg", io.BytesIO(TINY_JPEG), "image/jpeg")},
        headers={"X-Verified-Mobile-Token": token},
    )
    assert r.status_code == 201, r.text
    sighting_id = r.json()["sighting_id"]
    listed = client.get(f"{API}/sightings", headers=actors.helpdesk.headers)
    assert listed.status_code == 200
    assert any(i["id"] == sighting_id and i["has_photo"] for i in listed.json()["items"])


def test_sms_notification_creation_and_mock_delivery(client, actors):
    reset_provider_cache()
    get_settings().notification_provider = "mock"
    reset_provider_cache()
    token = _verify_mobile(client)
    created = _submit_missing(client, token).json()
    with SessionLocal() as db:
        case = db.scalar(select(Case).where(Case.case_number == created["case_id"]))
        provider = get_provider()
        assert isinstance(provider.sms, MockProvider) or provider.name == "composite"
        outcomes = notification_service.deliver_due(db, provider)
        assert outcomes["sent"] >= 1
        row = db.scalar(select(Notification).where(Notification.case_id == case.id))
        assert row.status == NotificationStatus.SENT


def test_email_notification_processing(client, actors):
    token = _verify_mobile(client)
    report = _public_report(reporter_email="father@example.com")
    created = _submit_missing(client, token, report=report).json()
    with SessionLocal() as db:
        case = db.scalar(select(Case).where(Case.case_number == created["case_id"]))
        emails = [
            n
            for n in db.scalars(select(Notification).where(Notification.case_id == case.id))
            if n.channel == "EMAIL"
        ]
        assert emails
        provider = get_provider()
        notification_service.deliver_due(db, provider)
        db.refresh(emails[0])
        assert emails[0].status in (NotificationStatus.SENT, NotificationStatus.PENDING)


def test_push_token_registration(client, actors):
    r = client.post(
        f"{API}/devices/push-token",
        json={"platform": "ANDROID", "token": "fcm-test-token-abcdefghijklmnopqrstuvwxyz"},
        headers=actors.police.headers,
    )
    assert r.status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(PushDeviceToken).where(PushDeviceToken.user_id == actors.police.id))
    deleted = client.request(
        "DELETE",
        f"{API}/devices/push-token",
        json={"token": "fcm-test-token-abcdefghijklmnopqrstuvwxyz"},
        headers=actors.police.headers,
    )
    assert deleted.status_code == 204


def test_notification_retry_handling(client, actors, monkeypatch):
    from app.notifications import ProviderError

    class Flaky:
        name = "flaky"
        enabled = True

        def __init__(self):
            self.calls = 0

        def send(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("timeout", permanent=False)
            from app.notifications import Delivery

            return Delivery(provider_message_id="ok")

    monkeypatch.setattr(get_settings(), "notification_max_attempts", 3)
    monkeypatch.setattr(get_settings(), "notification_retry_base_seconds", 1)
    token = _verify_mobile(client)
    created = _submit_missing(client, token).json()
    provider = Flaky()
    with SessionLocal() as db:
        assert notification_service.deliver_due(db, provider)["retry"] == 1
        db.execute(update(Notification).values(next_attempt_at=utcnow() - timedelta(seconds=1)))
        db.commit()
        assert notification_service.deliver_due(db, provider)["sent"] == 1


def test_hindi_english_templates_and_public_information(client, actors):
    create = client.post(
        f"{API}/admin/public-information",
        json={
            "kind": "HELP_DESK",
            "title_en": "Har Ki Pauri desk",
            "title_hi": "हर की पौड़ी डेस्क",
            "body_en": "Open 24 hours",
            "body_hi": "24 घंटे खुला",
            "contact_phone": "+911234567890",
            "publish": True,
        },
        headers=actors.admin.headers,
    )
    assert create.status_code == 201, create.text
    public = client.get(f"{API}/public/information")
    assert public.status_code == 200
    assert public.json()["items"][0]["title_hi"]
    denied = client.post(
        f"{API}/admin/public-information",
        json={
            "kind": "ANNOUNCEMENT",
            "title_en": "x",
            "title_hi": "य",
            "body_en": "body english text",
            "body_hi": "हिंदी पाठ यहाँ है",
            "publish": True,
        },
        headers=actors.helpdesk.headers,
    )
    assert denied.status_code == 403


def test_public_api_privacy_and_staff_review(client, actors):
    token = _verify_mobile(client)
    created = _submit_missing(client, token).json()
    with SessionLocal() as db:
        case = db.scalar(select(Case).where(Case.case_number == created["case_id"]))
        case_id = case.id
    reviewed = client.post(
        f"{API}/cases/{case_id}/public-review",
        json={"decision": "VERIFIED", "reason": "Documents checked at desk"},
        headers=actors.helpdesk.headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["public_review_status"] == "VERIFIED"
    assert "contact_details" not in reviewed.json()
