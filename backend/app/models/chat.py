import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import ChatChannel, ChatRole, ChatSessionStatus, enum_type
from app.models.mixins import CreatedAtMixin, TimestampMixin, uuid_pk


class ChatSession(TimestampMixin, Base):
    """Citizen chatbot conversation. The session secret is stored only as a hash."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_status_activity", "status", "last_activity_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[ChatChannel] = mapped_column(enum_type(ChatChannel, "chat_channel"), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[ChatSessionStatus] = mapped_column(
        enum_type(ChatSessionStatus, "chat_session_status"),
        nullable=False,
        default=ChatSessionStatus.OPEN,
        server_default=ChatSessionStatus.OPEN.value,
    )
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    handoff_reason: Mapped[str | None] = mapped_column(String(64))
    handed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessage(CreatedAtMixin, Base):
    """Redacted transcript line. Phone numbers, emails, and long digit runs are masked before insert."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_session_created", "session_id", "created_at", "id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ChatRole] = mapped_column(enum_type(ChatRole, "chat_role"), nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32))
    text_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
