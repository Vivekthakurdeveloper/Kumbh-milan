import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import (
    AttachmentUploadSessionStatus,
    MediaType,
    SyncTombstoneReason,
    enum_type,
)
from app.models.mixins import CreatedAtMixin, TimestampMixin, uuid_pk


class SyncDevice(TimestampMixin, Base):
    """Per-user device sync cursor and client-reported backlog. Created on first sync call."""

    __tablename__ = "sync_devices"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", name="uq_sync_devices_user_device"),
        CheckConstraint("pending_count IS NULL OR pending_count >= 0", name="ck_sync_devices_pending_non_negative"),
        CheckConstraint("failed_count IS NULL OR failed_count >= 0", name="ck_sync_devices_failed_non_negative"),
        CheckConstraint("length(btrim(device_id)) > 0", name="ck_sync_devices_device_id_not_blank"),
        Index("ix_sync_devices_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(128))
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_count: Mapped[int | None] = mapped_column(Integer)
    failed_count: Mapped[int | None] = mapped_column(Integer)
    last_pull_cursor: Mapped[str | None] = mapped_column(String(256))


class SyncDeliveredCase(Base):
    """A case previously returned to a user by sync and therefore eligible for a tombstone."""

    __tablename__ = "sync_delivered_cases"
    __table_args__ = (
        Index("ix_sync_delivered_cases_user_last", "user_id", "last_delivered_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), primary_key=True
    )
    first_delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    is_currently_delivered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncTombstone(CreatedAtMixin, Base):
    """Append-only per-user instruction to remove a formerly visible local case."""

    __tablename__ = "sync_tombstones"
    __table_args__ = (
        Index("ix_sync_tombstones_user_occurred_id", "user_id", "occurred_at", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[SyncTombstoneReason] = mapped_column(
        enum_type(SyncTombstoneReason, "sync_tombstone_reason"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttachmentUploadSession(TimestampMixin, Base):
    """Durable manifest and offset for an offline attachment being uploaded in chunks."""

    __tablename__ = "attachment_upload_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "device_id",
            "client_record_id",
            name="uq_attachment_upload_sessions_user_device_record",
        ),
        CheckConstraint("expected_size_bytes > 0", name="expected_size_positive"),
        CheckConstraint(
            "bytes_received >= 0 AND bytes_received <= expected_size_bytes",
            name="bytes_received_range",
        ),
        CheckConstraint(
            "expected_checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="expected_checksum_format",
        ),
        CheckConstraint(
            "status <> 'FINALIZED' OR attachment_id IS NOT NULL",
            name="finalized_has_attachment",
        ),
        Index(
            "ix_attachment_upload_sessions_user_device_status",
            "user_id",
            "device_id",
            "status",
        ),
        Index("ix_attachment_upload_sessions_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(128))
    media_type: Mapped[MediaType] = mapped_column(
        enum_type(MediaType, "attachment_upload_media_type"), nullable=False
    )
    expected_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    declared_content_type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[AttachmentUploadSessionStatus] = mapped_column(
        enum_type(AttachmentUploadSessionStatus, "attachment_upload_session_status"),
        nullable=False,
        default=AttachmentUploadSessionStatus.OPEN,
        server_default=AttachmentUploadSessionStatus.OPEN.value,
    )
    bytes_received: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    staging_storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_attachments.id", ondelete="RESTRICT")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}
