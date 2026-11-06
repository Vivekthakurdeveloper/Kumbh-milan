import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import (
    CaseStatus,
    CaseType,
    ConfidenceBand,
    MatchDomain,
    MatchSource,
    MatchStatus,
    MediaType,
    NoteType,
    ScanStatus,
    enum_type,
)
from app.models.mixins import CreatedAtMixin, TimestampMixin, uuid_pk


def _case_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False
    )


def _user_fk(nullable: bool = False) -> Mapped:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable
    )


class CaseAssignment(Base):
    """Assignment history. Reassignment closes the active row and inserts a new one."""

    __tablename__ = "case_assignments"
    __table_args__ = (
        CheckConstraint("is_active = (unassigned_at IS NULL)", name="active_matches_unassigned_at"),
        Index(
            "uq_case_assignments_one_active_per_case",
            "case_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_case_assignments_case_assigned_at", "case_id", "assigned_at"),
        Index("ix_case_assignments_assignee_active", "assigned_to_user_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = _case_fk()
    assigned_to_user_id: Mapped[uuid.UUID] = _user_fk()
    assigned_by_user_id: Mapped[uuid.UUID] = _user_fk()
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class CaseNote(CreatedAtMixin, Base):
    """Append-only (enforced by a trigger). Corrections reference the note they correct in the same case."""

    __tablename__ = "case_notes"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_case_notes_id_case_id"),
        ForeignKeyConstraint(
            ["corrects_note_id", "case_id"],
            ["case_notes.id", "case_notes.case_id"],
            ondelete="RESTRICT",
            name="fk_case_notes_correction_same_case",
        ),
        CheckConstraint(
            "(note_type = 'CORRECTION') = (corrects_note_id IS NOT NULL)",
            name="correction_requires_target",
        ),
        CheckConstraint("length(btrim(note_text)) > 0", name="note_text_not_blank"),
        CheckConstraint(
            "client_record_id IS NULL OR device_id IS NOT NULL", name="ck_case_notes_client_record_requires_device"
        ),
        Index("ix_case_notes_case_created", "case_id", "created_at"),
        Index(
            "uq_case_notes_author_device_client_record",
            "author_user_id",
            "device_id",
            "client_record_id",
            unique=True,
            postgresql_where=text("client_record_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = _case_fk()
    author_user_id: Mapped[uuid.UUID] = _user_fk()
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    note_type: Mapped[NoteType] = mapped_column(enum_type(NoteType, "note_type"), nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    corrects_note_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    client_id: Mapped[str | None] = mapped_column(String(128))
    device_id: Mapped[str | None] = mapped_column(String(128))
    client_record_id: Mapped[str | None] = mapped_column(String(128))
    client_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseAttachment(CreatedAtMixin, Base):
    """Metadata only; the object lives in the private bucket under storage_key."""

    __tablename__ = "case_attachments"
    __table_args__ = (
        CheckConstraint("file_size_bytes > 0", name="file_size_positive"),
        CheckConstraint("checksum_sha256 ~ '^[0-9a-f]{64}$'", name="checksum_format"),
        CheckConstraint("scan_attempts >= 0", name="scan_attempts_non_negative"),
        CheckConstraint(
            "client_record_id IS NULL OR device_id IS NOT NULL",
            name="ck_case_attachments_client_record_requires_device",
        ),
        Index("ix_case_attachments_case_created", "case_id", "created_at"),
        Index(
            "ix_case_attachments_scan_queue", "next_scan_at",
            postgresql_where=text("scan_status IN ('QUARANTINED', 'SCANNING')"),
        ),
        Index(
            "uq_case_attachments_uploader_device_client_record",
            "uploaded_by_user_id",
            "device_id",
            "client_record_id",
            unique=True,
            postgresql_where=text("client_record_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = _case_fk()
    uploaded_by_user_id: Mapped[uuid.UUID] = _user_fk()
    media_type: Mapped[MediaType] = mapped_column(enum_type(MediaType, "media_type"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_status: Mapped[ScanStatus] = mapped_column(enum_type(ScanStatus, "scan_status"), nullable=False)
    scan_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_signature: Mapped[str | None] = mapped_column(String(200))
    last_scan_error: Mapped[str | None] = mapped_column(String(200))
    object_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_user_id: Mapped[uuid.UUID | None] = _user_fk(nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(128))
    device_id: Mapped[str | None] = mapped_column(String(128))
    client_record_id: Mapped[str | None] = mapped_column(String(128))
    client_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseStatusHistory(CreatedAtMixin, Base):
    """Append-only (enforced by a trigger)."""

    __tablename__ = "case_status_history"
    __table_args__ = (Index("ix_case_status_history_case_created", "case_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = _case_fk()
    from_status: Mapped[CaseStatus | None] = mapped_column(enum_type(CaseStatus, "from_status"))
    to_status: Mapped[CaseStatus] = mapped_column(enum_type(CaseStatus, "to_status"), nullable=False)
    changed_by_user_id: Mapped[uuid.UUID] = _user_fk()
    reason: Mapped[str | None] = mapped_column(Text)
    verification_evidence: Mapped[str | None] = mapped_column(Text)
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_matches.id", ondelete="RESTRICT")
    )


class CaseMatch(TimestampMixin, Base):
    """Reviewable association between a missing/lost case (left) and a found case (right)."""

    __tablename__ = "case_matches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["left_case_id", "left_case_type"],
            ["cases.id", "cases.case_type"],
            ondelete="RESTRICT",
            name="fk_case_matches_left_case",
        ),
        ForeignKeyConstraint(
            ["right_case_id", "right_case_type"],
            ["cases.id", "cases.case_type"],
            ondelete="RESTRICT",
            name="fk_case_matches_right_case",
        ),
        UniqueConstraint("left_case_id", "right_case_id", name="uq_case_matches_pair"),
        CheckConstraint(
            "(match_domain = 'PERSON' AND left_case_type = 'MISSING_PERSON' AND right_case_type = 'FOUND_PERSON')"
            " OR (match_domain = 'ITEM' AND left_case_type = 'LOST_ITEM' AND right_case_type = 'FOUND_ITEM')",
            name="domain_matches_case_types",
        ),
        CheckConstraint(
            "(status = 'PROPOSED') = (reviewed_by_user_id IS NULL AND reviewed_at IS NULL)",
            name="review_fields_match_status",
        ),
        CheckConstraint(
            "status <> 'CONFIRMED' OR length(btrim(coalesce(review_evidence, ''))) > 0",
            name="confirmation_requires_evidence",
        ),
        CheckConstraint(
            "(source = 'MANUAL') = (proposed_by_user_id IS NOT NULL)", name="manual_matches_have_proposer"
        ),
        CheckConstraint(
            "source <> 'AUTOMATIC' OR (score IS NOT NULL AND confidence_band IS NOT NULL AND engine_version IS NOT NULL)",
            name="automatic_matches_are_scored",
        ),
        CheckConstraint(
            "source <> 'FACE' OR ("
            "face_similarity IS NOT NULL AND left_attachment_id IS NOT NULL AND right_attachment_id IS NOT NULL "
            "AND face_model IS NOT NULL AND score IS NOT NULL)",
            name="face_matches_are_scored",
        ),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
        CheckConstraint(
            "face_similarity IS NULL OR (face_similarity >= 0 AND face_similarity <= 1)",
            name="face_similarity_range",
        ),
        CheckConstraint(
            "(status = 'REVOKED') = (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)",
            name="revocation_fields_match_status",
        ),
        CheckConstraint(
            "first_approved_by_user_id IS NULL OR first_approved_by_user_id IS DISTINCT FROM reviewed_by_user_id",
            name="face_two_person_approval",
        ),
        CheckConstraint(
            "(first_approved_by_user_id IS NULL) = (first_approved_at IS NULL)",
            name="first_approval_fields_together",
        ),
        Index("ix_case_matches_right_case", "right_case_id"),
        Index("ix_case_matches_status_created", "status", "created_at"),
        # A case can be confirmed against at most one counterpart at a time.
        Index("uq_case_matches_confirmed_left", "left_case_id", unique=True, postgresql_where=text("status = 'CONFIRMED'")),
        Index("uq_case_matches_confirmed_right", "right_case_id", unique=True, postgresql_where=text("status = 'CONFIRMED'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    match_domain: Mapped[MatchDomain] = mapped_column(enum_type(MatchDomain, "match_domain"), nullable=False)
    left_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    left_case_type: Mapped[CaseType] = mapped_column(enum_type(CaseType, "left_case_type"), nullable=False)
    right_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    right_case_type: Mapped[CaseType] = mapped_column(enum_type(CaseType, "right_case_type"), nullable=False)
    status: Mapped[MatchStatus] = mapped_column(
        enum_type(MatchStatus, "match_status"), nullable=False, server_default=MatchStatus.PROPOSED.value
    )
    source: Mapped[MatchSource] = mapped_column(
        enum_type(MatchSource, "match_source"), nullable=False, server_default=MatchSource.MANUAL.value
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    confidence_band: Mapped[ConfidenceBand | None] = mapped_column(enum_type(ConfidenceBand, "confidence_band"))
    reasons: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    engine_version: Mapped[str | None] = mapped_column(String(32))
    left_attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_attachments.id", ondelete="RESTRICT")
    )
    right_attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_attachments.id", ondelete="RESTRICT")
    )
    face_similarity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    face_model: Mapped[str | None] = mapped_column(String(64))
    proposed_by_user_id: Mapped[uuid.UUID | None] = _user_fk(nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text)
    first_approved_by_user_id: Mapped[uuid.UUID | None] = _user_fk(nullable=True)
    first_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_approval_evidence: Mapped[str | None] = mapped_column(Text)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = _user_fk(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_evidence: Mapped[str | None] = mapped_column(Text)
    revoked_by_user_id: Mapped[uuid.UUID | None] = _user_fk(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)


class CaseContact(TimestampMixin, Base):
    """Owner/family contact details, encrypted at the application layer. Never included in list or search."""

    __tablename__ = "case_contacts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), primary_key=True
    )
    contact_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID] = _user_fk()
