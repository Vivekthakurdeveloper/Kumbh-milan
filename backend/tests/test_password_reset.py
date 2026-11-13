"""Self-service password reset: POST /auth/forgot-password and POST /auth/reset-password."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.common.time import utcnow
from app.database import SessionLocal
from app.models import AuditEvent, LoginAttempt, PasswordResetToken, User
from app.notifications import DisabledProvider, MockProvider
from app.notifications.email import DisabledEmailSender, EmailError, MockEmailSender
from tests.helpers import API, PASSWORD, login

NEW_PASSWORD = "Yamuna#Flow2028!"
GENERIC = "If an account matches, a reset code has been sent."


@pytest.fixture
def outbox(monkeypatch):
    email, sms = MockEmailSender(), MockProvider()
    monkeypatch.setattr("app.auth.password_reset.get_email_sender", lambda: email)
    monkeypatch.setattr("app.auth.password_reset.get_provider", lambda: sms)
    return email, sms


def _contact(username: str, *, email: str | None = None, phone: str | None = None, active: bool = True) -> None:
    with SessionLocal() as db:
        db.execute(
            update(User).where(User.username == username)
            .values(email=email, phone_number=phone, is_active=active)
        )
        db.commit()


def _code(text: str) -> str:
    match = re.search(r"\b(\d{8})\b", text)
    assert match, text
    return match.group(1)


def _forgot(client, identifier: str, **extra):
    return client.post(f"{API}/auth/forgot-password", json={"identifier": identifier, **extra})


def _reset(client, identifier: str, code: str, password: str = NEW_PASSWORD):
    return client.post(
        f"{API}/auth/reset-password", json={"identifier": identifier, "code": code, "new_password": password}
    )


def test_reset_by_email_end_to_end(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org")
    old_access = actors.police.headers

    response = _forgot(client, "police")
    assert response.status_code == 202
    assert response.json() == {"message": GENERIC, "code_length": 8, "expires_in_minutes": 15}
    assert len(email.sent) == 1 and email.sent[0].to == "officer@example.org"
    code = _code(email.sent[0].body)

    done = _reset(client, "police", code)
    assert done.status_code == 204, done.text

    assert client.post(f"{API}/auth/login", json={"username": "police", "password": PASSWORD}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"username": "police", "password": NEW_PASSWORD}).status_code == 200
    # Every session that existed before the reset is revoked.
    assert client.get(f"{API}/auth/me", headers=old_access).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": actors.police.refresh_token}).status_code == 401
    # The code works once.
    again = _reset(client, "police", code, "Another#Pass2029!")
    assert again.status_code == 422 and again.json()["error"]["code"] == "invalid_reset_code"
    # The user is told the password changed.
    assert len(email.sent) == 2 and "was changed" in email.sent[1].subject


def test_identifier_can_be_email_address_case_insensitively(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="Officer@Example.org")
    assert _forgot(client, "officer@EXAMPLE.org").status_code == 202
    assert len(email.sent) == 1
    assert _reset(client, "OFFICER@example.ORG", _code(email.sent[0].body)).status_code == 204


def test_reset_by_sms_using_mobile_number(client, actors, outbox):
    email, sms = outbox
    _contact("helpdesk", phone="98765 43210")
    assert _forgot(client, "+91 98765-43210").status_code == 202
    assert not email.sent and len(sms.deliveries) == 1
    assert sms.deliveries[0]["to"].endswith("3210")  # recipients are masked in the mock's record
    code = _code(sms.deliveries[0]["body"])
    assert _reset(client, "9876543210", code).status_code == 204
    assert client.post(f"{API}/auth/login", json={"username": "helpdesk", "password": NEW_PASSWORD}).status_code == 200


def test_explicit_channel_overrides_the_default(client, actors, outbox):
    email, sms = outbox
    _contact("police", email="officer@example.org", phone="9876501234")
    _forgot(client, "police", channel="SMS")
    assert not email.sent and len(sms.deliveries) == 1
    _forgot(client, "police", channel="EMAIL")
    assert len(email.sent) == 1


def test_unknown_and_known_accounts_get_identical_answers(client, actors, outbox):
    email, sms = outbox
    _contact("police", email="officer@example.org")
    known = _forgot(client, "police")
    unknown = _forgot(client, "nobody-here")
    no_channel = _forgot(client, "helpdesk")  # account exists but has no email or phone on file
    wrong_channel = _forgot(client, "police", channel="SMS")  # no phone on file
    assert {r.status_code for r in (known, unknown, no_channel, wrong_channel)} == {202}
    assert known.json() == unknown.json() == no_channel.json() == wrong_channel.json()
    assert len(email.sent) == 1 and not sms.deliveries


def test_inactive_account_gets_nothing(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org", active=False)
    assert _forgot(client, "police").status_code == 202
    assert not email.sent


def test_ambiguous_mobile_number_is_refused(client, actors, outbox):
    _, sms = outbox
    _contact("police", phone="9876543210")
    _contact("police2", phone="+91 98765 43210")
    assert _forgot(client, "9876543210").status_code == 202
    assert not sms.deliveries


def test_wrong_codes_burn_the_code(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org")
    _forgot(client, "police")
    code = _code(email.sent[0].body)
    wrong = "00000000" if code != "00000000" else "11111111"
    for _ in range(5):
        r = _reset(client, "police", wrong)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reset_code"
    # Even the right code no longer works.
    assert _reset(client, "police", code).status_code == 422
    with SessionLocal() as db:
        token = db.scalar(select(PasswordResetToken))
        assert token.consumed_reason == "too_many_attempts"
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "PASSWORD_RESET_CODE_BURNED")) is not None


def test_weak_password_does_not_spend_the_code(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org")
    _forgot(client, "police")
    code = _code(email.sent[0].body)
    weak = _reset(client, "police", code, "short")
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"
    assert _reset(client, "police", code).status_code == 204


def test_expired_code_is_rejected(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org")
    _forgot(client, "police")
    with SessionLocal() as db:
        db.execute(update(PasswordResetToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert _reset(client, "police", _code(email.sent[0].body)).status_code == 422


def test_new_request_supersedes_the_previous_code(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org")
    _forgot(client, "police")
    _forgot(client, "police")
    first, second = (_code(m.body) for m in email.sent)
    assert first != second
    assert _reset(client, "police", first).status_code == 422
    assert _reset(client, "police", second).status_code == 204


def test_requests_per_account_are_throttled_silently(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org")
    answers = [_forgot(client, "police") for _ in range(6)]
    assert {a.status_code for a in answers} == {202}
    assert len(email.sent) == 3  # PASSWORD_RESET_MAX_REQUESTS_PER_HOUR


def test_reset_clears_a_login_lockout(client, actors, outbox):
    email, _ = outbox
    _contact("helpdesk", email="desk@example.org")
    for _ in range(3):
        client.post(f"{API}/auth/login", json={"username": "helpdesk", "password": "wrong-password"})
    assert client.post(f"{API}/auth/login", json={"username": "helpdesk", "password": PASSWORD}).status_code == 429
    _forgot(client, "helpdesk")
    assert _reset(client, "helpdesk", _code(email.sent[0].body)).status_code == 204
    assert client.post(f"{API}/auth/login", json={"username": "helpdesk", "password": NEW_PASSWORD}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(LoginAttempt).where(LoginAttempt.succeeded.is_(False))) is None


def test_unavailable_when_no_delivery_is_configured(client, actors, monkeypatch):
    monkeypatch.setattr("app.auth.password_reset.get_email_sender", lambda: DisabledEmailSender())
    monkeypatch.setattr("app.auth.password_reset.get_provider", lambda: DisabledProvider())
    r = _forgot(client, "police")
    assert r.status_code == 503 and r.json()["error"]["code"] == "password_reset_unavailable"


def test_delivery_failure_is_not_visible_to_the_caller(client, actors, outbox):
    email, _ = outbox
    email.fail = EmailError("SMTPServerDisconnected")
    _contact("police", email="officer@example.org")
    r = _forgot(client, "police")
    assert r.status_code == 202 and r.json()["message"] == GENERIC


def test_code_is_never_stored_or_audited_in_clear(client, actors, outbox):
    email, _ = outbox
    _contact("police", email="officer@example.org")
    _forgot(client, "police")
    code = _code(email.sent[0].body)
    _reset(client, "police", code)
    with SessionLocal() as db:
        token = db.scalar(select(PasswordResetToken))
        assert code not in token.code_hash and len(token.code_hash) == 64
        assert token.consumed_reason == "used"
        events = db.scalars(select(AuditEvent).where(AuditEvent.action.like("PASSWORD_RESET%"))).all()
        assert {e.action for e in events} == {"PASSWORD_RESET_REQUESTED", "PASSWORD_RESET_COMPLETED"}
        blob = " ".join(str(e.after_state) for e in events)
        assert code not in blob and NEW_PASSWORD not in blob


def test_malformed_requests_are_rejected(client, actors, outbox):
    assert client.post(f"{API}/auth/forgot-password", json={}).status_code == 422
    assert _forgot(client, "police", channel="FAX").status_code == 422
    assert client.post(
        f"{API}/auth/reset-password", json={"identifier": "police", "code": "abc", "new_password": NEW_PASSWORD}
    ).status_code == 422


def test_admin_reset_route_still_works_and_is_separate(client, actors):
    r = client.post(
        f"{API}/users/{actors.police.id}/reset-password", json={"new_password": NEW_PASSWORD},
        headers=actors.admin.headers,
    )
    assert r.status_code == 204
    assert login(client, "police", NEW_PASSWORD)
