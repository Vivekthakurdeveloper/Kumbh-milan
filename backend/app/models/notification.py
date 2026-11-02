import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import NotificationChannel, NotificationStatus, NotificationTemplate, enum_type
from app.models.mixins import TimestampMixin, uuid_pk


def _user(nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable)


class Notification(TimestampMixin, Base):
    """
    Outbox row for a message to a family member or owner. The recipient is encrypted; only a masked hint is
    ever returned. Messages are rendered from PII-free templates at send time.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("(status = 'SENT') = (sent_at IS NOT NULL)", name="sent_at_matches_status"),
        CheckConstraint("length(btrim(consent_statement)) > 0", name="consent_recorded"),
        CheckConstraint("recipient_ciphertext IS NOT NULL OR status IN ('SENT', 'FAILED', 'CANCELLED')",
                        name="pending_has_recipient"),
        Index("ix_notifications_queue", "next_attempt_at", postgresql_where=text("status IN ('PENDING', 'SENDING')")),
        Index("ix_notifications_case_created", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False)
    template: Mapped[NotificationTemplate] = mapped_column(enum_type(NotificationTemplate, "notification_template"), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(enum_type(NotificationChannel, "notification_channel"), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    recipient_ciphertext: Mapped[str | None] = mapped_column(Text)
    recipient_hint: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[NotificationStatus] = mapped_column(enum_type(NotificationStatus, "notification_status"), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sending_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    consent_statement: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = _user()
