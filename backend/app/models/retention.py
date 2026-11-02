import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import RetentionPolicy, RetentionRunStatus, enum_type
from app.models.mixins import TimestampMixin, uuid_pk

REDACTION_PREFIX = "[redacted by retention run "


def _user(nullable: bool = True) -> Mapped[uuid.UUID | None]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable)


class LegalHold(TimestampMixin, Base):
    """While a hold is active the case is excluded from archiving and anonymisation."""

    __tablename__ = "legal_holds"
    __table_args__ = (
        CheckConstraint(
            "(released_at IS NULL) = (released_by_user_id IS NULL) AND (released_at IS NULL) = (release_reason IS NULL)",
            name="release_fields_together",
        ),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        Index("ix_legal_holds_active_case", "case_id", postgresql_where=text("released_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(200))
    placed_by_user_id: Mapped[uuid.UUID] = _user(nullable=False)
    released_by_user_id: Mapped[uuid.UUID | None] = _user()
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)


class RetentionRun(TimestampMixin, Base):
    """
    PLANNED (dry-run report) -> APPROVED (by a different administrator) -> EXECUTING -> EXECUTED | FAILED.
    PLANNED or APPROVED runs can be CANCELLED. Eligibility is re-checked item by item at execution.
    """

    __tablename__ = "retention_runs"
    __table_args__ = (
        CheckConstraint("approved_by_user_id IS NULL OR approved_by_user_id <> planned_by_user_id", name="two_person_approval"),
        CheckConstraint(
            "status NOT IN ('APPROVED', 'EXECUTING', 'EXECUTED', 'FAILED') OR approved_by_user_id IS NOT NULL",
            name="executed_runs_were_approved",
        ),
        CheckConstraint("status <> 'CANCELLED' OR cancelled_by_user_id IS NOT NULL", name="cancellation_recorded"),
        Index("ix_retention_runs_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    policy: Mapped[RetentionPolicy] = mapped_column(enum_type(RetentionPolicy, "retention_policy"), nullable=False)
    status: Mapped[RetentionRunStatus] = mapped_column(enum_type(RetentionRunStatus, "retention_run_status"), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    plan_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    execution_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    planned_by_user_id: Mapped[uuid.UUID] = _user(nullable=False)
    approved_by_user_id: Mapped[uuid.UUID | None] = _user()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_by_user_id: Mapped[uuid.UUID | None] = _user()
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[uuid.UUID | None] = _user()
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
