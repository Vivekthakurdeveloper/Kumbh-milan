"""Push device token registration for staff users and verified citizen case sessions."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.public_tokens import decode_public_token
from app.common.crypto import encrypt_text
from app.common.errors import NotFound, Unauthenticated, ValidationFailed
from app.common.security_hash import keyed_hash
from app.common.time import utcnow
from app.models import Case, PushDeviceToken, User
from app.models.enums import PushPlatform
from app.schemas.public import PushTokenRegister


def register_token(
    db: Session,
    *,
    payload: PushTokenRegister,
    staff_user: User | None = None,
    citizen_authorization: str | None = None,
) -> PushDeviceToken:
    token_hash = keyed_hash(payload.token)
    case_id = None
    user_id = None
    if staff_user is not None:
        user_id = staff_user.id
    elif citizen_authorization:
        if not citizen_authorization.lower().startswith("bearer "):
            raise Unauthenticated("Citizen status authorization required")
        claims = decode_public_token(citizen_authorization.split(" ", 1)[1], expected_typ="case_status")
        if not payload.case_id or claims.get("case_number") != payload.case_id:
            raise ValidationFailed("case_id must match the verified status session", code="case_mismatch")
        case = db.scalar(select(Case).where(Case.case_number == payload.case_id))
        if case is None or str(case.id) != claims.get("case_id"):
            raise NotFound("Case not found", code="case_not_found")
        case_id = case.id
    else:
        raise Unauthenticated("Authentication required")

    existing = db.scalar(select(PushDeviceToken).where(PushDeviceToken.token_lookup_hash == token_hash))
    if existing is not None:
        existing.user_id = user_id
        existing.case_id = case_id
        existing.platform = payload.platform
        existing.token_ciphertext = encrypt_text(payload.token)
        existing.is_active = True
        existing.last_seen_at = utcnow()
        existing.deactivated_at = None
        existing.deactivation_reason = None
        row = existing
    else:
        row = PushDeviceToken(
            id=uuid.uuid4(),
            user_id=user_id,
            case_id=case_id,
            platform=payload.platform,
            token_ciphertext=encrypt_text(payload.token),
            token_lookup_hash=token_hash,
            is_active=True,
            last_seen_at=utcnow(),
        )
        db.add(row)
    record_audit(
        db,
        action=AuditAction.PUSH_TOKEN_REGISTERED,
        entity_type="push_device_token",
        entity_id=row.id,
        actor_user_id=user_id,
        after={"platform": payload.platform, "case_bound": case_id is not None},
    )
    db.commit()
    db.refresh(row)
    return row


def delete_token(
    db: Session,
    *,
    token: str,
    staff_user: User | None = None,
    citizen_authorization: str | None = None,
) -> None:
    token_hash = keyed_hash(token)
    row = db.scalar(select(PushDeviceToken).where(PushDeviceToken.token_lookup_hash == token_hash))
    if row is None:
        return
    if staff_user is not None:
        if row.user_id != staff_user.id:
            raise NotFound("Push token not found", code="push_token_not_found")
    elif citizen_authorization:
        claims = decode_public_token(citizen_authorization.split(" ", 1)[1], expected_typ="case_status")
        if row.case_id is None or str(row.case_id) != claims.get("case_id"):
            raise NotFound("Push token not found", code="push_token_not_found")
    else:
        raise Unauthenticated("Authentication required")
    row.is_active = False
    row.deactivated_at = utcnow()
    row.deactivation_reason = "user_removed"
    record_audit(
        db,
        action=AuditAction.PUSH_TOKEN_REMOVED,
        entity_type="push_device_token",
        entity_id=row.id,
        actor_user_id=staff_user.id if staff_user else None,
        after={"deactivation_reason": "user_removed"},
    )
    db.commit()
