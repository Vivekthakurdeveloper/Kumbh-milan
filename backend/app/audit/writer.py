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
        "claimant_ciphertext",
        "claimant_full_name",
        "claimant_mobile_number",
        "claimant_address",
        "identity_document_reference",
        "witness_ciphertext",
        "witness_full_name",
        "witness_mobile_number",
        "witness_statement",
        "verification_ciphertext",
        "verification_evidence",
        "acknowledgement_ciphertext",
        "acknowledgement",
        "rejection_ciphertext",
        "rejection_reason",
        "resolution_summary",
        "secret",
        "webhook_secret",
        "msg91_webhook_secret",
        "chat_llm_api_key",
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
    PASSWORD_RESET_CODE_BURNED = "PASSWORD_RESET_CODE_BURNED"
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
    HANDOVER_CREATED = "HANDOVER_CREATED"
    HANDOVER_VERIFIED = "HANDOVER_VERIFIED"
    HANDOVER_REJECTED = "HANDOVER_REJECTED"
    HANDOVER_COMPLETED = "HANDOVER_COMPLETED"
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
    SYNC_TOMBSTONE_EMITTED = "SYNC_TOMBSTONE_EMITTED"
    SYNC_ATTACHMENT_SESSION_CREATED = "SYNC_ATTACHMENT_SESSION_CREATED"
    SYNC_ATTACHMENT_SESSION_FINALIZED = "SYNC_ATTACHMENT_SESSION_FINALIZED"
    SYNC_ATTACHMENT_SESSION_ABORTED = "SYNC_ATTACHMENT_SESSION_ABORTED"
    PUBLIC_OTP_REQUESTED = "PUBLIC_OTP_REQUESTED"
    PUBLIC_OTP_VERIFIED = "PUBLIC_OTP_VERIFIED"
    PUBLIC_OTP_FAILED = "PUBLIC_OTP_FAILED"
    PUBLIC_CASE_SUBMITTED = "PUBLIC_CASE_SUBMITTED"
    PUBLIC_CASE_REVIEWED = "PUBLIC_CASE_REVIEWED"
    PUBLIC_SIGHTING_REPORTED = "PUBLIC_SIGHTING_REPORTED"
    PUBLIC_INFORMATION_UPDATED = "PUBLIC_INFORMATION_UPDATED"
    PUSH_TOKEN_REGISTERED = "PUSH_TOKEN_REGISTERED"
    PUSH_TOKEN_REMOVED = "PUSH_TOKEN_REMOVED"
    CASE_UNASSIGNED = "CASE_UNASSIGNED"
    ASSIGNMENT_REQUESTED = "ASSIGNMENT_REQUESTED"
    ASSIGNMENT_APPROVED = "ASSIGNMENT_APPROVED"
    ASSIGNMENT_REJECTED = "ASSIGNMENT_REJECTED"
    ASSIGNMENT_CANCELLED = "ASSIGNMENT_CANCELLED"
    NOTIFICATION_DELIVERY_UPDATED = "NOTIFICATION_DELIVERY_UPDATED"
    REFERENCE_UPDATED = "REFERENCE_UPDATED"
    GRIEVANCE_SUBMITTED = "GRIEVANCE_SUBMITTED"
    GRIEVANCE_ASSIGNED = "GRIEVANCE_ASSIGNED"
    GRIEVANCE_STATUS_CHANGED = "GRIEVANCE_STATUS_CHANGED"
    GRIEVANCE_COMMENTED = "GRIEVANCE_COMMENTED"
    GRIEVANCE_ESCALATED = "GRIEVANCE_ESCALATED"
    REPORT_REQUESTED = "REPORT_REQUESTED"
    REPORT_GENERATED = "REPORT_GENERATED"
    REPORT_FAILED = "REPORT_FAILED"
    REPORT_DOWNLOADED = "REPORT_DOWNLOADED"
    REPORT_CANCELLED = "REPORT_CANCELLED"
    REPORT_SCHEDULED = "REPORT_SCHEDULED"
    REPORT_SCHEDULE_UPDATED = "REPORT_SCHEDULE_UPDATED"
    INTEGRATION_DESTINATION_CREATED = "INTEGRATION_DESTINATION_CREATED"
    INTEGRATION_DESTINATION_UPDATED = "INTEGRATION_DESTINATION_UPDATED"
    ALERT_CREATED = "ALERT_CREATED"
    ALERT_UPDATED = "ALERT_UPDATED"
    ALERT_APPROVED = "ALERT_APPROVED"
    ALERT_PUBLISHED = "ALERT_PUBLISHED"
    ALERT_WITHDRAWN = "ALERT_WITHDRAWN"
    ALERT_EXPIRED = "ALERT_EXPIRED"
    DISPATCH_DELIVERED = "DISPATCH_DELIVERED"
    DISPATCH_FAILED = "DISPATCH_FAILED"
    CASE_EXTERNAL_EXPORT_REQUESTED = "CASE_EXTERNAL_EXPORT_REQUESTED"
    INBOUND_MESSAGE_RECEIVED = "INBOUND_MESSAGE_RECEIVED"
    INBOUND_MESSAGE_REVIEWED = "INBOUND_MESSAGE_REVIEWED"
    CHAT_HANDOFF_REQUESTED = "CHAT_HANDOFF_REQUESTED"
    CHAT_STAFF_REPLY = "CHAT_STAFF_REPLY"
    CHAT_SESSION_CLOSED = "CHAT_SESSION_CLOSED"


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
