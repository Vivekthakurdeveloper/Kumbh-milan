import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import (
    HandoverKind,
    HandoverStatus,
    IdentityDocumentType,
    IdentityVerificationMethod,
    enum_type,
)
from app.models.mixins import TimestampMixin, uuid_pk


class CaseHandover(TimestampMixin, Base):
    """Structured claimant verification and physical handover state.

    Sensitive claimant, witness, verification, acknowledgement, and rejection
    values are encrypted JSON/text. Lifecycle changes are also written to the
    append-only audit log.
    """

    __tablename__ = "case_handovers"
    __table_args__ = (
        CheckConstraint(
            "counterpart_case_id IS NULL OR counterpart_case_id <> primary_case_id",
            name="different_counterpart",
        ),
        CheckConstraint(
            "kind <> 'PERSON_REUNIFICATION' OR match_id IS NOT NULL",
            name="person_reunification_requires_match",
        ),
        CheckConstraint(
            "claimant_ciphertext IS NOT NULL OR anonymized_at IS NOT NULL",
            name="claimant_or_anonymized",
        ),
        CheckConstraint(
            "status NOT IN ('VERIFIED', 'COMPLETED') "
            "OR anonymized_at IS NOT NULL "
            "OR (verified_by_user_id IS NOT NULL AND verified_at IS NOT NULL "
            "AND verification_method IS NOT NULL AND verification_ciphertext IS NOT NULL)",
            name="verified_fields_present",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' "
            "OR anonymized_at IS NOT NULL "
            "OR (completed_by_user_id IS NOT NULL AND completed_at IS NOT NULL "
            "AND handover_at IS NOT NULL AND handover_location IS NOT NULL "
            "AND acknowledgement_ciphertext IS NOT NULL)",
            name="completed_fields_present",
        ),
        CheckConstraint(
            "status <> 'REJECTED' "
            "OR anonymized_at IS NOT NULL "
            "OR (rejected_by_user_id IS NOT NULL AND rejected_at IS NOT NULL "
            "AND rejection_ciphertext IS NOT NULL)",
            name="rejected_fields_present",
        ),
        Index("ix_case_handovers_primary_created", "primary_case_id", "created_at", "id"),
        Index("ix_case_handovers_counterpart_created", "counterpart_case_id", "created_at", "id"),
        Index("ix_case_handovers_match", "match_id"),
        Index(
            "uq_case_handovers_active_primary",
            "primary_case_id",
            unique=True,
            postgresql_where=text("status IN ('CLAIMANT_RECORDED', 'VERIFIED')"),
        ),
        Index(
            "uq_case_handovers_active_match",
            "match_id",
            unique=True,
            postgresql_where=text(
                "status IN ('CLAIMANT_RECORDED', 'VERIFIED') AND match_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_case_handovers_completed_match",
            "match_id",
            unique=True,
            postgresql_where=text("status = 'COMPLETED' AND match_id IS NOT NULL"),
        ),
        Index(
            "uq_case_handovers_completed_primary_without_match",
            "primary_case_id",
            unique=True,
            postgresql_where=text("status = 'COMPLETED' AND match_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    primary_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False
    )
    counterpart_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT")
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_matches.id", ondelete="RESTRICT")
    )
    kind: Mapped[HandoverKind] = mapped_column(
        enum_type(HandoverKind, "handover_kind"), nullable=False
    )
    status: Mapped[HandoverStatus] = mapped_column(
        enum_type(HandoverStatus, "handover_status"),
        nullable=False,
        default=HandoverStatus.CLAIMANT_RECORDED,
        server_default=HandoverStatus.CLAIMANT_RECORDED.value,
    )
    claimant_ciphertext: Mapped[str | None] = mapped_column(Text)
    identity_document_type: Mapped[IdentityDocumentType | None] = mapped_column(
        enum_type(IdentityDocumentType, "handover_identity_document_type")
    )
    witness_ciphertext: Mapped[str | None] = mapped_column(Text)
    evidence_attachment_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    verification_method: Mapped[IdentityVerificationMethod | None] = mapped_column(
        enum_type(IdentityVerificationMethod, "handover_verification_method")
    )
    verification_ciphertext: Mapped[str | None] = mapped_column(Text)
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handover_location: Mapped[str | None] = mapped_column(String(500))
    handover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledgement_ciphertext: Mapped[str | None] = mapped_column(Text)
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_ciphertext: Mapped[str | None] = mapped_column(Text)
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}
