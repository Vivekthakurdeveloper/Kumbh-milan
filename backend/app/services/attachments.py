"""
Attachments: quarantine-first.

1. Validate size and file signature in memory (the transient UPLOADED stage).
2. Store under quarantine/{case}/{id} and insert the row as QUARANTINED (one transaction; the object is
   deleted if the row cannot be written).
3. Claim the row (SCANNING) and scan once synchronously. CLEAN files are copied to cases/{case}/{id};
   infected files are deleted and the row becomes REJECTED; an unreachable scanner leaves the row
   QUARANTINED with a backoff, and the scan worker retries until SCAN_FAILED.

Only CLEAN files (and SKIPPED_DEV files outside staging/production) can be downloaded.
"""

import hashlib
import logging
import uuid
from datetime import timedelta
from typing import BinaryIO

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.errors import Conflict, NotFound, PayloadTooLarge, UnsupportedMedia, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseAttachment, User
from app.models.enums import CaseStatus, MediaType, ScanStatus
from app.observability.metrics import SCAN_OUTCOMES
from app.services.access import load_case
from app.storage import ObjectStorage
from app.storage.scanner import MalwareScanner, ScannerUnavailable, ScanOutcome
from app.storage.validation import declared_type_conflicts, detect_media, safe_display_filename

logger = logging.getLogger("kumbh_setu.attachments")


def _max_bytes(media_type: MediaType) -> int:
    settings = get_settings()
    return settings.max_photo_bytes if media_type is MediaType.PHOTO else settings.max_audio_bytes


def quarantine_key(case_id: uuid.UUID, attachment_id: uuid.UUID) -> str:
    return f"quarantine/{case_id}/{attachment_id.hex}"


def final_key(case_id: uuid.UUID, attachment_id: uuid.UUID) -> str:
    return f"cases/{case_id}/{attachment_id.hex}"


def sighting_final_key(sighting_id: uuid.UUID, attachment_id: uuid.UUID) -> str:
    return f"sightings/{sighting_id}/{attachment_id.hex}"


def is_downloadable(attachment: CaseAttachment) -> bool:
    if attachment.scan_status == ScanStatus.CLEAN:
        return True
    return attachment.scan_status == ScanStatus.SKIPPED_DEV and get_settings().app_env in ("local", "test")


def find_attachment_by_client_record(
    db: Session, actor: User, *, device_id: str, client_record_id: str
) -> CaseAttachment | None:
    return db.scalar(
        select(CaseAttachment).where(
            CaseAttachment.uploaded_by_user_id == actor.id,
            CaseAttachment.device_id == device_id,
            CaseAttachment.client_record_id == client_record_id,
            CaseAttachment.deleted_at.is_(None),
        )
    )


def upload_attachment(
    db: Session,
    storage: ObjectStorage,
    actor: User,
    case_id: uuid.UUID,
    *,
    media_type: MediaType,
    stream: BinaryIO,
    filename: str | None,
    declared_content_type: str | None,
    scanner: MalwareScanner,
    idempotency_key: str | None = None,
    client_id: str | None = None,
    device_id: str | None = None,
    client_record_id: str | None = None,
    client_created_at=None,
) -> tuple[CaseAttachment, bool]:
    from sqlalchemy.exc import IntegrityError

    from app.services.idempotency import run_idempotent_operation

    require_permission(actor, Permission.ATTACHMENT_UPLOAD, allow_public=True)
    case = load_case(db, actor, case_id)
    if case.status == CaseStatus.CLOSED:
        raise Conflict("Closed cases do not accept new attachments", code="case_closed")
    if client_record_id is not None and not device_id:
        raise ValidationFailed("device_id is required when client_record_id is sent", code="device_id_required")

    limit = _max_bytes(media_type)
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge(f"{media_type.title()} files are limited to {limit // (1024 * 1024)} MB")
    if not data:
        raise ValidationFailed("File is empty", code="empty_file")

    detected = detect_media(data[:64])
    if detected is None:
        raise UnsupportedMedia("Unsupported or unrecognised file format", code="unsupported_media_type")
    if detected.media_type is not media_type:
        raise UnsupportedMedia(
            f"File content is {detected.media_type.lower()}, but {media_type.lower()} was declared",
            code="media_type_mismatch",
        )
    if declared_type_conflicts(declared_content_type, detected):
        raise UnsupportedMedia("Declared content type does not match the file content", code="content_type_mismatch")

    checksum = hashlib.sha256(data).hexdigest()
    if client_record_id is not None:
        existing = find_attachment_by_client_record(
            db, actor, device_id=device_id, client_record_id=client_record_id
        )
        if existing is not None:
            if existing.case_id != case.id:
                raise Conflict(
                    "client_record_id was already used for an attachment on a different case",
                    code="client_record_reused",
                    details={"attachment_id": existing.id, "case_id": existing.case_id},
                )
            if existing.checksum_sha256 != checksum:
                raise Conflict(
                    "client_record_id was already used for a different file",
                    code="client_record_reused",
                    details={"attachment_id": existing.id, "checksum_sha256": existing.checksum_sha256},
                )
            return existing, True

    attachment_id = uuid.uuid4()
    key = quarantine_key(case.id, attachment_id)
    fingerprint = {
        "case_id": str(case.id),
        "media_type": media_type.value,
        "checksum_sha256": checksum,
        "client_record_id": client_record_id,
        "device_id": device_id,
    }
    stored = False

    def _store() -> None:
        nonlocal stored
        storage.put(key, data, content_type=detected.content_type, checksum_sha256=checksum)
        stored = True
        attachment = CaseAttachment(
            id=attachment_id,
            case_id=case.id,
            uploaded_by_user_id=actor.id,
            media_type=media_type,
            storage_key=key,
            original_filename=safe_display_filename(filename, detected.extension),
            content_type=detected.content_type,
            file_size_bytes=len(data),
            checksum_sha256=checksum,
            scan_status=ScanStatus.QUARANTINED,
            next_scan_at=utcnow(),
            client_id=client_id,
            device_id=device_id,
            client_record_id=client_record_id,
            client_created_at=client_created_at,
            server_synced_at=utcnow() if client_record_id else None,
        )
        db.add(attachment)
        record_audit(
            db, action=AuditAction.ATTACHMENT_UPLOADED, entity_type="case_attachment", entity_id=attachment_id,
            actor_user_id=actor.id, case=case,
            after={
                "media_type": media_type,
                "content_type": detected.content_type,
                "size": len(data),
                "sha256": checksum,
                "scan_status": ScanStatus.QUARANTINED,
                "client_record_id": client_record_id,
                "device_id": device_id,
            },
        )
        db.commit()

    try:
        replayed = run_idempotent_operation(
            db,
            user=actor,
            key=idempotency_key,
            endpoint=f"attachment:{case.id}",
            payload=fingerprint,
            resource_id=attachment_id,
            operation=_store,
        )
    except IntegrityError:
        db.rollback()
        if stored:
            storage.delete(key)
            stored = False
        if client_record_id is None:
            raise
        existing = find_attachment_by_client_record(
            db, actor, device_id=device_id, client_record_id=client_record_id
        )
        if existing is None:
            raise
        return existing, True
    except Exception:
        if stored:
            storage.delete(key)
        raise

    if replayed:
        from app.models import IdempotencyKey

        attachment_id = db.scalar(
            select(IdempotencyKey.resource_id).where(
                IdempotencyKey.user_id == actor.id, IdempotencyKey.key == idempotency_key
            )
        )
        attachment = db.get(CaseAttachment, attachment_id)
        if attachment is None:
            raise NotFound("Attachment not found", code="attachment_not_found")
        return attachment, True

    process_scan(db, storage, scanner, attachment_id, data=data)
    db.expire_all()
    attachment = db.get(CaseAttachment, attachment_id)
    if attachment.scan_status == ScanStatus.REJECTED:
        raise ValidationFailed(
            "File was rejected by malware scanning and has been deleted", code="malware_detected",
            details={"attachment_id": attachment_id},
        )
    return attachment, False


def _claim(db: Session, attachment_id: uuid.UUID) -> bool:
    claimed = db.execute(
        update(CaseAttachment)
        .where(CaseAttachment.id == attachment_id, CaseAttachment.scan_status == ScanStatus.QUARANTINED)
        .values(scan_status=ScanStatus.SCANNING, scan_attempts=CaseAttachment.scan_attempts + 1, scan_started_at=func.now())
        .returning(CaseAttachment.id)
    ).first()
    db.commit()
    return claimed is not None


def _case_of(db: Session, attachment: CaseAttachment) -> Case | None:
    if attachment.case_id is None:
        return None
    return db.get(Case, attachment.case_id)


def process_scan(
    db: Session, storage: ObjectStorage, scanner: MalwareScanner, attachment_id: uuid.UUID, *, data: bytes | None = None
) -> ScanStatus:
    """Claims a QUARANTINED attachment, scans it, and records the outcome. Safe to call from several workers."""
    if not _claim(db, attachment_id):
        current = db.get(CaseAttachment, attachment_id, populate_existing=True)
        return ScanStatus(current.scan_status) if current else ScanStatus.SCAN_FAILED
    attachment = db.get(CaseAttachment, attachment_id, populate_existing=True)
    case = _case_of(db, attachment)

    if scanner.mode == "disabled":
        return _promote(db, storage, attachment, case, ScanStatus.SKIPPED_DEV)

    try:
        payload = data if data is not None else storage.get(attachment.storage_key)
        verdict = scanner.scan(payload)
    except ScannerUnavailable as exc:
        return _defer(db, attachment, case, f"ScannerUnavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 - e.g. storage read failure: retried, never approved
        return _defer(db, attachment, case, type(exc).__name__)

    if verdict.outcome is ScanOutcome.CLEAN:
        return _promote(db, storage, attachment, case, ScanStatus.CLEAN)
    if verdict.outcome is ScanOutcome.INFECTED:
        storage.delete(attachment.storage_key)
        attachment.scan_status = ScanStatus.REJECTED
        attachment.scan_signature = verdict.signature
        attachment.scanned_at = utcnow()
        attachment.object_purged_at = utcnow()
        attachment.next_scan_at = None
        record_audit(
            db, action=AuditAction.ATTACHMENT_REJECTED, entity_type="case_attachment", entity_id=attachment.id,
            actor_user_id=None, case=case, after={"signature": verdict.signature, "object_deleted": True},
        )
        db.commit()
        SCAN_OUTCOMES.labels("infected").inc()
        logger.warning("Upload rejected by malware scanner", extra={"attachment_id": str(attachment.id)})
        return ScanStatus.REJECTED
    return _fail(db, attachment, case, verdict.detail or "scanner error")


def _promote(
    db: Session, storage: ObjectStorage, attachment: CaseAttachment, case: Case | None, status: ScanStatus
) -> ScanStatus:
    source = attachment.storage_key
    if attachment.case_id is not None:
        dest = final_key(attachment.case_id, attachment.id)
    else:
        dest = sighting_final_key(attachment.sighting_id, attachment.id)
    try:
        storage.copy(source, dest)
    except Exception as exc:  # noqa: BLE001
        return _defer(db, attachment, case, type(exc).__name__)
    attachment.storage_key = dest
    attachment.scan_status = status
    attachment.scanned_at = utcnow()
    attachment.next_scan_at = None
    attachment.last_scan_error = None
    record_audit(
        db, action=AuditAction.ATTACHMENT_SCAN_CLEAN, entity_type="case_attachment", entity_id=attachment.id,
        actor_user_id=None, case=case, after={"scan_status": status, "sighting_id": attachment.sighting_id},
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(dest)
        raise
    storage.delete(source)
    SCAN_OUTCOMES.labels("clean" if status is ScanStatus.CLEAN else "skipped_dev").inc()
    # Sighting-only photos are never face-matched.
    if attachment.media_type == MediaType.PHOTO and attachment.case_id is not None:
        from app.services.faces import run_after_photo

        run_after_photo(attachment.id)
    return status


def _defer(db: Session, attachment: CaseAttachment, case: Case | None, error: str) -> ScanStatus:
    settings = get_settings()
    if attachment.scan_attempts >= settings.scan_max_attempts:
        return _fail(db, attachment, case, f"scanner unavailable after {attachment.scan_attempts} attempts ({error})")
    delay = settings.scan_retry_base_seconds * 2 ** max(0, attachment.scan_attempts - 1)
    attachment.scan_status = ScanStatus.QUARANTINED
    attachment.next_scan_at = utcnow() + timedelta(seconds=delay)
    attachment.last_scan_error = error[:200]
    db.commit()
    SCAN_OUTCOMES.labels("deferred").inc()
    logger.warning("Malware scan deferred", extra={"attachment_id": str(attachment.id), "error": error, "retry_in_s": delay})
    return ScanStatus.QUARANTINED


def _fail(db: Session, attachment: CaseAttachment, case: Case | None, detail: str) -> ScanStatus:
    attachment.scan_status = ScanStatus.SCAN_FAILED
    attachment.next_scan_at = None
    attachment.last_scan_error = detail[:200]
    record_audit(
        db, action=AuditAction.ATTACHMENT_SCAN_FAILED, entity_type="case_attachment", entity_id=attachment.id,
        actor_user_id=None, case=case, after={"detail": detail[:200], "attempts": attachment.scan_attempts},
    )
    db.commit()
    SCAN_OUTCOMES.labels("failed").inc()
    logger.error("Malware scan failed permanently", extra={"attachment_id": str(attachment.id)})
    return ScanStatus.SCAN_FAILED


def release_stale_scans(db: Session) -> int:
    """Rows stuck in SCANNING (a worker died mid-scan) go back to the queue."""
    cutoff = utcnow() - timedelta(seconds=get_settings().scan_stale_after_seconds)
    count = db.execute(
        update(CaseAttachment)
        .where(CaseAttachment.scan_status == ScanStatus.SCANNING, CaseAttachment.scan_started_at < cutoff)
        .values(scan_status=ScanStatus.QUARANTINED, next_scan_at=func.now())
    ).rowcount
    db.commit()
    return count


def due_scans(db: Session, limit: int) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(CaseAttachment.id)
            .where(CaseAttachment.scan_status == ScanStatus.QUARANTINED, CaseAttachment.next_scan_at <= func.now())
            .order_by(CaseAttachment.next_scan_at)
            .limit(limit)
        )
    )


def list_attachments(
    db: Session, actor: User, case_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[CaseAttachment], int]:
    require_permission(actor, Permission.CASE_READ, allow_public=True)
    load_case(db, actor, case_id)
    conditions = [CaseAttachment.case_id == case_id, CaseAttachment.deleted_at.is_(None)]
    total = db.scalar(select(func.count(CaseAttachment.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(CaseAttachment)
        .where(*conditions)
        .order_by(CaseAttachment.created_at, CaseAttachment.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


def _get_attachment(db: Session, case_id: uuid.UUID, attachment_id: uuid.UUID, *, for_update: bool = False) -> CaseAttachment:
    stmt = select(CaseAttachment).where(
        CaseAttachment.id == attachment_id,
        CaseAttachment.case_id == case_id,
        CaseAttachment.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    attachment = db.scalar(stmt)
    if attachment is None:
        raise NotFound("Attachment not found", code="attachment_not_found")
    return attachment


def download_url(
    db: Session, media, actor: User, case_id: uuid.UUID, attachment_id: uuid.UUID
) -> tuple[str, int, str]:
    require_permission(actor, Permission.ATTACHMENT_DOWNLOAD)
    case = load_case(db, actor, case_id)
    attachment = _get_attachment(db, case.id, attachment_id)
    if not is_downloadable(attachment):
        raise Conflict(
            "This file is not available: it has not passed malware scanning",
            code="attachment_not_available", details={"scan_status": attachment.scan_status},
        )
    ttl = media.ttl_seconds
    url = media.url_for(attachment, actor)
    record_audit(
        db, action=AuditAction.ATTACHMENT_ACCESSED, entity_type="case_attachment", entity_id=attachment.id,
        actor_user_id=actor.id, case=case,
    )
    db.commit()
    return url, ttl, attachment.content_type


def delete_attachment(db: Session, actor: User, case_id: uuid.UUID, attachment_id: uuid.UUID, reason: str) -> None:
    """Soft delete. The object stays in the private bucket until the retention job removes it."""
    require_permission(actor, Permission.ATTACHMENT_DELETE)
    case = load_case(db, actor, case_id)
    attachment = _get_attachment(db, case.id, attachment_id, for_update=True)
    attachment.deleted_at = utcnow()
    attachment.deleted_by_user_id = actor.id
    from app.services.faces import delete_embedding_for_attachment

    delete_embedding_for_attachment(db, attachment, actor=actor, case=case)
    record_audit(
        db, action=AuditAction.ATTACHMENT_DELETED, entity_type="case_attachment", entity_id=attachment.id,
        actor_user_id=actor.id, case=case, reason=reason,
    )
    db.commit()


def reconcile_objects(db: Session, storage: ObjectStorage, *, limit: int = 500) -> dict[str, int | bool]:
    """Report database rows whose private object is missing. Does not delete rows or objects."""
    keys = list(
        db.scalars(
            select(CaseAttachment.storage_key).where(CaseAttachment.object_purged_at.is_(None)).limit(limit)
        )
    )
    missing = sum(1 for key in keys if not storage.exists(key))
    return {"checked": len(keys), "missing_objects": missing, "applied": False}
