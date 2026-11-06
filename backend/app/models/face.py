import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import CaseType, FaceStatus, enum_type
from app.models.mixins import CreatedAtMixin, uuid_pk


class FaceEmbedding(CreatedAtMixin, Base):
    """Encrypted facial embedding for one photograph. Vectors never leave the service layer."""

    __tablename__ = "face_embeddings"
    __table_args__ = (
        CheckConstraint(
            "(status = 'FACE_FOUND') = (embedding_ciphertext IS NOT NULL)",
            name="face_found_requires_embedding",
        ),
        CheckConstraint("face_count >= 0", name="face_count_non_negative"),
        CheckConstraint(
            "detection_score IS NULL OR (detection_score >= 0 AND detection_score <= 1)",
            name="detection_score_range",
        ),
        Index("ix_face_embeddings_case_type_status", "case_type", "status"),
        Index("ix_face_embeddings_case_id", "case_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_attachments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False
    )
    case_type: Mapped[CaseType] = mapped_column(enum_type(CaseType, "face_case_type"), nullable=False)
    status: Mapped[FaceStatus] = mapped_column(enum_type(FaceStatus, "face_status"), nullable=False)
    face_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    detection_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_ciphertext: Mapped[str | None] = mapped_column(Text)
