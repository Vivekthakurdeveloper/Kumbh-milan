import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin, uuid_pk


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
