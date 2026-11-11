import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.common.context import client_ip_var, request_id_var
from app.models import AuditEvent, Case

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "verified_mobile_token",
        "contact_details",
        "contact_ciphertext",
        "storage_key",
        "authorization",
        "embedding",
        "embedding_ciphertext",
        "otp",
        "code",
        "mobile_number",
        "recipient_phone",
        "email",
        "push_token",
        "token_ciphertext",
        "msg91_auth_key",
        "smtp_password",
        "fcm_credentials_json",
    }
)


class AuditAction(StrEnum):
    LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    TOKEN_REUSE_DETECTED = "TOKEN_REUSE_DETECTED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PASSWORD_RESET = "PASSWORD_RESET"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
    PASSWORD_RESET_COMPLETED = "PASSWORD_RESET_COMPLETED"
    USER_CREATED = "USER_CREATED"
    PUBLIC_USER_REGISTERED = "PUBLIC_USER_REGISTERED"
    USER_UPDATED = "USER_UPDATED"
    STATION_CREATED = "STATION_CREATED"
    STATION_UPDATED = "STATION_UPDATED"
    CASE_CREATED = "CASE_CREATED"
    CASE_UPDATED = "CASE_UPDATED"
    CASE_ASSIGNED = "CASE_ASSIGNED"
    CASE_STATUS_CHANGED = "CASE_STATUS_CHANGED"
    CASE_CLOSED = "CASE_CLOSED"
    CASE_REOPENED = "CASE_REOPENED"
    CUSTODY_CHANGED = "CUSTODY_CHANGED"
    CONTACT_UPDATED = "CONTACT_UPDATED"
    CONTACT_VIEWED = "CONTACT_VIEWED"
    NOTE_ADDED = "NOTE_ADDED"
    ATTACHMENT_UPLOADED = "ATTACHMENT_UPLOADED"
    ATTACHMENT_ACCESSED = "ATTACHMENT_ACCESSED"
    ATTACHMENT_DELETED = "ATTACHMENT_DELETED"
    MATCH_PROPOSED = "MATCH_PROPOSED"
    MATCH_REVIEWED = "MATCH_REVIEWED"
    MATCH_REVOKED = "MATCH_REVOKED"
    MATCH_SUGGESTED = "MATCH_SUGGESTED"
    MATCH_FIRST_APPROVAL = "MATCH_FIRST_APPROVAL"
    FACE_ANALYZED = "FACE_ANALYZED"
    FACE_COMPARISON_INITIATED = "FACE_COMPARISON_INITIATED"
    FACE_COMPARISON_COMPLETED = "FACE_COMPARISON_COMPLETED"
    FACE_MATCH_CANDIDATE_GENERATED = "FACE_MATCH_CANDIDATE_GENERATED"
    FACE_EMBEDDING_DELETED = "FACE_EMBEDDING_DELETED"
    ATTACHMENT_SCAN_CLEAN = "ATTACHMENT_SCAN_CLEAN"
    ATTACHMENT_REJECTED = "ATTACHMENT_REJECTED"
    ATTACHMENT_SCAN_FAILED = "ATTACHMENT_SCAN_FAILED"
    ATTACHMENT_OBJECT_PURGED = "ATTACHMENT_OBJECT_PURGED"
    LOCATION_CREATED = "LOCATION_CREATED"
    LOCATION_UPDATED = "LOCATION_UPDATED"
    CASE_LOCATION_LINKED = "CASE_LOCATION_LINKED"
    LEGAL_HOLD_PLACED = "LEGAL_HOLD_PLACED"
    LEGAL_HOLD_RELEASED = "LEGAL_HOLD_RELEASED"
    RETENTION_RUN_PLANNED = "RETENTION_RUN_PLANNED"
    RETENTION_RUN_APPROVED = "RETENTION_RUN_APPROVED"
    RETENTION_RUN_EXECUTED = "RETENTION_RUN_EXECUTED"
    RETENTION_RUN_CANCELLED = "RETENTION_RUN_CANCELLED"
    CASE_ARCHIVED = "CASE_ARCHIVED"
    CASE_ANONYMIZED = "CASE_ANONYMIZED"
    NOTIFICATION_QUEUED = "NOTIFICATION_QUEUED"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    CASE_EXPORTED = "CASE_EXPORTED"
    SYNC_PUSH = "SYNC_PUSH"
    SYNC_PULL = "SYNC_PULL"
    SYNC_CONFLICT = "SYNC_CONFLICT"
    PUBLIC_OTP_REQUESTED = "PUBLIC_OTP_REQUESTED"
    PUBLIC_OTP_VERIFIED = "PUBLIC_OTP_VERIFIED"
    PUBLIC_OTP_FAILED = "PUBLIC_OTP_FAILED"
    PUBLIC_CASE_SUBMITTED = "PUBLIC_CASE_SUBMITTED"
    PUBLIC_CASE_REVIEWED = "PUBLIC_CASE_REVIEWED"
    PUBLIC_SIGHTING_REPORTED = "PUBLIC_SIGHTING_REPORTED"
    PUBLIC_INFORMATION_UPDATED = "PUBLIC_INFORMATION_UPDATED"
    PUSH_TOKEN_REGISTERED = "PUSH_TOKEN_REGISTERED"
    PUSH_TOKEN_REMOVED = "PUSH_TOKEN_REMOVED"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if k.lower() in SENSITIVE_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def record_audit(
    db: Session,
    *,
    action: AuditAction,
    entity_type: str,
    actor_user_id: uuid.UUID | None,
    entity_id: uuid.UUID | None = None,
    case: Case | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    """Adds an audit row to the caller's transaction, so it commits or rolls back with the change."""
    db.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action=action.value,
            entity_type=entity_type,
            entity_id=entity_id,
            case_id=case.id if case else None,
            case_number=case.case_number if case else None,
            before_state=redact(before) if before is not None else None,
            after_state=redact(after) if after is not None else None,
            reason=reason,
            request_id=request_id_var.get(),
            client_ip=client_ip_var.get(),
        )
    )
