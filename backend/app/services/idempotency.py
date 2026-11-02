import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.errors import Conflict, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.models import IdempotencyKey, User

UNIQUE_CONSTRAINT = "uq_idempotency_keys_user_key"


def _fingerprint(endpoint: str, payload: dict[str, Any]) -> str:
    body = json.dumps({"endpoint": endpoint, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _violates_key(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == UNIQUE_CONSTRAINT


def _check_key(key: str) -> None:
    if not 8 <= len(key) <= 128:
        raise ValidationFailed("Idempotency-Key must be 8-128 characters", code="invalid_idempotency_key")


def _existing(db: Session, user: User, key: str, request_hash: str) -> IdempotencyKey | None:
    existing = db.scalar(select(IdempotencyKey).where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == key))
    if existing is not None and existing.expires_at <= utcnow():
        db.delete(existing)
        db.flush()
        return None
    if existing is not None and (existing.request_hash != request_hash or existing.resource_id is None):
        raise Conflict("Idempotency-Key was already used for a different request", code="idempotency_key_reused")
    return existing


def run_idempotent_operation(
    db: Session,
    *,
    user: User,
    key: str | None,
    endpoint: str,
    payload: dict[str, Any],
    resource_id: uuid.UUID,
    operation: Callable[[], None],
) -> bool:
    """
    For workflow operations whose resource id is known up front and which commit themselves.
    The key row is flushed first, so it commits atomically with the operation; a failed operation
    rolls the key back and the client may retry. Returns True when the request was a replay.
    """
    if key is None:
        operation()
        return False
    _check_key(key)
    request_hash = _fingerprint(endpoint, payload)
    for attempt in range(2):
        if _existing(db, user, key, request_hash) is not None:
            db.rollback()
            return True
        try:
            db.add(
                IdempotencyKey(
                    user_id=user.id, key=key, endpoint=endpoint, request_hash=request_hash, resource_id=resource_id,
                    expires_at=utcnow() + timedelta(hours=get_settings().idempotency_ttl_hours),
                )
            )
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            if attempt or not _violates_key(exc):
                raise
            continue
        operation()
        return False
    raise Conflict("Could not complete the request; retry", code="idempotency_retry")


def run_idempotent(
    db: Session,
    *,
    user: User,
    key: str | None,
    endpoint: str,
    payload: dict[str, Any],
    create: Callable[[], uuid.UUID],
) -> tuple[uuid.UUID, bool]:
    """
    Runs `create` and commits. With a key, a retry of the same request returns the original resource id
    (replayed=True); reusing the key for a different request is a 409. Concurrent duplicates block on the
    unique index until the first commits, then replay.
    """
    if key is None:
        resource_id = create()
        db.commit()
        return resource_id, False
    if not 8 <= len(key) <= 128:
        raise ValidationFailed("Idempotency-Key must be 8-128 characters", code="invalid_idempotency_key")

    request_hash = _fingerprint(endpoint, payload)
    for attempt in range(2):
        existing = db.scalar(
            select(IdempotencyKey).where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == key)
        )
        if existing is not None and existing.expires_at <= utcnow():
            db.delete(existing)
            db.flush()
            existing = None
        if existing is not None:
            if existing.request_hash != request_hash or existing.resource_id is None:
                raise Conflict(
                    "Idempotency-Key was already used for a different request", code="idempotency_key_reused"
                )
            return existing.resource_id, True
        try:
            row = IdempotencyKey(
                user_id=user.id,
                key=key,
                endpoint=endpoint,
                request_hash=request_hash,
                expires_at=utcnow() + timedelta(hours=get_settings().idempotency_ttl_hours),
            )
            db.add(row)
            db.flush()
            row.resource_id = create()
            db.commit()
            return row.resource_id, False
        except IntegrityError as exc:
            db.rollback()
            if attempt or not _violates_key(exc):
                raise
    raise Conflict("Could not complete the request; retry", code="idempotency_retry")
