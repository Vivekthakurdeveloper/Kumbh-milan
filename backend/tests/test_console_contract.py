"""Console requests: dashboard windows, in_scope on a single case, browser-reachable media links."""

from datetime import timedelta
from urllib.parse import urlparse

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.common.time import utcnow
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import Case, CaseAttachment, CaseMatch
from app.models.enums import CaseType, MatchDomain, MatchSource
from app.storage import ObjectStorage
from app.storage.media import issue_token, verify_token
from tests.helpers import API, create_case
from tests.test_media import JPEG, PNG, _upload
from tests.test_platform import PRODUCTION_OK

# --- Dashboard window ----------------------------------------------------------------------------------


def _age(case_id: str, **delta) -> None:
    with SessionLocal() as db:
        db.execute(update(Case).where(Case.id == case_id).values(created_at=utcnow() - timedelta(**delta)))
        db.commit()


def _summary(client, actor, **params):
    r = client.get(f"{API}/dashboard/summary", params=params, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


def _day(days_ago: int) -> str:
    from app.services.cases import CASE_NUMBER_TZ

    return (utcnow().astimezone(CASE_NUMBER_TZ) - timedelta(days=days_ago)).date().isoformat()


def test_dashboard_window_changes_the_four_triage_counters(client, actors):
    create_case(client, actors.police, "missing-persons")  # CRITICAL, unassigned, registered now
    week = create_case(client, actors.police, "missing-persons")
    old = create_case(client, actors.police, "missing-persons")
    _age(week["id"], days=4)
    _age(old["id"], days=20)

    everything = _summary(client, actors.police)
    assert everything["urgent_active"] == 3 and everything["unassigned_active"] == 3
    assert everything["registered_today"] == 1
    assert everything["period_from"] is None and everything["period_to"] is None

    day = _summary(client, actors.police, **{"from": _day(0), "to": _day(0)})
    assert (day["urgent_active"], day["unassigned_active"], day["registered_today"]) == (1, 1, 1)
    assert day["period_from"] and day["period_to"]

    seven = _summary(client, actors.police, **{"from": _day(6), "to": _day(0)})
    assert (seven["urgent_active"], seven["unassigned_active"], seven["registered_today"]) == (2, 2, 2)

    thirty = _summary(client, actors.police, **{"from": _day(29), "to": _day(0)})
    assert (thirty["urgent_active"], thirty["unassigned_active"], thirty["registered_today"]) == (3, 3, 3)

    # The other fields describe the current state whatever the window.
    assert day["active_total"] == seven["active_total"] == thirty["active_total"] == 3


def test_dashboard_window_accepts_datetimes_and_open_bounds(client, actors):
    create_case(client, actors.police, "missing-persons")
    future = (utcnow() + timedelta(days=1)).isoformat()
    assert _summary(client, actors.police, **{"from": future})["registered_today"] == 0
    assert _summary(client, actors.police, **{"from": "2020-01-01T00:00:00+05:30"})["registered_today"] == 1
    assert _summary(client, actors.police, to=future)["registered_today"] == 1
    assert _summary(client, actors.police, to="2020-01-01")["registered_today"] == 0


def test_dashboard_window_filters_pending_match_reviews(client, actors):
    mp = create_case(client, actors.police, "missing-persons")
    fp = create_case(client, actors.police, "found-persons")
    with SessionLocal() as db:
        db.add(CaseMatch(
            match_domain=MatchDomain.PERSON, left_case_id=mp["id"], left_case_type=CaseType.MISSING_PERSON,
            right_case_id=fp["id"], right_case_type=CaseType.FOUND_PERSON, source=MatchSource.MANUAL,
            proposed_by_user_id=actors.police.id, rationale="same clothing",
            created_at=utcnow() - timedelta(days=10),
        ))
        db.commit()
    assert _summary(client, actors.police)["pending_match_reviews"] == 1
    assert _summary(client, actors.police, **{"from": _day(6), "to": _day(0)})["pending_match_reviews"] == 0
    assert _summary(client, actors.police, **{"from": _day(29), "to": _day(0)})["pending_match_reviews"] == 1


@pytest.mark.parametrize(
    "params",
    [{"from": "yesterday"}, {"to": "06/11/2026"}, {"from": "2026-11-06", "to": "2026-11-01"},
     {"from": "2024-01-01", "to": "2026-01-01"}],
)
def test_dashboard_window_validation(client, actors, params):
    r = client.get(f"{API}/dashboard/summary", params=params, headers=actors.police.headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_period"


def test_dashboard_window_respects_station_scope(client, actors):
    create_case(client, actors.police, "missing-persons")
    other = _summary(client, actors.police_b, **{"from": _day(0), "to": _day(0)})
    assert other["registered_today"] == 0 and other["urgent_active"] == 0


# --- in_scope on a single case -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["missing-persons", "found-persons", "lost-items", "found-items"])
def test_single_case_carries_in_scope(client, actors, kind):
    case = create_case(client, actors.police, kind)
    assert case["in_scope"] is True  # create response
    typed = client.get(f"{API}/cases/{kind}/{case['id']}", headers=actors.police.headers).json()
    shared = client.get(f"{API}/cases/{case['id']}", headers=actors.police.headers).json()
    assert typed["in_scope"] is True and shared["in_scope"] is True


def test_in_scope_matches_the_list_and_out_of_scope_stays_hidden(client, actors):
    case = create_case(client, actors.police, "missing-persons")
    listed = client.get(f"{API}/cases", headers=actors.police.headers).json()["items"][0]
    assert listed["in_scope"] is True
    # A case outside the caller's scope is not disclosed at all, so the field cannot be used to probe it.
    assert client.get(f"{API}/cases/{case['id']}", headers=actors.helpdesk_b.headers).status_code == 404


# --- Browser-reachable media links ----------------------------------------------------------------------


@pytest.fixture
def proxy_mode(monkeypatch):
    """The API reaches storage on an internal hostname, as in docker compose, with no public store host."""
    settings = get_settings()
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://objectstore:8333")
    monkeypatch.setattr(settings, "s3_public_endpoint_url", None)
    monkeypatch.setattr(settings, "api_public_base_url", None)
    return settings


def _photo_url(client, actors, data=PNG, name="face.png", content_type="image/png"):
    case = create_case(client, actors.helpdesk, "found-persons")
    aid = _upload(client, actors.helpdesk, case["id"], data, name=name, content_type=content_type).json()["id"]
    r = client.get(f"{API}/cases/{case['id']}/attachments/{aid}", headers=actors.helpdesk.headers)
    assert r.status_code == 200, r.text
    return r.json(), aid


def test_download_url_never_points_at_an_internal_storage_host(client, actors, proxy_mode):
    body, _ = _photo_url(client, actors)
    url = urlparse(body["url"])
    assert url.netloc == "testserver" and url.path.startswith("/api/v1/media/")
    assert "8333" not in body["url"] and "objectstore" not in body["url"] and "localhost" not in body["url"]
    assert body["expires_in"] == 300 and body["content_type"] == "image/png"


def test_public_base_url_setting_is_used_behind_a_proxy(client, actors, proxy_mode, monkeypatch):
    monkeypatch.setattr(proxy_mode, "api_public_base_url", "https://api.kumbh.example.org/")
    body, _ = _photo_url(client, actors)
    assert body["url"].startswith("https://api.kumbh.example.org/api/v1/media/")


def test_media_link_streams_the_file_without_a_bearer_token(client, actors, proxy_mode):
    body, _ = _photo_url(client, actors, data=JPEG, name="child.jpg", content_type="image/jpeg")
    path = urlparse(body["url"]).path
    r = client.get(path)  # no Authorization header: usable as <img src>
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["accept-ranges"] == "bytes"


def test_media_link_supports_byte_ranges(client, actors, proxy_mode):
    body, _ = _photo_url(client, actors, data=JPEG, name="child.jpg", content_type="image/jpeg")
    path = urlparse(body["url"]).path
    part = client.get(path, headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == JPEG[:4]
    assert part.headers["content-range"].startswith("bytes 0-3/")
    bad = client.get(path, headers={"Range": f"bytes={len(JPEG) + 100}-"})
    assert bad.status_code == 416


def test_media_link_expires_and_cannot_be_forged(client, actors, proxy_mode, db, monkeypatch):
    body, aid = _photo_url(client, actors)
    token = urlparse(body["url"]).path.rsplit("/", 1)[1]
    assert client.get(f"{API}/media/{token[:-3]}abc").status_code == 404
    assert client.get(f"{API}/media/not-a-token").status_code == 404

    attachment = db.get(CaseAttachment, aid)
    user = type("U", (), {"id": actors.helpdesk.id, "token_version": 0})()
    monkeypatch.setattr(proxy_mode, "s3_presign_ttl_seconds", -5)
    expired = issue_token(proxy_mode, attachment, user)
    r = client.get(f"{API}/media/{expired}")
    assert r.status_code == 410 and r.json()["error"]["code"] == "media_link_expired"


def test_media_link_dies_with_the_session_and_with_the_attachment(client, actors, proxy_mode):
    body, aid = _photo_url(client, actors)
    path = urlparse(body["url"]).path
    assert client.get(path).status_code == 200

    # Withdrawing the attachment kills links that were already issued.
    case_id = client.get(f"{API}/cases", headers=actors.helpdesk.headers).json()["items"][0]["id"]
    assert client.request(
        "DELETE", f"{API}/cases/{case_id}/attachments/{aid}", json={"reason": "wrong photo uploaded"},
        headers=actors.police.headers,
    ).status_code == 204
    assert client.get(path).status_code == 404

    body2, _ = _photo_url(client, actors)
    path2 = urlparse(body2["url"]).path
    assert client.get(path2).status_code == 200
    # Changing the password revokes the user's sessions, and their outstanding media links with them.
    r = client.post(
        f"{API}/users/{actors.helpdesk.id}/reset-password", json={"new_password": "Brand#New2030pw"},
        headers=actors.admin.headers,
    )
    assert r.status_code == 204
    assert client.get(path2).status_code == 404


def test_media_token_is_not_an_access_token(client, actors, proxy_mode):
    body, _ = _photo_url(client, actors)
    token = urlparse(body["url"]).path.rsplit("/", 1)[1]
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    # And an access token is not a media token.
    access = actors.helpdesk.headers["Authorization"].split()[1]
    assert client.get(f"{API}/media/{access}").status_code == 404


def test_match_photo_url_is_browser_reachable_and_has_no_similarity(client, actors, proxy_mode):
    mp = create_case(client, actors.police, "missing-persons")
    fp = create_case(client, actors.police, "found-persons")
    left = _upload(client, actors.police, mp["id"], PNG).json()["id"]
    right = _upload(client, actors.police, fp["id"], JPEG, name="b.jpg", content_type="image/jpeg").json()["id"]
    with SessionLocal() as db:
        match = CaseMatch(
            match_domain=MatchDomain.PERSON, left_case_id=mp["id"], left_case_type=CaseType.MISSING_PERSON,
            right_case_id=fp["id"], right_case_type=CaseType.FOUND_PERSON, source=MatchSource.MANUAL,
            proposed_by_user_id=actors.police.id, rationale="same clothing",
            left_attachment_id=left, right_attachment_id=right,
        )
        db.add(match)
        db.commit()
        match_id = str(match.id)

    for side, content in (("left", PNG), ("right", JPEG)):
        r = client.get(f"{API}/matches/{match_id}/photos/{side}", headers=actors.police.headers)
        assert r.status_code == 200, r.text
        photo = r.json()
        assert set(photo) == {"side", "attachment_id", "url", "expires_in", "content_type"}
        assert "similarity_score" not in photo and "face_similarity" not in photo
        assert urlparse(photo["url"]).netloc == "testserver" and "8333" not in photo["url"]
        assert client.get(urlparse(photo["url"]).path).content == content


def test_direct_links_use_the_declared_public_store_host():
    public = Settings(
        s3_endpoint_url="http://objectstore:8333", s3_public_endpoint_url="https://media.kumbh.example.org",
        s3_access_key_id="k", s3_secret_access_key="s",
    )
    assert public.media_mode == "presigned"
    url = ObjectStorage(public).presign_get("k/1", content_type="image/png", filename="a.png", ttl_seconds=60)
    assert urlparse(url).netloc == "media.kumbh.example.org"


@pytest.mark.parametrize(
    "overrides,mode",
    [
        ({}, "presigned"),  # AWS S3: no custom endpoint, links are reachable
        ({"s3_endpoint_url": "http://objectstore:8333"}, "proxy"),
        ({"s3_endpoint_url": "http://objectstore:8333", "s3_public_endpoint_url": "https://m.example.org"}, "presigned"),
        ({"s3_endpoint_url": "http://objectstore:8333", "media_delivery": "presigned"}, "presigned"),
        ({"media_delivery": "proxy"}, "proxy"),
    ],
)
def test_media_mode_resolution(overrides, mode):
    assert Settings(**overrides).media_mode == mode


@pytest.mark.parametrize(
    "override",
    [
        {"s3_endpoint_url": "http://objectstore:8333"},  # proxy mode without API_PUBLIC_BASE_URL
        {"s3_endpoint_url": "http://objectstore:8333", "api_public_base_url": "http://localhost:8000"},
        {"s3_endpoint_url": "http://objectstore:8333", "s3_public_endpoint_url": "http://localhost:8333"},
        {"s3_endpoint_url": "http://objectstore:8333", "media_delivery": "presigned"},
        {"email_provider": "console"},
        {"email_provider": "mock"},
    ],
)
def test_production_refuses_media_urls_a_browser_cannot_reach(override):
    with pytest.raises(ValidationError):
        Settings(**{**PRODUCTION_OK, **override})


def test_production_accepts_a_reachable_media_configuration():
    Settings(**{**PRODUCTION_OK, "s3_endpoint_url": "http://objectstore:8333",
                "api_public_base_url": "https://api.kumbh.example.org"})
    Settings(**{**PRODUCTION_OK, "s3_endpoint_url": "http://objectstore:8333",
                "s3_public_endpoint_url": "https://media.kumbh.example.org"})
    Settings(**{**PRODUCTION_OK, "email_provider": "smtp", "smtp_host": "smtp.example.org"})


def test_smtp_provider_requires_a_host():
    with pytest.raises(ValidationError):
        Settings(email_provider="smtp")


def test_verify_token_rejects_wrong_audience_and_signature():
    from app.common.errors import NotFound

    with pytest.raises(NotFound):
        verify_token(get_settings(), "eyJhbGciOiJIUzI1NiJ9.e30.x")
