from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent
from tests.helpers import API, PASSWORD, login


def test_login_and_me(client, actors):
    me = client.get(f"{API}/auth/me", headers=actors.police.headers)
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == "police"
    assert "password_hash" not in body
    assert "case:resolve" in body["permissions"]


def test_bad_credentials_are_generic(client, actors):
    wrong_pw = client.post(f"{API}/auth/login", json={"username": "police", "password": "nope-nope-nope"})
    unknown = client.post(f"{API}/auth/login", json={"username": "ghost", "password": "nope-nope-nope"})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_login_lockout_after_repeated_failures(client, actors):
    for _ in range(3):
        assert client.post(f"{API}/auth/login", json={"username": "helpdesk", "password": "wrong-password"}).status_code == 401
    locked = client.post(f"{API}/auth/login", json={"username": "helpdesk", "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_temporarily_locked"


def test_failed_login_audit_never_stores_password(client, actors):
    client.post(f"{API}/auth/login", json={"username": "police", "password": "Secret-Guess-123"})
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "LOGIN_FAILED")).all()
    assert events and all("Secret-Guess-123" not in str(e.after_state) for e in events)


def test_unauthenticated_and_garbage_tokens_rejected(client, actors):
    assert client.get(f"{API}/cases").status_code == 401
    bad = client.get(f"{API}/cases", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401
    assert bad.headers["www-authenticate"] == "Bearer"


def test_refresh_rotates_and_detects_reuse(client, actors):
    first = client.post(f"{API}/auth/refresh", json={"refresh_token": actors.police.refresh_token})
    assert first.status_code == 200
    rotated = first.json()["refresh_token"]
    assert rotated != actors.police.refresh_token

    reuse = client.post(f"{API}/auth/refresh", json={"refresh_token": actors.police.refresh_token})
    assert reuse.status_code == 401
    # Reuse revokes the session, so the rotated token and its access token die too.
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": rotated}).status_code == 401
    new_access = {"Authorization": f"Bearer {first.json()['access_token']}"}
    assert client.get(f"{API}/auth/me", headers=new_access).status_code == 401


def test_logout_revokes_access_token_immediately(client, actors):
    headers, refresh = login(client, "police2")
    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": refresh}).status_code == 401
    assert client.get(f"{API}/auth/me", headers=actors.police2.headers).status_code == 200


def test_change_password_revokes_all_sessions(client, actors):
    weak = client.post(
        f"{API}/auth/change-password", headers=actors.police.headers,
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert weak.status_code == 422
    ok = client.post(
        f"{API}/auth/change-password", headers=actors.police.headers,
        json={"current_password": PASSWORD, "new_password": "Yamuna#Bank2027!"},
    )
    assert ok.status_code == 204
    assert client.get(f"{API}/auth/me", headers=actors.police.headers).status_code == 401
    login(client, "police", "Yamuna#Bank2027!")


def test_deactivated_user_is_locked_out(client, actors):
    r = client.patch(f"{API}/users/{actors.helpdesk.id}", headers=actors.admin.headers, json={"is_active": False})
    assert r.status_code == 200
    assert client.get(f"{API}/auth/me", headers=actors.helpdesk.headers).status_code == 401
    assert client.post(f"{API}/auth/login", json={"username": "helpdesk", "password": PASSWORD}).status_code == 401


def test_admin_cannot_lock_themselves_out(client, actors):
    r = client.patch(f"{API}/users/{actors.admin.id}", headers=actors.admin.headers, json={"is_active": False})
    assert r.status_code == 403
