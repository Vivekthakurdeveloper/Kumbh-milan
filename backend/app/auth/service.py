import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.passwords import (
    dummy_hash,
    hash_password,
    needs_rehash,
    validate_new_password,
    verify_password,
)
from app.auth.tokens import create_access_token, hash_token, new_refresh_token, parse_refresh_token
from app.common.context import client_ip_var
from app.common.errors import TooManyRequests, Unauthenticated, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.observability.metrics import AUTH_FAILURES
from app.models import LoginAttempt, RefreshToken, User

INVALID_CREDENTIALS = "Invalid username or password"


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def _issue(db: Session, user: User, session: RefreshToken) -> TokenPair:
    refresh, refresh_hash = new_refresh_token(session.id)
    session.previous_token_hash = session.token_hash if session.token_hash else None
    session.token_hash = refresh_hash
    access, expires_in = create_access_token(
        user_id=user.id, role=user.role, session_id=session.id, token_version=user.token_version
    )
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)


def _is_locked_out(db: Session, username: str) -> bool:
    settings = get_settings()
    window_start = utcnow() - timedelta(minutes=settings.login_lockout_minutes)
    last_success = db.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.username == username, LoginAttempt.succeeded.is_(True)
        )
    )
    since = max(window_start, last_success) if last_success else window_start
    failures = db.scalar(
        select(func.count()).where(
            LoginAttempt.username == username,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at > since,
        )
    )
    return (failures or 0) >= settings.login_max_failures


def login(db: Session, *, username: str, password: str, user_agent: str | None) -> TokenPair:
    username = username.strip().lower()
    ip = client_ip_var.get()
    if _is_locked_out(db, username):
        AUTH_FAILURES.labels("locked_out").inc()
        raise TooManyRequests(
            "Too many failed sign-in attempts. Try again later.", code="account_temporarily_locked"
        )

    user = db.scalar(select(User).where(User.username == username))
    password_ok = verify_password(user.password_hash if user else dummy_hash(), password)
    if user is None or not password_ok or not user.is_active:
        db.add(LoginAttempt(username=username, client_ip=ip, succeeded=False))
        record_audit(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="user",
            actor_user_id=None,
            entity_id=user.id if user else None,
            after={"username": username, "reason": "inactive" if user and password_ok else "bad_credentials"},
        )
        db.commit()
        AUTH_FAILURES.labels("bad_credentials").inc()
        raise Unauthenticated(INVALID_CREDENTIALS, code="invalid_credentials")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    now = utcnow()
    user.last_login_at = now
    session = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash="",
        rotation_count=0,
        expires_at=now + timedelta(days=get_settings().refresh_token_ttl_days),
        last_used_at=now,
        user_agent=(user_agent or "")[:255] or None,
        client_ip=ip,
    )
    db.add(session)
    pair = _issue(db, user, session)
    db.add(LoginAttempt(username=username, client_ip=ip, succeeded=True))
    record_audit(db, action=AuditAction.LOGIN_SUCCEEDED, entity_type="user", actor_user_id=user.id, entity_id=user.id)
    db.commit()
    return pair


def refresh(db: Session, *, refresh_token: str) -> TokenPair:
    session_id = parse_refresh_token(refresh_token)
    session = db.scalar(select(RefreshToken).where(RefreshToken.id == session_id).with_for_update())
    if session is None:
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token")
    presented = hash_token(refresh_token)

    if session.previous_token_hash and hmac.compare_digest(presented, session.previous_token_hash):
        # A rotated-out token came back: treat the session as stolen.
        if session.revoked_at is None:
            session.revoked_at = utcnow()
            session.revoked_reason = "refresh_token_reuse"
            record_audit(
                db,
                action=AuditAction.TOKEN_REUSE_DETECTED,
                entity_type="session",
                actor_user_id=session.user_id,
                entity_id=session.id,
            )
            db.commit()
        AUTH_FAILURES.labels("refresh_token_reuse").inc()
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token")

    if not hmac.compare_digest(presented, session.token_hash):
        raise Unauthenticated("Invalid refresh token", code="invalid_refresh_token")
    if session.revoked_at is not None or session.expires_at <= utcnow():
        raise Unauthenticated("Session is no longer valid", code="session_revoked")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise Unauthenticated("Session is no longer valid", code="session_revoked")

    pair = _issue(db, user, session)
    session.rotation_count += 1
    session.last_used_at = utcnow()
    db.commit()
    return pair


def logout(db: Session, *, user: User, session_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == session_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason="logout")
    )
    record_audit(db, action=AuditAction.LOGOUT, entity_type="session", actor_user_id=user.id, entity_id=session_id)
    db.commit()


def revoke_all_sessions(db: Session, user: User, reason: str) -> None:
    """Invalidates every refresh token and, via token_version, every outstanding access token."""
    user.token_version += 1
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )


def change_password(db: Session, *, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise ValidationFailed("Current password is incorrect", code="invalid_current_password")
    if current_password == new_password:
        raise ValidationFailed("New password must differ from the current password", code="weak_password")
    validate_new_password(new_password, username=user.username)
    user.password_hash = hash_password(new_password)
    revoke_all_sessions(db, user, "password_changed")
    record_audit(db, action=AuditAction.PASSWORD_CHANGED, entity_type="user", actor_user_id=user.id, entity_id=user.id)
    db.commit()
