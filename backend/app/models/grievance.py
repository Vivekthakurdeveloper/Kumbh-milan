import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import GrievanceCategory, GrievanceEventKind, GrievanceStatus, UrgencyLevel, enum_type
from app.models.mixins import CreatedAtMixin, TimestampMixin, uuid_pk


def _user_fk(nullable: bool = True) -> Mapped:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable)


class Grievance(TimestampMixin, Base):
    """Citizen grievance. Description and mobile number are encrypted; only a masked hint is returned."""

    __tablename__ = "grievances"
    __table_args__ = (
        CheckConstraint(
            "citizen_user_id IS NOT NULL OR mobile_lookup_hash IS NOT NULL OR anonymized_at IS NOT NULL",
            name="has_citizen",
        ),
        CheckConstraint("(status = 'CLOSED') = (closed_at IS NOT NULL)", name="closed_at_matches_status"),
        CheckConstraint("escalation_level >= 0", name="escalation_level_non_negative"),
        Index("ix_grievances_station_status_created", "station_id", "status", "created_at"),
        Index("ix_grievances_due_open", "due_at", postgresql_where=text("status NOT IN ('RESOLVED', 'CLOSED')")),
        Index("ix_grievances_citizen", "citizen_user_id"),
        Index("ix_grievances_mobile_hash", "mobile_lookup_hash"),
        Index("ix_grievances_assignee", "assigned_to_user_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    reference: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    category: Mapped[GrievanceCategory] = mapped_column(
        enum_type(GrievanceCategory, "grievance_category"), nullable=False
    )
    status: Mapped[GrievanceStatus] = mapped_column(
        enum_type(GrievanceStatus, "grievance_status"),
        nullable=False,
        default=GrievanceStatus.OPEN,
        server_default=GrievanceStatus.OPEN.value,
    )
    priority: Mapped[UrgencyLevel] = mapped_column(
        enum_type(UrgencyLevel, "grievance_priority"),
        nullable=False,
        default=UrgencyLevel.MEDIUM,
        server_default=UrgencyLevel.MEDIUM.value,
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), nullable=False
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"))
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    description_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    citizen_user_id: Mapped[uuid.UUID | None] = _user_fk()
    mobile_ciphertext: Mapped[str | None] = mapped_column(Text)
    mobile_lookup_hash: Mapped[str | None] = mapped_column(String(64))
    mobile_hint: Mapped[str | None] = mapped_column(String(16))
    locale: Mapped[str] = mapped_column(String(8), nullable=False, server_default="hi")
    assigned_to_user_id: Mapped[uuid.UUID | None] = _user_fk()
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_ciphertext: Mapped[str | None] = mapped_column(Text)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}


class GrievanceEvent(CreatedAtMixin, Base):
    """Append-only grievance history: status, assignment, comments, and escalations."""

    __tablename__ = "grievance_events"
    __table_args__ = (Index("ix_grievance_events_grievance_created", "grievance_id", "created_at", "id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    grievance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grievances.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[GrievanceEventKind] = mapped_column(
        enum_type(GrievanceEventKind, "grievance_event_kind"), nullable=False
    )
    from_status: Mapped[GrievanceStatus | None] = mapped_column(enum_type(GrievanceStatus, "grievance_from_status"))
    to_status: Mapped[GrievanceStatus | None] = mapped_column(enum_type(GrievanceStatus, "grievance_to_status"))
    actor_user_id: Mapped[uuid.UUID | None] = _user_fk()
    assignee_user_id: Mapped[uuid.UUID | None] = _user_fk()
    body_ciphertext: Mapped[str | None] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    by_citizen: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
