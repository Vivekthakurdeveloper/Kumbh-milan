import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import (
    AlertStatus,
    DispatchOperation,
    DispatchStatus,
    InboundMessageStatus,
    IntegrationAdapter,
    IntegrationHealth,
    IntegrationKind,
    UrgencyLevel,
    enum_type,
)
from app.models.mixins import TimestampMixin, uuid_pk


def _user_fk(nullable: bool = True) -> Mapped:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable)


class IntegrationDestination(TimestampMixin, Base):
    """An allowlisted external endpoint: PA zone, display group, social account, or government system."""

    __tablename__ = "integration_destinations"
    __table_args__ = (
        CheckConstraint("adapter <> 'WEBHOOK' OR endpoint_url IS NOT NULL", name="webhook_has_endpoint"),
        CheckConstraint("endpoint_url IS NULL OR endpoint_url LIKE 'https://%' OR endpoint_url LIKE 'http://%'",
                        name="endpoint_is_http"),
        Index("ix_integration_destinations_kind_enabled", "kind", "is_enabled"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[IntegrationKind] = mapped_column(enum_type(IntegrationKind, "integration_kind"), nullable=False)
    system: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter: Mapped[IntegrationAdapter] = mapped_column(
        enum_type(IntegrationAdapter, "integration_adapter"), nullable=False
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(500))
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"), default=10)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"), default=5)
    health: Mapped[IntegrationHealth] = mapped_column(
        enum_type(IntegrationHealth, "integration_health"),
        nullable=False,
        default=IntegrationHealth.UNKNOWN,
        server_default=IntegrationHealth.UNKNOWN.value,
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicAlert(TimestampMixin, Base):
    """Approved, redacted public message for PA, LED/VMD, and social channels."""

    __tablename__ = "public_alerts"
    __table_args__ = (
        CheckConstraint(
            "status NOT IN ('APPROVED', 'PUBLISHED') OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="approved_fields_present",
        ),
        CheckConstraint("expires_at > starts_at", name="expiry_after_start"),
        Index("ix_public_alerts_status_expires", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"))
    title_en: Mapped[str] = mapped_column(String(200), nullable=False)
    title_hi: Mapped[str] = mapped_column(String(200), nullable=False)
    body_en: Mapped[str] = mapped_column(Text, nullable=False)
    body_hi: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[UrgencyLevel] = mapped_column(
        enum_type(UrgencyLevel, "alert_priority"), nullable=False, server_default=UrgencyLevel.MEDIUM.value
    )
    status: Mapped[AlertStatus] = mapped_column(
        enum_type(AlertStatus, "alert_status"),
        nullable=False,
        default=AlertStatus.DRAFT,
        server_default=AlertStatus.DRAFT.value,
    )
    destination_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    consent_reference: Mapped[str | None] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = _user_fk(nullable=False)
    approved_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdraw_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}


class OutboundDispatch(TimestampMixin, Base):
    """Transactional outbox row for one destination. Payloads are already classified as shareable."""

    __tablename__ = "outbound_dispatches"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("status <> 'DELIVERED' OR delivered_at IS NOT NULL", name="delivered_at_present"),
        CheckConstraint("operation <> 'CASE_EXPORT' OR legal_basis IS NOT NULL", name="export_has_legal_basis"),
        Index("ix_outbound_dispatches_queue", "next_attempt_at", postgresql_where=text("status = 'PENDING'")),
        Index("ix_outbound_dispatches_alert", "alert_id"),
        Index("ix_outbound_dispatches_destination_created", "destination_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integration_destinations.id", ondelete="RESTRICT"), nullable=False
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public_alerts.id", ondelete="RESTRICT")
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"))
    operation: Mapped[DispatchOperation] = mapped_column(
        enum_type(DispatchOperation, "dispatch_operation"), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[DispatchStatus] = mapped_column(
        enum_type(DispatchStatus, "dispatch_status"),
        nullable=False,
        default=DispatchStatus.PENDING,
        server_default=DispatchStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sending_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    remote_reference: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(String(200))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legal_basis: Mapped[str | None] = mapped_column(String(500))
    requested_by_user_id: Mapped[uuid.UUID | None] = _user_fk()


class InboundIntegrationMessage(TimestampMixin, Base):
    """Signed inbound payload from an external system. Untrusted until a staff member accepts it."""

    __tablename__ = "inbound_integration_messages"
    __table_args__ = (
        UniqueConstraint("destination_id", "external_id", name="uq_inbound_integration_messages_external"),
        Index("ix_inbound_integration_messages_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    destination_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integration_destinations.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[InboundMessageStatus] = mapped_column(
        enum_type(InboundMessageStatus, "inbound_message_status"),
        nullable=False,
        default=InboundMessageStatus.QUARANTINED,
        server_default=InboundMessageStatus.QUARANTINED.value,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = _user_fk()
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
