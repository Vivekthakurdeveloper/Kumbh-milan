"""Durable resumable upload sessions for offline mobile attachments."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from io import BytesIO
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.errors import AppError, Conflict, NotFound, PayloadTooLarge, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.models import AttachmentUploadSession, CaseAttachment, User
from app.models.enums import AttachmentUploadSessionStatus, CaseStatus, MediaType
from app.schemas.sync import (
    AttachmentUploadFinalizeResponse,
    AttachmentUploadSessionCreate,
    AttachmentUploadSessionRead,
)
from app.schemas.workflow import AttachmentRead
from app.services import attachments as attachment_service
from app.services.access import load_case
from app.storage import ObjectStorage
from app.storage.scanner import MalwareScanner

ACTIVE_STATUSES = (
    AttachmentUploadSessionStatus.OPEN,
    AttachmentUploadSessionStatus.READY,
)


def _max_bytes(media_type: MediaType) -> int:
    settings = get_settings()
    return settings.max_photo_bytes if media_type is MediaType.PHOTO else settings.max_audio_bytes


def _staging_key(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    return f"sync-uploads/{user_id}/{session_id.hex}.part"


def to_read(row: AttachmentUploadSession) -> AttachmentUploadSessionRead:
    return AttachmentUploadSessionRead.model_validate(row)


def _same_manifest(row: AttachmentUploadSession, req: AttachmentUploadSessionCreate) -> bool:
    return (
        row.case_id == req.case_id
        and row.device_id == req.device_id
        and row.client_record_id == req.client_record_id
        and row.client_id == req.client_id
        and row.media_type == req.media_type
        and row.expected_checksum_sha256 == req.expected_checksum_sha256
        and row.expected_size_bytes == req.expected_size_bytes
        and row.original_filename == req.original_filename
        and row.declared_content_type == req.declared_content_type
    )


def create_session(
    db: Session,
    actor: User,
    req: AttachmentUploadSessionCreate,
) -> tuple[AttachmentUploadSession, bool]:
    require_permission(actor, Permission.ATTACHMENT_UPLOAD)
    case = load_case(db, actor, req.case_id)
    if case.status == CaseStatus.CLOSED:
        raise Conflict("Closed cases do not accept new attachments", code="case_closed")
    limit = _max_bytes(req.media_type)
    if req.expected_size_bytes > limit:
        raise PayloadTooLarge(
            f"{req.media_type.title()} files are limited to {limit // (1024 * 1024)} MB"
        )

    existing = db.scalar(
        select(AttachmentUploadSession).where(
            AttachmentUploadSession.user_id == actor.id,
            AttachmentUploadSession.device_id == req.device_id,
            AttachmentUploadSession.client_record_id == req.client_record_id,
        )
    )
    if existing is not None:
        if not _same_manifest(existing, req):
            raise Conflict(
                "client_record_id was already used for a different upload manifest",
                code="client_record_reused",
                details={"session_id": existing.id},
            )
        return existing, True

    active_count = db.scalar(
        select(func.count(AttachmentUploadSession.id)).where(
            AttachmentUploadSession.user_id == actor.id,
            AttachmentUploadSession.device_id == req.device_id,
            AttachmentUploadSession.status.in_(ACTIVE_STATUSES),
        )
    ) or 0
    settings = get_settings()
    if active_count >= settings.sync_attachment_max_open_sessions_per_device:
        raise Conflict(
            "This device has too many open attachment uploads",
            code="upload_session_limit",
            details={"limit": settings.sync_attachment_max_open_sessions_per_device},
        )

    session_id = uuid.uuid4()
    row = AttachmentUploadSession(
        id=session_id,
        user_id=actor.id,
        case_id=case.id,
        device_id=req.device_id,
        client_record_id=req.client_record_id,
        client_id=req.client_id,
        media_type=req.media_type,
        expected_checksum_sha256=req.expected_checksum_sha256,
        expected_size_bytes=req.expected_size_bytes,
        original_filename=req.original_filename,
        declared_content_type=req.declared_content_type,
        status=AttachmentUploadSessionStatus.OPEN,
        bytes_received=0,
        staging_storage_key=_staging_key(actor.id, session_id),
        expires_at=utcnow() + timedelta(hours=settings.sync_attachment_session_ttl_hours),
    )
    db.add(row)
    record_audit(
        db,
        action=AuditAction.SYNC_ATTACHMENT_SESSION_CREATED,
        entity_type="attachment_upload_session",
        entity_id=row.id,
        actor_user_id=actor.id,
        case=case,
        after={
            "device_id": req.device_id,
            "client_record_id": req.client_record_id,
            "media_type": req.media_type,
            "expected_size_bytes": req.expected_size_bytes,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(AttachmentUploadSession).where(
                AttachmentUploadSession.user_id == actor.id,
                AttachmentUploadSession.device_id == req.device_id,
                AttachmentUploadSession.client_record_id == req.client_record_id,
            )
        )
        if existing is not None and _same_manifest(existing, req):
            return existing, True
        raise exc
    return row, False


def _load(
    db: Session,
    actor: User,
    session_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> AttachmentUploadSession:
    require_permission(actor, Permission.ATTACHMENT_UPLOAD)
    stmt = select(AttachmentUploadSession).where(
        AttachmentUploadSession.id == session_id,
        AttachmentUploadSession.user_id == actor.id,
    )
    row = db.scalar(stmt.with_for_update() if for_update else stmt)
    if row is None:
        raise NotFound("Attachment upload session not found", code="upload_session_not_found")
    load_case(db, actor, row.case_id)
    return row


def _expire_if_needed(
    db: Session,
    storage: ObjectStorage,
    row: AttachmentUploadSession,
) -> None:
    if row.expires_at > utcnow() or row.status not in ACTIVE_STATUSES:
        return
    row.status = AttachmentUploadSessionStatus.EXPIRED
    row.last_error_code = "upload_session_expired"
    storage.delete(row.staging_storage_key)
    db.commit()
    raise Conflict("Attachment upload session expired", code="upload_session_expired")


def upload_chunk(
    db: Session,
    storage: ObjectStorage,
    actor: User,
    session_id: uuid.UUID,
    *,
    offset: int,
    stream: BinaryIO,
) -> AttachmentUploadSession:
    row = _load(db, actor, session_id, for_update=True)
    _expire_if_needed(db, storage, row)
    if row.status not in ACTIVE_STATUSES:
        raise Conflict("Attachment upload session is not writable", code="upload_session_state")
    if offset != row.bytes_received:
        raise Conflict(
            "Upload offset does not match the server",
            code="upload_offset_mismatch",
            details={"expected_offset": row.bytes_received, "received_offset": offset},
        )
    remaining = row.expected_size_bytes - row.bytes_received
    chunk = stream.read(remaining + 1)
    if not chunk:
        raise ValidationFailed("Upload chunk is empty", code="empty_file")
    if len(chunk) > remaining:
        raise PayloadTooLarge("Upload exceeds the declared attachment size")

    previous = storage.get(row.staging_storage_key) if row.bytes_received else b""
    if len(previous) != row.bytes_received:
        raise Conflict(
            "Staged upload length does not match the session",
            code="upload_staging_mismatch",
        )
    combined = previous + chunk
    storage.put(
        row.staging_storage_key,
        combined,
        content_type="application/octet-stream",
        checksum_sha256=hashlib.sha256(combined).hexdigest(),
    )
    row.bytes_received = len(combined)
    row.status = (
        AttachmentUploadSessionStatus.READY
        if row.bytes_received == row.expected_size_bytes
        else AttachmentUploadSessionStatus.OPEN
    )
    row.last_error_code = None
    try:
        db.commit()
    except Exception:
        db.rollback()
        if previous:
            storage.put(
                row.staging_storage_key,
                previous,
                content_type="application/octet-stream",
                checksum_sha256=hashlib.sha256(previous).hexdigest(),
            )
        else:
            storage.delete(row.staging_storage_key)
        raise
    return row


def finalize_session(
    db: Session,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    actor: User,
    session_id: uuid.UUID,
) -> AttachmentUploadFinalizeResponse:
    row = _load(db, actor, session_id, for_update=True)
    _expire_if_needed(db, storage, row)
    if row.status is AttachmentUploadSessionStatus.FINALIZED and row.attachment_id is not None:
        attachment = db.get(CaseAttachment, row.attachment_id)
        if attachment is None:
            raise NotFound("Finalized attachment not found", code="attachment_not_found")
        return AttachmentUploadFinalizeResponse(
            session=to_read(row),
            attachment=_attachment_read(attachment),
            replayed=True,
        )
    if row.status is not AttachmentUploadSessionStatus.READY:
        raise Conflict(
            "Upload is not ready to finalize",
            code="upload_session_state",
            details={
                "status": row.status,
                "bytes_received": row.bytes_received,
                "expected_size_bytes": row.expected_size_bytes,
            },
        )

    data = storage.get(row.staging_storage_key)
    checksum = hashlib.sha256(data).hexdigest()
    if len(data) != row.expected_size_bytes or checksum != row.expected_checksum_sha256:
        row.status = AttachmentUploadSessionStatus.FAILED
        row.last_error_code = "upload_checksum_mismatch"
        storage.delete(row.staging_storage_key)
        db.commit()
        raise ValidationFailed(
            "Uploaded bytes do not match the declared size and checksum",
            code="upload_checksum_mismatch",
        )

    try:
        attachment, replayed = attachment_service.upload_attachment(
            db,
            storage,
            actor,
            row.case_id,
            media_type=row.media_type,
            stream=BytesIO(data),
            filename=row.original_filename,
            declared_content_type=row.declared_content_type,
            scanner=scanner,
            idempotency_key=f"sync-attachment-{row.id}",
            client_id=row.client_id,
            device_id=row.device_id,
            client_record_id=row.client_record_id,
            client_created_at=row.created_at,
        )
    except AppError as exc:
        row = db.get(AttachmentUploadSession, session_id, populate_existing=True)
        row.status = AttachmentUploadSessionStatus.FAILED
        row.last_error_code = exc.code
        storage.delete(row.staging_storage_key)
        db.commit()
        raise

    row = db.get(AttachmentUploadSession, session_id, populate_existing=True)
    row.status = AttachmentUploadSessionStatus.FINALIZED
    row.attachment_id = attachment.id
    row.last_error_code = None
    case = load_case(db, actor, row.case_id)
    record_audit(
        db,
        action=AuditAction.SYNC_ATTACHMENT_SESSION_FINALIZED,
        entity_type="attachment_upload_session",
        entity_id=row.id,
        actor_user_id=actor.id,
        case=case,
        after={"attachment_id": attachment.id, "replayed": replayed},
    )
    db.commit()
    storage.delete(row.staging_storage_key)
    return AttachmentUploadFinalizeResponse(
        session=to_read(row),
        attachment=_attachment_read(attachment),
        replayed=replayed,
    )


def _attachment_read(attachment: CaseAttachment) -> AttachmentRead:
    return AttachmentRead.model_validate(attachment).model_copy(
        update={"downloadable": attachment_service.is_downloadable(attachment)}
    )


def abort_session(
    db: Session,
    storage: ObjectStorage,
    actor: User,
    session_id: uuid.UUID,
) -> AttachmentUploadSession:
    row = _load(db, actor, session_id, for_update=True)
    if row.status is AttachmentUploadSessionStatus.FINALIZED:
        raise Conflict("A finalized upload cannot be aborted", code="upload_session_state")
    if row.status is not AttachmentUploadSessionStatus.ABORTED:
        row.status = AttachmentUploadSessionStatus.ABORTED
        storage.delete(row.staging_storage_key)
        case = load_case(db, actor, row.case_id)
        record_audit(
            db,
            action=AuditAction.SYNC_ATTACHMENT_SESSION_ABORTED,
            entity_type="attachment_upload_session",
            entity_id=row.id,
            actor_user_id=actor.id,
            case=case,
        )
        db.commit()
    return row


def list_device_sessions(
    db: Session,
    actor: User,
    *,
    device_id: str,
) -> list[AttachmentUploadSession]:
    return list(
        db.scalars(
            select(AttachmentUploadSession)
            .where(
                AttachmentUploadSession.user_id == actor.id,
                AttachmentUploadSession.device_id == device_id,
                AttachmentUploadSession.status.in_(
                    (
                        AttachmentUploadSessionStatus.OPEN,
                        AttachmentUploadSessionStatus.READY,
                        AttachmentUploadSessionStatus.FAILED,
                    )
                ),
            )
            .order_by(AttachmentUploadSession.created_at, AttachmentUploadSession.id)
        )
    )


def purge_expired_sessions(
    db: Session,
    storage: ObjectStorage,
    *,
    limit: int = 100,
) -> int:
    rows = list(
        db.scalars(
            select(AttachmentUploadSession)
            .where(
                AttachmentUploadSession.status.in_(ACTIVE_STATUSES),
                AttachmentUploadSession.expires_at <= utcnow(),
            )
            .order_by(AttachmentUploadSession.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        row.status = AttachmentUploadSessionStatus.EXPIRED
        row.last_error_code = "upload_session_expired"
        storage.delete(row.staging_storage_key)
    db.commit()
    return len(rows)
