"""Two-step sign-in: POST /auth/login answers mfa_required for TOTP accounts; POST /auth/mfa/verify-login issues tokens."""

import threading
import time
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.auth import service as auth_service
from app.auth.mfa import decode_secret, encode_secret, generate_secret, totp
from app.common.crypto import encrypt_text
from app.common.errors import TooManyRequests, Unauthenticated
from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal
from app.main import create_app
from app.models import AuditEvent, LoginAttempt, MfaLoginChallenge, RefreshToken, User
from app.models.enums import Role
from tests.helpers import API, PASSWORD, create_user, login

TOKEN_KEYS = {"access_token", "refresh_token", "token_type", "expires_in"}


def enable_mfa(user_id: uuid.UUID) -> bytes:
    """Turns on TOTP directly, as a completed enrollment would."""
    secret = generate_secret()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.totp_secret_ciphertext = encrypt_text(encode_secret(secret))
        user.totp_enabled = True
        db.commit()
    return secret


def next_code(secret: bytes) -> str:
    """A code for the next 30-second step: valid within the drift window and never used before in this test."""
    return totp(secret, time.time() + 30)


def forget_used_code(user_id: uuid.UUID) -> None:
    """Lets a test sign in twice within one 30-second step without waiting for a new code."""
    with SessionLocal() as db:
        db.get(User, user_id).totp_last_used_step = None
        db.commit()


def start_login(client: TestClient, username: str, password: str = PASSWORD) -> dict:
    response = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def verify(client: TestClient, challenge: str, code: str):
    return client.post(f"{API}/auth/mfa/verify-login", json={"challenge_token": challenge, "mfa_code": code})


@pytest.fixture
def mfa_user(client):
    user_id = create_user("mfa_admin", Role.ADMIN, None)
    return user_id, enable_mfa(user_id)


def _sessions(user_id: uuid.UUID) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(RefreshToken).where(RefreshToken.user_id == user_id))


# --- login, step 1 -------------------------------------------------------------------------------


def test_login_without_mfa_is_unchanged(client, actors):
    body = start_login(client, "police")
    assert set(body) == TOKEN_KEYS
    assert body["token_type"] == "bearer"
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["mfa_enabled"] is False


def test_mfa_login_returns_challenge_and_no_tokens(client, mfa_user):
    user_id, _ = mfa_user
    body = start_login(client, "mfa_admin")
    assert body["mfa_required"] is True
    assert body["message"] == "MFA verification required."
    assert body["challenge_expires_in"] == get_settings().mfa_login_challenge_ttl_seconds
    assert body["challenge_token"].startswith("mfa.")
    assert not TOKEN_KEYS & set(body)
    assert _sessions(user_id) == 0
    with SessionLocal() as db:
        challenge = db.scalars(select(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user_id)).one()
        assert challenge.token_hash != body["challenge_token"] and len(challenge.token_hash) == 64
        assert not db.scalars(select(LoginAttempt).where(LoginAttempt.succeeded.is_(True))).all()


def test_wrong_password_on_mfa_account_is_generic(client, mfa_user):
    wrong = client.post(f"{API}/auth/login", json={"username": "mfa_admin", "password": "not-the-password"})
    unknown = client.post(f"{API}/auth/login", json={"username": "ghost_user", "password": "not-the-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == {**unknown.json()["error"], "request_id": wrong.json()["error"]["request_id"]}
    assert "challenge_token" not in wrong.text


# --- verify-login, step 2 ------------------------------------------------------------------------


def test_correct_code_issues_working_tokens(client, mfa_user):
    user_id, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    response = verify(client, challenge, next_code(secret))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == TOKEN_KEYS and body["token_type"] == "bearer"
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["mfa_enabled"] is True
    assert _sessions(user_id) == 1
    with SessionLocal() as db:
        row = db.scalars(select(MfaLoginChallenge)).one()
        assert row.consumed_reason == "used"
        assert db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(True))) == 1


def test_wrong_code_is_rejected_and_issues_nothing(client, mfa_user):
    user_id, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    good = next_code(secret)
    wrong = f"{(int(good) + 1) % 1_000_000:06d}"
    response = verify(client, challenge, wrong)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "mfa_code_invalid"
    assert "access_token" not in response.text
    assert _sessions(user_id) == 0
    # The challenge survives a single typo.
    assert verify(client, challenge, good).status_code == 200


def test_expired_challenge_is_rejected(client, mfa_user):
    _, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    with SessionLocal() as db:
        db.execute(update(MfaLoginChallenge).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    response = verify(client, challenge, next_code(secret))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "mfa_challenge_expired"
    assert verify(client, challenge, next_code(secret)).json()["error"]["code"] == "mfa_challenge_invalid"


def test_consumed_challenge_cannot_be_replayed(client, mfa_user):
    _, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    code = next_code(secret)
    assert verify(client, challenge, code).status_code == 200
    replay = verify(client, challenge, code)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "mfa_challenge_invalid"


def test_same_code_cannot_be_used_for_a_second_login(client, mfa_user):
    _, secret = mfa_user
    code = next_code(secret)
    assert verify(client, start_login(client, "mfa_admin")["challenge_token"], code).status_code == 200
    second = verify(client, start_login(client, "mfa_admin")["challenge_token"], code)
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "mfa_code_invalid"


def test_tampered_or_unknown_challenge_is_rejected(client, mfa_user):
    _, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    for bad in (challenge[:-2] + "xx", f"mfa.{uuid.uuid4()}.abc", "not-a-challenge", challenge.removeprefix("mfa.")):
        response = verify(client, bad, next_code(secret))
        assert response.status_code == 401, bad
        assert response.json()["error"]["code"] == "mfa_challenge_invalid"
    assert verify(client, challenge, next_code(secret)).status_code == 200


def test_newer_login_supersedes_older_challenge(client, mfa_user):
    _, secret = mfa_user
    first = start_login(client, "mfa_admin")["challenge_token"]
    second = start_login(client, "mfa_admin")["challenge_token"]
    assert verify(client, first, next_code(secret)).json()["error"]["code"] == "mfa_challenge_invalid"
    assert verify(client, second, next_code(secret)).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"challenge_token": "mfa.x.y", "mfa_code": "12345"},
        {"challenge_token": "mfa.x.y", "mfa_code": "1234567"},
        {"challenge_token": "mfa.x.y", "mfa_code": "12a456"},
        {"challenge_token": "mfa.x.y", "mfa_code": 123456},
        {"challenge_token": "mfa.x.y"},
        {"mfa_code": "123456"},
        {"challenge_token": "", "mfa_code": "123456"},
        {"challenge_token": "mfa.x.y", "mfa_code": "123456", "extra": True},
    ],
)
def test_malformed_verify_request_is_422(client, payload):
    response = client.post(f"{API}/auth/mfa/verify-login", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert "123456" not in response.text and "12345" not in str(response.json()["error"]["details"])


def test_too_many_wrong_codes_burn_the_challenge(client, mfa_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "mfa_login_max_attempts", 2)
    user_id, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    good = next_code(secret)
    wrong = f"{(int(good) + 7) % 1_000_000:06d}"
    assert verify(client, challenge, wrong).status_code == 401
    burned = verify(client, challenge, wrong)
    assert burned.status_code == 429
    assert burned.json()["error"]["code"] == "mfa_attempts_exceeded"
    # Even the right code no longer works on a burned challenge.
    after = verify(client, challenge, good)
    assert after.status_code == 429 and "access_token" not in after.text
    assert _sessions(user_id) == 0
    with SessionLocal() as db:
        assert db.scalars(select(AuditEvent.action).where(AuditEvent.action == "MFA_CHALLENGE_BURNED")).all()


def test_wrong_codes_count_toward_account_lockout(client, mfa_user):
    _, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    good = next_code(secret)
    wrong = f"{(int(good) + 3) % 1_000_000:06d}"
    for _ in range(get_settings().login_max_failures):
        assert verify(client, challenge, wrong).status_code == 401
    locked = verify(client, challenge, good)
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_temporarily_locked"
    relogin = client.post(f"{API}/auth/login", json={"username": "mfa_admin", "password": PASSWORD})
    assert relogin.status_code == 429


def test_challenge_token_is_not_an_access_or_refresh_token(client, mfa_user):
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    headers = {"Authorization": f"Bearer {challenge}"}
    for method, path in (("GET", "/auth/me"), ("GET", "/cases"), ("POST", "/auth/logout"), ("GET", "/users")):
        response = client.request(method, f"{API}{path}", headers=headers)
        assert response.status_code == 401, path
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": challenge}).status_code == 401
    assert client.post(f"{API}/assistant/ask", json={"question": "help"}, headers=headers).status_code == 401


def test_password_change_invalidates_pending_challenge(client, mfa_user):
    user_id, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    with SessionLocal() as db:
        user = db.get(User, user_id)
        auth_service.revoke_all_sessions(db, user, "password_changed")
        db.commit()
    response = verify(client, challenge, next_code(secret))
    assert response.status_code == 401 and response.json()["error"]["code"] == "mfa_challenge_invalid"


def test_deactivated_user_cannot_finish_mfa(client, mfa_user):
    user_id, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    with SessionLocal() as db:
        db.get(User, user_id).is_active = False
        db.commit()
    assert verify(client, challenge, next_code(secret)).status_code == 401


# --- unchanged session behaviour -----------------------------------------------------------------


def test_refresh_and_logout_unchanged_after_mfa_login(client, mfa_user):
    user_id, secret = mfa_user
    tokens = verify(client, start_login(client, "mfa_admin")["challenge_token"], next_code(secret)).json()
    rotated = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200 and set(rotated.json()) == TOKEN_KEYS
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 401

    forget_used_code(user_id)
    fresh = verify(client, start_login(client, "mfa_admin")["challenge_token"], next_code(secret))
    assert fresh.status_code == 200
    headers = {"Authorization": f"Bearer {fresh.json()['access_token']}"}
    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": fresh.json()["refresh_token"]}).status_code == 401


def test_concurrent_verification_succeeds_once(client, mfa_user):
    _, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    code = next_code(secret)
    results: list[str] = []
    barrier = threading.Barrier(5)

    def attempt() -> None:
        with SessionLocal() as db:
            barrier.wait()
            try:
                auth_service.verify_mfa_login(db, challenge_token=challenge, code=code, user_agent=None)
                results.append("ok")
            except (Unauthenticated, TooManyRequests) as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert results.count("ok") == 1, results
    assert set(results) - {"ok"} <= {"mfa_challenge_invalid"}


# --- enrollment through the API ------------------------------------------------------------------


def test_enroll_confirm_then_login_requires_mfa(client, actors, monkeypatch):
    monkeypatch.setattr(get_settings(), "mfa_mode", "totp")
    other_headers, other_refresh = login(client, "helpdesk")
    enrolled = client.post(f"{API}/auth/mfa/enroll", headers=actors.helpdesk.headers)
    assert enrolled.status_code == 200
    secret = decode_secret(enrolled.json()["secret"])
    confirm = client.post(f"{API}/auth/mfa/confirm", json={"code": totp(secret)}, headers=actors.helpdesk.headers)
    assert confirm.status_code == 204
    # The enrolling session stays; sessions signed in with only the password are signed out.
    assert client.get(f"{API}/auth/me", headers=actors.helpdesk.headers).json()["mfa_enabled"] is True
    assert client.get(f"{API}/auth/me", headers=other_headers).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": other_refresh}).status_code == 401

    body = start_login(client, "helpdesk")
    assert body["mfa_required"] is True and "access_token" not in body
    assert verify(client, body["challenge_token"], next_code(secret)).status_code == 200


def test_mfa_still_required_after_mfa_mode_is_switched_off(client, mfa_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "mfa_mode", "disabled")
    _, secret = mfa_user
    body = start_login(client, "mfa_admin")
    assert body["mfa_required"] is True
    assert verify(client, body["challenge_token"], next_code(secret)).status_code == 200


def test_secrets_never_reach_the_audit_log(client, mfa_user):
    _, secret = mfa_user
    challenge = start_login(client, "mfa_admin")["challenge_token"]
    code = next_code(secret)
    verify(client, challenge, f"{(int(code) + 1) % 1_000_000:06d}")
    verify(client, challenge, code)
    with SessionLocal() as db:
        dumped = " ".join(str((e.before_state, e.after_state)) for e in db.scalars(select(AuditEvent)))
    assert challenge not in dumped and challenge.split(".")[-1] not in dumped
    assert code not in dumped and encode_secret(secret) not in dumped


def test_verify_login_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_mfa_verify_per_minute", 2)
    with TestClient(create_app()) as limited:
        payload = {"challenge_token": "mfa.x.y", "mfa_code": "123456"}
        statuses = [limited.post(f"{API}/auth/mfa/verify-login", json=payload).status_code for _ in range(3)]
        assert statuses[:2] == [401, 401]
        assert statuses[2] == 429
