"""Citizen sighting reports. Never auto-confirm matches or change case status."""

from __future__ import annotations

import hashlib
import uuid
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.auth.public_tokens import decode_public_token
from app.common.context import client_ip_var
from app.common.crypto import encrypt_text
from app.common.errors import NotFound, PayloadTooLarge, Unauthenticated, UnsupportedMedia, ValidationFailed
from app.common.security_hash import keyed_hash
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseAttachment, PublicSighting, User
from app.models.enums import MediaType, OtpPurpose, ScanStatus, SightingStatus
from app.notifications import mask, normalise_phone
from app.schemas.public import PublicSightingCreate
from app.services import attachments as attachment_service
from app.services.access import load_case
from app.services.public_cases import _intake_actor
from app.storage import ObjectStorage
from app.storage.scanner import MalwareScanner
from app.storage.validation import declared_type_conflicts, detect_media, safe_display_filename


def _require_verified_mobile(token: str, *, expected_mobile: str) -> str:
    claims = decode_public_token(token, expected_typ="verified_mobile")
    if claims.get("purpose") != OtpPurpose.REPORT_SIGHTING.value:
        raise Unauthenticated("Verified mobile token purpose mismatch", code="invalid_token")
    phone = normalise_phone(expected_mobile, get_settings().notification_default_country_code)
    if phone is None or claims.get("mobile") != phone:
        raise Unauthenticated("Verified mobile token does not match reporter number", code="invalid_token")
    return phone


def submit_sighting(
    db: Session,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    *,
    payload: PublicSightingCreate,
    verified_mobile_token: str,
    photo: BinaryIO | None = None,
    photo_filename: str | None = None,
    photo_content_type: str | None = None,
) -> PublicSighting:
    phone = _require_verified_mobile(verified_mobile_token, expected_mobile=payload.reporter_mobile_number)
    actor = _intake_actor(db)
    settings = get_settings()
    linked_case = None
    if payload.case_id:
        linked_case = db.scalar(select(Case).where(Case.case_number == payload.case_id.strip()))
    sighting = PublicSighting(
        id=uuid.uuid4(),
        case_id=linked_case.id if linked_case else None,
        case_number_submitted=payload.case_id.strip() if payload.case_id else None,
        station_id=linked_case.station_id if linked_case else settings.public_intake_station_id,
        sighting_location=payload.sighting_location,
        sighting_at=payload.sighting_at,
        description=payload.description,
        reporter_mobile_ciphertext=encrypt_text(phone),
        reporter_mobile_lookup_hash=keyed_hash(phone),
        reporter_mobile_hint=mask(phone),
        status=SightingStatus.UNVERIFIED,
        client_ip=client_ip_var.get(),
    )
    db.add(sighting)
    record_audit(
        db,
        action=AuditAction.PUBLIC_SIGHTING_REPORTED,
        entity_type="public_sighting",
        entity_id=sighting.id,
        actor_user_id=actor.id,
        case=linked_case,
        after={
            "status": SightingStatus.UNVERIFIED,
            "linked": linked_case is not None,
            "station_id": str(sighting.station_id),
        },
    )
    db.commit()
    if photo is not None:
        _upload_sighting_photo(
            db,
            storage,
            scanner,
            actor,
            sighting,
            stream=photo,
            filename=photo_filename,
            content_type=photo_content_type,
        )
    db.refresh(sighting)
    return sighting


def _upload_sighting_photo(
    db: Session,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    actor: User,
    sighting: PublicSighting,
    *,
    stream: BinaryIO,
    filename: str | None,
    content_type: str | None,
) -> CaseAttachment:
    limit = get_settings().max_photo_bytes
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge(f"Photo files are limited to {limit // (1024 * 1024)} MB")
    if not data:
        raise ValidationFailed("File is empty", code="empty_file")
    detected = detect_media(data[:64])
    if detected is None or detected.media_type is not MediaType.PHOTO:
        raise UnsupportedMedia("Unsupported or unrecognised file format", code="unsupported_media_type")
    if declared_type_conflicts(content_type, detected):
        raise UnsupportedMedia("Declared content type does not match the file content", code="content_type_mismatch")
    attachment_id = uuid.uuid4()
    key = f"quarantine/sightings/{sighting.id}/{attachment_id.hex}"
    checksum = hashlib.sha256(data).hexdigest()
    storage.put(key, data, content_type=detected.content_type, checksum_sha256=checksum)
    attachment = CaseAttachment(
        id=attachment_id,
        case_id=None,
        sighting_id=sighting.id,
        uploaded_by_user_id=actor.id,
        media_type=MediaType.PHOTO,
        storage_key=key,
        original_filename=safe_display_filename(filename, detected.extension),
        content_type=detected.content_type,
        file_size_bytes=len(data),
        checksum_sha256=checksum,
        scan_status=ScanStatus.QUARANTINED,
        next_scan_at=utcnow(),
    )
    db.add(attachment)
    record_audit(
        db,
        action=AuditAction.ATTACHMENT_UPLOADED,
        entity_type="case_attachment",
        entity_id=attachment_id,
        actor_user_id=actor.id,
        after={"sighting_id": str(sighting.id), "media_type": MediaType.PHOTO, "scan_status": ScanStatus.QUARANTINED},
    )
    db.commit()
    attachment_service.process_scan(db, storage, scanner, attachment_id, data=data)
    db.expire_all()
    return db.get(CaseAttachment, attachment_id)


def list_sightings(
    db: Session, actor: User, *, case_id: uuid.UUID | None, limit: int, offset: int
) -> tuple[list[PublicSighting], int]:
    require_permission(actor, Permission.SIGHTING_READ)
    if case_id is not None:
        load_case(db, actor, case_id)
        conditions = [PublicSighting.case_id == case_id]
    elif actor.station_id is not None:
        conditions = [PublicSighting.station_id == actor.station_id]
    else:
        conditions = []
    total = db.scalar(select(func.count(PublicSighting.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(PublicSighting)
            .where(*conditions)
            .order_by(PublicSighting.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_sighting(db: Session, actor: User, sighting_id: uuid.UUID) -> PublicSighting:
    require_permission(actor, Permission.SIGHTING_READ)
    sighting = db.get(PublicSighting, sighting_id)
    if sighting is None:
        raise NotFound("Sighting not found", code="sighting_not_found")
    if actor.station_id is not None and sighting.station_id != actor.station_id:
        raise NotFound("Sighting not found", code="sighting_not_found")
    return sighting


def sighting_has_photo(db: Session, sighting_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(CaseAttachment.id).where(
                CaseAttachment.sighting_id == sighting_id, CaseAttachment.deleted_at.is_(None)
            )
        )
        is not None
    )


def sighting_photo_url(db: Session, storage: ObjectStorage, actor: User, sighting_id: uuid.UUID):
    sighting = get_sighting(db, actor, sighting_id)
    attachment = db.scalar(
        select(CaseAttachment).where(
            CaseAttachment.sighting_id == sighting.id, CaseAttachment.deleted_at.is_(None)
        )
    )
    if attachment is None or not attachment_service.is_downloadable(attachment):
        raise NotFound("Sighting photo not available", code="attachment_not_found")
    require_permission(actor, Permission.ATTACHMENT_DOWNLOAD)
    ttl = get_settings().s3_presign_ttl_seconds
    url = storage.presign_get(
        attachment.storage_key,
        content_type=attachment.content_type,
        filename=attachment.original_filename or "sighting.jpg",
        ttl_seconds=ttl,
    )
    record_audit(
        db,
        action=AuditAction.ATTACHMENT_ACCESSED,
        entity_type="case_attachment",
        entity_id=attachment.id,
        actor_user_id=actor.id,
        after={"sighting_id": str(sighting.id)},
    )
    db.commit()
    return url, ttl, attachment.content_type
