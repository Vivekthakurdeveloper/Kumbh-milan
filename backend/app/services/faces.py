"""
Facial recognition for person-case photographs.

Suggestions are PROPOSED FACE matches that police must review. Nothing here confirms identity,
changes a case to RESOLVED, or exposes embeddings. A match is never automatic beyond the suggestion.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.common.time import utcnow
from app.config import get_settings
from app.faces import get_face_engine
from app.faces.crypto import decrypt_embedding, encrypt_embedding
from app.faces.engine import FaceStatus as EngineFaceStatus
from app.models import Case, CaseAttachment, CaseMatch, FaceEmbedding, StaffNotification, User
from app.models.enums import (
    CaseType,
    ConfidenceBand,
    FaceStatus,
    MatchDomain,
    MatchSource,
    MatchStatus,
    MediaType,
    Role,
    StaffNotificationKind,
    StaffNotificationStatus,
)
from app.observability.metrics import FACE_ANALYSES, FACE_CANDIDATES, FACE_FAILURES
from app.services.attachments import is_downloadable
from app.services.workflow import MATCH_CONFIRMATION_SOURCES
from app.storage import ObjectStorage

logger = logging.getLogger("kumbh_setu.faces")

COUNTERPART = {
    CaseType.MISSING_PERSON: CaseType.FOUND_PERSON,
    CaseType.FOUND_PERSON: CaseType.MISSING_PERSON,
}
LEFT_TYPES = (CaseType.MISSING_PERSON,)
PERSON_TYPES = (CaseType.MISSING_PERSON, CaseType.FOUND_PERSON)


def run_after_photo(attachment_id: uuid.UUID) -> None:
    """Best effort in its own session: a face-pipeline failure never fails the upload."""
    if not get_settings().face_matching_enabled:
        return
    from app.database import SessionLocal
    from app.storage import get_storage

    try:
        with SessionLocal() as db:
            process_photo(db, get_storage(), attachment_id)
    except Exception:  # noqa: BLE001
        FACE_FAILURES.inc()
        logger.exception("Face analysis failed", extra={"attachment_id": str(attachment_id)})


def process_photo(db: Session, storage: ObjectStorage, attachment_id: uuid.UUID) -> FaceEmbedding | None:
    """Idempotent: analyse one clean photo, store status/embedding, then compare if usable."""
    if not get_settings().face_matching_enabled:
        return None
    existing = db.scalar(select(FaceEmbedding).where(FaceEmbedding.attachment_id == attachment_id))
    if existing is not None:
        if existing.status == FaceStatus.FACE_FOUND and existing.embedding_ciphertext:
            compare_for_embedding(db, existing)
        return existing

    attachment = db.get(CaseAttachment, attachment_id)
    if attachment is None or attachment.deleted_at is not None:
        return None
    if attachment.media_type != MediaType.PHOTO or not is_downloadable(attachment):
        return None
    case = db.get(Case, attachment.case_id)
    if case is None or CaseType(case.case_type) not in PERSON_TYPES:
        return None
    if case.anonymized_at is not None:
        return None

    engine = get_face_engine()
    try:
        analysis = engine.analyze(storage.get(attachment.storage_key))
    except Exception:  # noqa: BLE001
        FACE_FAILURES.inc()
        logger.exception("Face engine failed", extra={"attachment_id": str(attachment_id)})
        analysis_status = FaceStatus.UNREADABLE
        row = FaceEmbedding(
            id=uuid.uuid4(),
            attachment_id=attachment.id,
            case_id=case.id,
            case_type=case.case_type,
            status=analysis_status,
            face_count=0,
            detection_score=None,
            model_name=engine.name,
            embedding_ciphertext=None,
        )
        db.add(row)
        record_audit(
            db,
            action=AuditAction.FACE_ANALYZED,
            entity_type="face_embedding",
            entity_id=row.id,
            actor_user_id=None,
            case=case,
            after={"status": analysis_status, "face_count": 0, "model": engine.name, "attachment_id": attachment.id},
        )
        db.commit()
        FACE_ANALYSES.labels(analysis_status.value).inc()
        return row

    status = FaceStatus(analysis.status.value)
    ciphertext = None
    if analysis.status is EngineFaceStatus.FACE_FOUND and analysis.embedding is not None:
        ciphertext = encrypt_embedding(analysis.embedding)

    row = FaceEmbedding(
        id=uuid.uuid4(),
        attachment_id=attachment.id,
        case_id=case.id,
        case_type=case.case_type,
        status=status,
        face_count=analysis.face_count,
        detection_score=(
            Decimal(str(round(analysis.detection_score, 4))) if analysis.detection_score is not None else None
        ),
        model_name=engine.name,
        embedding_ciphertext=ciphertext,
    )
    db.add(row)
    record_audit(
        db,
        action=AuditAction.FACE_ANALYZED,
        entity_type="face_embedding",
        entity_id=row.id,
        actor_user_id=None,
        case=case,
        after={
            "status": status,
            "face_count": analysis.face_count,
            "model": engine.name,
            "attachment_id": attachment.id,
            "detection_score": float(analysis.detection_score) if analysis.detection_score is not None else None,
        },
    )
    db.commit()
    FACE_ANALYSES.labels(status.value).inc()

    if row.status == FaceStatus.FACE_FOUND:
        compare_for_embedding(db, row)
    return row


def compare_for_embedding(
    db: Session, embedding_row: FaceEmbedding, *, actor: User | None = None
) -> list[uuid.UUID]:
    """Compare one FACE_FOUND embedding against eligible opposite-type cases. Returns new match ids."""
    settings = get_settings()
    if not settings.face_matching_enabled:
        return []
    if embedding_row.status != FaceStatus.FACE_FOUND or not embedding_row.embedding_ciphertext:
        return []

    case = db.get(Case, embedding_row.case_id)
    if case is None or case.status not in MATCH_CONFIRMATION_SOURCES:
        return []
    if case.archived_at is not None or case.anonymized_at is not None:
        return []

    target = COUNTERPART.get(CaseType(case.case_type))
    if target is None:
        return []

    engine = get_face_engine()
    source_vector = decrypt_embedding(embedding_row.embedding_ciphertext)
    window = timedelta(days=settings.face_candidate_window_days)

    candidates = list(
        db.scalars(
            select(FaceEmbedding)
            .join(Case, Case.id == FaceEmbedding.case_id)
            .where(
                FaceEmbedding.case_type == target,
                FaceEmbedding.status == FaceStatus.FACE_FOUND,
                FaceEmbedding.embedding_ciphertext.is_not(None),
                Case.status.in_(MATCH_CONFIRMATION_SOURCES),
                Case.archived_at.is_(None),
                Case.anonymized_at.is_(None),
                Case.created_at.between(case.created_at - window, case.created_at + window),
            )
        )
    )

    record_audit(
        db,
        action=AuditAction.FACE_COMPARISON_INITIATED,
        entity_type="face_embedding",
        entity_id=embedding_row.id,
        actor_user_id=actor.id if actor else None,
        case=case,
        after={"candidate_embeddings": len(candidates), "model": engine.name, "threshold": settings.face_similarity_threshold},
    )

    # Best photo pair per counterpart case.
    best_by_case: dict[uuid.UUID, tuple[float, FaceEmbedding]] = {}
    for other in candidates:
        if other.case_id == case.id:
            continue
        score = engine.similarity(source_vector, decrypt_embedding(other.embedding_ciphertext))
        previous = best_by_case.get(other.case_id)
        if previous is None or score > previous[0]:
            best_by_case[other.case_id] = (score, other)

    threshold = settings.face_similarity_threshold
    ranked = sorted(
        ((score, other) for score, other in best_by_case.values() if score >= threshold),
        key=lambda item: (-item[0], str(item[1].case_id)),
    )[: settings.face_max_candidates]

    if not ranked:
        record_audit(
            db,
            action=AuditAction.FACE_COMPARISON_COMPLETED,
            entity_type="face_embedding",
            entity_id=embedding_row.id,
            actor_user_id=actor.id if actor else None,
            case=case,
            after={"candidates_found": 0, "threshold": threshold},
        )
        db.commit()
        return []

    created: list[uuid.UUID] = []
    for score, other_embedding in ranked:
        other_case = db.get(Case, other_embedding.case_id)
        if other_case is None:
            continue
        source_is_left = CaseType(case.case_type) in LEFT_TYPES
        left, right = (case, other_case) if source_is_left else (other_case, case)
        left_att, right_att = (
            (embedding_row.attachment_id, other_embedding.attachment_id)
            if source_is_left
            else (other_embedding.attachment_id, embedding_row.attachment_id)
        )
        match_id = _upsert_face_candidate(
            db,
            left=left,
            right=right,
            left_attachment_id=left_att,
            right_attachment_id=right_att,
            similarity=score,
            model_name=engine.name,
            actor=actor,
        )
        if match_id is not None:
            created.append(match_id)
            FACE_CANDIDATES.inc()

    record_audit(
        db,
        action=AuditAction.FACE_COMPARISON_COMPLETED,
        entity_type="face_embedding",
        entity_id=embedding_row.id,
        actor_user_id=actor.id if actor else None,
        case=case,
        after={"candidates_found": len(created), "threshold": threshold},
    )
    db.commit()
    return created


def _upsert_face_candidate(
    db: Session,
    *,
    left: Case,
    right: Case,
    left_attachment_id: uuid.UUID,
    right_attachment_id: uuid.UUID,
    similarity: float,
    model_name: str,
    actor: User | None,
) -> uuid.UUID | None:
    """Insert a FACE candidate, or enrich an existing PROPOSED automatic/manual pair. Never duplicates."""
    existing = db.scalar(
        select(CaseMatch)
        .where(CaseMatch.left_case_id == left.id, CaseMatch.right_case_id == right.id)
        .with_for_update()
    )
    rounded = Decimal(str(round(similarity, 4)))
    band = _band(float(rounded))

    if existing is not None:
        if existing.status != MatchStatus.PROPOSED:
            return None
        # Enrich an open suggestion with face evidence; do not invent a second row.
        if existing.face_similarity is None or rounded > existing.face_similarity:
            existing.face_similarity = rounded
            existing.face_model = model_name
            existing.left_attachment_id = left_attachment_id
            existing.right_attachment_id = right_attachment_id
            if existing.source == MatchSource.AUTOMATIC or existing.score is None or rounded > existing.score:
                existing.score = rounded
                existing.confidence_band = band
            if existing.source == MatchSource.AUTOMATIC:
                existing.source = MatchSource.FACE
                existing.engine_version = model_name
            record_audit(
                db,
                action=AuditAction.FACE_MATCH_CANDIDATE_GENERATED,
                entity_type="case_match",
                entity_id=existing.id,
                actor_user_id=actor.id if actor else None,
                case=left,
                after={
                    "counterpart_case_number": right.case_number,
                    "face_similarity": float(rounded),
                    "enriched_existing": True,
                    "source": existing.source,
                },
            )
            record_audit(
                db,
                action=AuditAction.FACE_MATCH_CANDIDATE_GENERATED,
                entity_type="case_match",
                entity_id=existing.id,
                actor_user_id=actor.id if actor else None,
                case=right,
                after={
                    "counterpart_case_number": left.case_number,
                    "face_similarity": float(rounded),
                    "enriched_existing": True,
                    "source": existing.source,
                },
            )
            notify_reviewers(db, existing, left, right)
        return existing.id

    match_id = uuid.uuid4()
    inserted = db.execute(
        insert(CaseMatch)
        .values(
            id=match_id,
            match_domain=MatchDomain.PERSON,
            left_case_id=left.id,
            left_case_type=left.case_type,
            right_case_id=right.id,
            right_case_type=right.case_type,
            status=MatchStatus.PROPOSED,
            source=MatchSource.FACE,
            score=rounded,
            confidence_band=band,
            reasons=[{"feature": "face", "weight": 1.0, "similarity": float(rounded),
                      "contribution": float(rounded), "detail": f"SFace cosine similarity {float(rounded):.4f}"}],
            engine_version=model_name,
            left_attachment_id=left_attachment_id,
            right_attachment_id=right_attachment_id,
            face_similarity=rounded,
            face_model=model_name,
        )
        .on_conflict_do_nothing(index_elements=["left_case_id", "right_case_id"])
        .returning(CaseMatch.id)
    ).scalar()
    if inserted is None:
        return None

    for side, counterpart in ((left, right), (right, left)):
        record_audit(
            db,
            action=AuditAction.FACE_MATCH_CANDIDATE_GENERATED,
            entity_type="case_match",
            entity_id=match_id,
            actor_user_id=actor.id if actor else None,
            case=side,
            after={
                "counterpart_case_number": counterpart.case_number,
                "face_similarity": float(rounded),
                "model": model_name,
            },
        )
    match = db.get(CaseMatch, match_id)
    notify_reviewers(db, match, left, right)
    return match_id


def _band(score: float) -> ConfidenceBand:
    if score >= 0.75:
        return ConfidenceBand.HIGH
    if score >= 0.55:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def notify_reviewers(db: Session, match: CaseMatch, left: Case, right: Case) -> int:
    """In-app notifications for active police officers at both case stations."""
    station_ids = {left.station_id, right.station_id}
    officers = list(
        db.scalars(
            select(User).where(
                User.role == Role.POLICE_OFFICER,
                User.is_active.is_(True),
                User.station_id.in_(station_ids),
            )
        )
    )
    created = 0
    for officer in officers:
        result = db.execute(
            insert(StaffNotification)
            .values(
                id=uuid.uuid4(),
                recipient_user_id=officer.id,
                kind=StaffNotificationKind.MATCH_CANDIDATE,
                match_id=match.id,
                status=StaffNotificationStatus.UNREAD,
            )
            .on_conflict_do_nothing(constraint="uq_staff_notifications_recipient_match_kind")
            .returning(StaffNotification.id)
        ).scalar()
        if result is not None:
            created += 1
    return created


def compare_case(db: Session, storage: ObjectStorage, case_id: uuid.UUID, *, actor: User | None = None) -> list[uuid.UUID]:
    """Run face comparison for every usable photo on a person case (used by /matches/generate)."""
    case = db.get(Case, case_id)
    if case is None or CaseType(case.case_type) not in PERSON_TYPES:
        return []
    before = set(
        db.scalars(
            select(CaseMatch.id).where(
                or_(CaseMatch.left_case_id == case_id, CaseMatch.right_case_id == case_id),
                CaseMatch.status == MatchStatus.PROPOSED,
            )
        )
    )
    attachments = list(
        db.scalars(
            select(CaseAttachment).where(
                CaseAttachment.case_id == case_id,
                CaseAttachment.media_type == MediaType.PHOTO,
                CaseAttachment.deleted_at.is_(None),
            )
        )
    )
    for attachment in attachments:
        row = process_photo(db, storage, attachment.id)
        if row is not None and row.status == FaceStatus.FACE_FOUND and actor is not None:
            # process_photo already compared; a second pass attributes FACE_COMPARISON_* to the actor.
            compare_for_embedding(db, row, actor=actor)
    after = set(
        db.scalars(
            select(CaseMatch.id).where(
                or_(CaseMatch.left_case_id == case_id, CaseMatch.right_case_id == case_id),
                CaseMatch.status == MatchStatus.PROPOSED,
                or_(CaseMatch.source == MatchSource.FACE, CaseMatch.face_similarity.is_not(None)),
            )
        )
    )
    return list(after - before)


def delete_embedding_for_attachment(db: Session, attachment: CaseAttachment, *, actor: User | None, case: Case) -> None:
    """Remove the embedding for a soft-deleted attachment. Caller commits."""
    row = db.scalar(select(FaceEmbedding).where(FaceEmbedding.attachment_id == attachment.id))
    if row is None:
        return
    record_audit(
        db,
        action=AuditAction.FACE_EMBEDDING_DELETED,
        entity_type="face_embedding",
        entity_id=row.id,
        actor_user_id=actor.id if actor else None,
        case=case,
        after={"attachment_id": attachment.id, "reason": "attachment_deleted"},
    )
    db.delete(row)


def delete_embeddings_for_case(db: Session, case: Case, *, reason: str) -> int:
    """Delete all embeddings for a case (anonymisation / purge). Caller commits."""
    rows = list(db.scalars(select(FaceEmbedding).where(FaceEmbedding.case_id == case.id)))
    for row in rows:
        record_audit(
            db,
            action=AuditAction.FACE_EMBEDDING_DELETED,
            entity_type="face_embedding",
            entity_id=row.id,
            actor_user_id=None,
            case=case,
            after={"attachment_id": row.attachment_id, "reason": reason},
        )
        db.delete(row)
    return len(rows)


def purge_face_embeddings(db: Session) -> int:
    """Delete embeddings for cases that left active status longer than the retention window."""
    settings = get_settings()
    cutoff = utcnow() - timedelta(days=settings.face_embedding_retention_days)
    inactive = select(Case.id).where(
        or_(
            Case.archived_at.is_not(None),
            Case.anonymized_at.is_not(None),
            Case.status.notin_(MATCH_CONFIRMATION_SOURCES),
        ),
        Case.updated_at < cutoff,
    )
    rows = list(db.scalars(select(FaceEmbedding).where(FaceEmbedding.case_id.in_(inactive))))
    for row in rows:
        case = db.get(Case, row.case_id)
        record_audit(
            db,
            action=AuditAction.FACE_EMBEDDING_DELETED,
            entity_type="face_embedding",
            entity_id=row.id,
            actor_user_id=None,
            case=case,
            after={"attachment_id": row.attachment_id, "reason": "retention_window"},
        )
        db.delete(row)
    db.commit()
    return len(rows)


def backfill(db: Session, storage: ObjectStorage, *, limit: int = 500) -> tuple[int, int]:
    """Analyse person photos that have no face_embeddings row yet. Returns (photos, new candidates)."""
    missing = exists().where(FaceEmbedding.attachment_id == CaseAttachment.id)
    ids = list(
        db.scalars(
            select(CaseAttachment.id)
            .join(Case, Case.id == CaseAttachment.case_id)
            .where(
                CaseAttachment.media_type == MediaType.PHOTO,
                CaseAttachment.deleted_at.is_(None),
                Case.case_type.in_(PERSON_TYPES),
                Case.anonymized_at.is_(None),
                ~missing,
            )
            .order_by(CaseAttachment.created_at.desc())
            .limit(limit)
        )
    )
    before = set(db.scalars(select(CaseMatch.id).where(CaseMatch.source == MatchSource.FACE)))
    for attachment_id in ids:
        process_photo(db, storage, attachment_id)
    after = set(db.scalars(select(CaseMatch.id).where(CaseMatch.source == MatchSource.FACE)))
    return len(ids), len(after - before)


def face_status_for_attachment(db: Session, attachment_id: uuid.UUID) -> FaceStatus | None:
    return db.scalar(select(FaceEmbedding.status).where(FaceEmbedding.attachment_id == attachment_id))
