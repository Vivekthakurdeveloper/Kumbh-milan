"""PUBLIC self-registration, ownership, password reset, dashboard dates, and case detail fields."""

from __future__ import annotations

import io
import re
from datetime import date, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.common.time import utcnow
from app.database import SessionLocal
from app.models import PasswordResetToken, User
from app.notifications import MockProvider
from tests.helpers import (
    API,
    FOUND_ITEM,
    FOUND_PERSON,
    LOST_ITEM,
    MISSING_PERSON,
    PASSWORD,
    create_case,
)

PUBLIC_PASSWORD = "Citizen#Secure2027!"
TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
    b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00"
    b"\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03"
    b"\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1"
    b"\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJ"
    b"STUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94"
    b"\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3"
    b"\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2"
    b"\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9"
    b"\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01"
    b"\x00\x00?\x00\xfb\xd5\x1f\xff\xd9"
)


def _register(client, mobile: str = "9876543210", full_name: str = "Test Citizen") -> dict:
    response = client.post(
        f"{API}/auth/register",
        json={"username": mobile, "password": PUBLIC_PASSWORD, "full_name": full_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _public_headers(client, mobile: str = "9876543210") -> dict[str, str]:
    tokens = _register(client, mobile=mobile)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --- Registration / auth -------------------------------------------------------------------------


def test_public_registration_login_me_refresh_logout(client, actors):
    tokens = _register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "PUBLIC"
    assert body["permissions"] == []
    assert body["station_id"] is None
    assert body["username"] == "919876543210"

    # Login with the original 10-digit mobile still works after canonicalisation.
    relogin = client.post(
        f"{API}/auth/login", json={"username": "9876543210", "password": PUBLIC_PASSWORD}
    )
    assert relogin.status_code == 200
    refreshed = client.post(f"{API}/auth/refresh", json={"refresh_token": relogin.json()["refresh_token"]})
    assert refreshed.status_code == 200
    assert client.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"}).status_code == 204

    # Staff login remains unaffected.
    assert client.post(f"{API}/auth/login", json={"username": "police", "password": PASSWORD}).status_code == 200


def test_duplicate_public_registration_rejected(client, actors):
    _register(client, mobile="9123456780")
    again = client.post(
        f"{API}/auth/register",
        json={"username": "9123456780", "password": PUBLIC_PASSWORD, "full_name": "Other"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "duplicate"


def test_public_registration_rejects_role_field(client, actors):
    response = client.post(
        f"{API}/auth/register",
        json={
            "username": "9111222333",
            "password": PUBLIC_PASSWORD,
            "full_name": "Nope",
            "role": "ADMIN",
        },
    )
    assert response.status_code == 422


def test_incorrect_public_password_rejected(client, actors):
    _register(client, mobile="9000011111")
    bad = client.post(f"{API}/auth/login", json={"username": "9000011111", "password": "Wrong-Password-99"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"


# --- Ownership / authorization -------------------------------------------------------------------


def test_public_can_create_all_four_case_types_and_only_see_own(client, actors):
    headers_a = _public_headers(client, "9811111111")
    headers_b = _public_headers(client, "9822222222")

    created = []
    for slug, payload in (
        ("missing-persons", MISSING_PERSON),
        ("found-persons", FOUND_PERSON),
        ("lost-items", LOST_ITEM),
        ("found-items", FOUND_ITEM),
    ):
        response = client.post(f"{API}/cases/{slug}", json=payload, headers=headers_a)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["source"] == "PUBLIC"
        assert body["public_review_status"] == "PENDING_VERIFICATION"
        assert body["in_scope"] is True
        assert "verification_evidence" in body
        created.append(body)

    with SessionLocal() as db:
        user_a = db.scalar(select(User).where(User.username == "919811111111"))
        assert user_a is not None
        for case in created:
            assert case["reported_by_user_id"] == str(user_a.id)

    # Citizen B creates one case; must not see A's cases.
    other = client.post(f"{API}/cases/missing-persons", json=MISSING_PERSON, headers=headers_b)
    assert other.status_code == 201
    listed = client.get(f"{API}/cases", headers=headers_b)
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["items"]}
    assert other.json()["id"] in ids
    assert created[0]["id"] not in ids
    assert all(item["reported_by_user_id"] for item in listed.json()["items"])

    # IDOR: B cannot read A's case.
    assert client.get(f"{API}/cases/{created[0]['id']}", headers=headers_b).status_code == 404
    assert client.get(f"{API}/cases/{created[0]['id']}/history", headers=headers_b).status_code == 404
    assert client.get(f"{API}/cases/{created[0]['id']}/notes", headers=headers_b).status_code == 404
    assert client.get(f"{API}/cases/{created[0]['id']}/attachments", headers=headers_b).status_code == 404

    # Owner can read history/notes and upload an attachment.
    assert client.get(f"{API}/cases/{created[0]['id']}/history", headers=headers_a).status_code == 200
    note = client.post(
        f"{API}/cases/{created[0]['id']}/notes",
        headers=headers_a,
        json={"note_text": "Family update from citizen", "note_type": "OPERATIONAL"},
    )
    assert note.status_code == 201
    upload = client.post(
        f"{API}/cases/{created[0]['id']}/attachments",
        headers=headers_a,
        data={"media_type": "PHOTO"},
        files={"file": ("face.jpg", io.BytesIO(TINY_JPEG), "image/jpeg")},
    )
    assert upload.status_code in (201, 202), upload.text


def test_public_forbidden_from_staff_endpoints(client, actors):
    headers = _public_headers(client, "9833333333")
    case = create_case(client, actors.helpdesk, "missing-persons")

    checks = [
        ("GET", f"{API}/dashboard/summary", None),
        ("GET", f"{API}/matches", None),
        ("GET", f"{API}/cases/nearby?lat=29.9&lng=78.1", None),
        ("GET", f"{API}/cases/map?min_lat=29&min_lng=77&max_lat=30&max_lng=79", None),
        ("GET", f"{API}/users", None),
        ("GET", f"{API}/stations", None),
        ("GET", f"{API}/reports/cases.csv", None),
        ("POST", f"{API}/cases/missing-persons/{case['id']}/status", {"status": "UNDER_REVIEW", "reason": "x"}),
        ("POST", f"{API}/cases/missing-persons/{case['id']}/assign", {"assignee_user_id": str(actors.police.id)}),
        ("POST", f"{API}/cases/{case['id']}/contact/reveal", {"reason_code": "FAMILY_ENQUIRY"}),
        ("POST", f"{API}/cases/{case['id']}/matches/generate", None),
    ]
    for method, path, payload in checks:
        response = client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 403, (method, path, response.status_code, response.text)


def test_staff_permissions_unchanged_for_helpdesk(client, actors):
    me = client.get(f"{API}/auth/me", headers=actors.helpdesk.headers).json()
    assert me["role"] == "HELP_DESK_OPERATOR"
    assert "case:create" in me["permissions"]
    assert "case:resolve" not in me["permissions"]
    assert "match:review" not in me["permissions"]


# --- Password recovery ---------------------------------------------------------------------------


def test_forgot_password_anti_enumeration_and_reset_flow(client, actors, monkeypatch):
    provider = MockProvider()
    monkeypatch.setattr("app.auth.password_reset.get_provider", lambda: provider)
    _register(client, mobile="9844444444")
    known = client.post(f"{API}/auth/forgot-password", json={"identifier": "9844444444"})
    unknown = client.post(f"{API}/auth/forgot-password", json={"identifier": "9800000000"})
    assert known.status_code == unknown.status_code == 202
    assert known.json()["message"] == unknown.json()["message"]
    assert "If an account matches" in known.json()["message"]
    assert len(provider.deliveries) == 1
    match = re.search(r"\b(\d{8})\b", provider.deliveries[0]["body"])
    assert match is not None
    code = match.group(1)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "919844444444"))
        token_row = db.scalar(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id, PasswordResetToken.consumed_at.is_(None))
        )
        assert token_row is not None and code not in token_row.code_hash

    ok = client.post(
        f"{API}/auth/reset-password",
        json={"identifier": "9844444444", "code": code, "new_password": "NewCitizen#Pass2028!"},
    )
    assert ok.status_code == 204

    # Code cannot be reused.
    reuse = client.post(
        f"{API}/auth/reset-password",
        json={"identifier": "9844444444", "code": code, "new_password": "AnotherCitizen#Pass2028!"},
    )
    assert reuse.status_code == 422
    assert reuse.json()["error"]["code"] == "invalid_reset_code"

    # New password works; old does not.
    assert client.post(
        f"{API}/auth/login", json={"username": "9844444444", "password": PUBLIC_PASSWORD}
    ).status_code == 401
    assert client.post(
        f"{API}/auth/login", json={"username": "9844444444", "password": "NewCitizen#Pass2028!"}
    ).status_code == 200


def test_expired_reset_code_rejected(client, actors, monkeypatch):
    provider = MockProvider()
    monkeypatch.setattr("app.auth.password_reset.get_provider", lambda: provider)
    _register(client, mobile="9855555555")
    client.post(f"{API}/auth/forgot-password", json={"identifier": "9855555555"})
    match = re.search(r"\b(\d{8})\b", provider.deliveries[0]["body"])
    assert match is not None
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "919855555555"))
        row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    response = client.post(
        f"{API}/auth/reset-password",
        json={
            "identifier": "9855555555",
            "code": match.group(1),
            "new_password": "ExpiredToken#Pass2028!",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reset_code"


# --- Dashboard date filtering --------------------------------------------------------------------


def test_dashboard_date_filter_kolkata_inclusive(client, actors):
    created = create_case(client, actors.helpdesk, "missing-persons")
    bare = client.get(f"{API}/dashboard/summary", headers=actors.helpdesk.headers)
    assert bare.status_code == 200
    assert "registered_today" in bare.json()

    today = date.today()
    filtered = client.get(
        f"{API}/dashboard/summary",
        headers=actors.helpdesk.headers,
        params={"from": today.isoformat(), "to": today.isoformat()},
    )
    assert filtered.status_code == 200
    assert filtered.json()["registered_today"] >= 1
    assert filtered.json()["active_total"] >= 1

    past = client.get(
        f"{API}/dashboard/summary",
        headers=actors.helpdesk.headers,
        params={"from": "2020-01-01", "to": "2020-01-02"},
    )
    assert past.status_code == 200
    assert past.json()["registered_today"] == 0

    invalid = client.get(
        f"{API}/dashboard/summary",
        headers=actors.helpdesk.headers,
        params={"from": "2026-11-10", "to": "2026-11-01"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_period"
    _ = created


def test_dashboard_accepts_kolkata_datetime_bounds_from_the_desk(client, actors):
    create_case(client, actors.helpdesk, "missing-persons")
    today = utcnow().astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()

    for start in (today, (date.fromisoformat(today) - timedelta(days=6)).isoformat()):
        response = client.get(
            f"{API}/dashboard/summary",
            headers=actors.helpdesk.headers,
            params={"from": f"{start}T00:00:00+05:30", "to": f"{today}T23:59:59+05:30"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["registered_today"] == 1
        assert response.json()["active_total"] == 1

    past = client.get(
        f"{API}/dashboard/summary",
        headers=actors.helpdesk.headers,
        params={"from": "2020-01-01T00:00:00+05:30", "to": "2020-01-01T23:59:59+05:30"},
    )
    assert past.status_code == 200
    assert past.json()["registered_today"] == 0
    assert past.json()["active_total"] == 1

    garbage = client.get(
        f"{API}/dashboard/summary", headers=actors.helpdesk.headers, params={"from": "yesterday", "to": today}
    )
    assert garbage.status_code == 422
    assert garbage.json()["error"]["code"] == "invalid_period"


# --- Case detail fields --------------------------------------------------------------------------


def test_case_detail_in_scope_and_verification_evidence(client, actors):
    case = create_case(client, actors.helpdesk, "missing-persons")
    detail = client.get(f"{API}/cases/{case['id']}", headers=actors.helpdesk.headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["in_scope"] is True
    assert body["verification_evidence"] is None  # help desk cannot see sensitive evidence

    assign = client.post(
        f"{API}/cases/missing-persons/{case['id']}/assign",
        headers=actors.police.headers,
        json={"assignee_user_id": str(actors.police.id), "reason": "Assigned"},
    )
    assert assign.status_code == 200, assign.text
    resolved = client.post(
        f"{API}/cases/missing-persons/{case['id']}/status",
        headers=actors.police.headers,
        json={
            "status": "RESOLVED",
            "reason": "Reunited",
            "verification_evidence": "Identity verified against registered missing-person details.",
        },
    )
    assert resolved.status_code == 200, resolved.text
    police_view = client.get(f"{API}/cases/{case['id']}", headers=actors.police.headers).json()
    assert police_view["in_scope"] is True
    assert police_view["verification_evidence"] == "Identity verified against registered missing-person details."

    helpdesk_view = client.get(f"{API}/cases/{case['id']}", headers=actors.helpdesk.headers).json()
    assert helpdesk_view["verification_evidence"] is None


def test_match_photo_url_is_http_when_configured(client, actors):
    from app.config import get_settings
    from app.storage import get_storage

    settings = get_settings()
    previous_public = settings.s3_public_endpoint_url
    previous_endpoint = settings.s3_endpoint_url
    try:
        settings.s3_public_endpoint_url = "https://media.example.org"
        settings.s3_endpoint_url = "http://objectstore:8333"
        get_storage.cache_clear()

        # Without a real face match fixture, assert the storage client would sign against the public host.
        storage = get_storage()
        assert storage.public_endpoint_url == "https://media.example.org"
        url = storage.presign_get(
            "cases/demo/key",
            content_type="image/jpeg",
            filename="demo.jpg",
            ttl_seconds=60,
        )
        parsed = urlparse(url)
        assert parsed.scheme in ("http", "https")
        assert parsed.hostname == "media.example.org"
        assert "localhost" not in url
        assert "objectstore" not in url
    finally:
        settings.s3_public_endpoint_url = previous_public
        settings.s3_endpoint_url = previous_endpoint
        get_storage.cache_clear()
