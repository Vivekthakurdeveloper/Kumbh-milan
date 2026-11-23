import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import ReportCadence, ReportJobStatus, ReportType, enum_type
from app.models.mixins import TimestampMixin, uuid_pk


class ItemCategory(TimestampMixin, Base):
    """Managed lost/found item taxonomy with stable codes and bilingual labels."""

    __tablename__ = "item_categories"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    label_en: Mapped[str] = mapped_column(String(100), nullable=False)
    label_hi: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class ReportSchedule(TimestampMixin, Base):
    __tablename__ = "report_schedules"
    __table_args__ = (Index("ix_report_schedules_due", "next_run_at", postgresql_where=text("is_active")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    report_type: Mapped[ReportType] = mapped_column(enum_type(ReportType, "schedule_report_type"), nullable=False)
    cadence: Mapped[ReportCadence] = mapped_column(enum_type(ReportCadence, "report_cadence"), nullable=False)
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT")
    )
    recipient_user_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ReportJob(TimestampMixin, Base):
    """Asynchronous MIS report. The artifact is stored privately and expires."""

    __tablename__ = "report_jobs"
    __table_args__ = (
        CheckConstraint("status <> 'COMPLETED' OR storage_key IS NOT NULL", name="completed_has_artifact"),
        Index("ix_report_jobs_queue", "created_at", postgresql_where=text("status = 'QUEUED'")),
        Index("ix_report_jobs_requester_created", "requested_by_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    report_type: Mapped[ReportType] = mapped_column(enum_type(ReportType, "report_type"), nullable=False)
    status: Mapped[ReportJobStatus] = mapped_column(
        enum_type(ReportJobStatus, "report_job_status"),
        nullable=False,
        default=ReportJobStatus.QUEUED,
        server_default=ReportJobStatus.QUEUED.value,
    )
    period_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT")
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_schedules.id", ondelete="SET NULL")
    )
    storage_key: Mapped[str | None] = mapped_column(String(512))
    row_count: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
