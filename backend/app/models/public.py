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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import (
    OtpPurpose,
    PublicInformationKind,
    PushPlatform,
    SightingStatus,
    enum_type,
)
from app.models.mixins import CreatedAtMixin, TimestampMixin, uuid_pk


class OtpChallenge(CreatedAtMixin, Base):
    """One-time citizen verification challenge. Plaintext OTP is never stored."""

    __tablename__ = "otp_challenges"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_otp_challenges_attempts_non_negative"),
        CheckConstraint("max_attempts > 0", name="ck_otp_challenges_max_attempts_positive"),
        CheckConstraint("length(btrim(code_hash)) = 64", name="ck_otp_challenges_code_hash_format"),
        CheckConstraint("length(btrim(mobile_lookup_hash)) = 64", name="ck_otp_challenges_mobile_hash_format"),
        Index("ix_otp_challenges_mobile_purpose_created", "mobile_lookup_hash", "purpose", "created_at"),
        Index(
            "ix_otp_challenges_active",
            "mobile_lookup_hash",
            "purpose",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    purpose: Mapped[OtpPurpose] = mapped_column(enum_type(OtpPurpose, "otp_purpose"), nullable=False)
    mobile_lookup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mobile_hint: Mapped[str] = mapped_column(String(16), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE")
    )
    case_number: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    locale: Mapped[str] = mapped_column(String(8), nullable=False, server_default="hi")


class PublicSighting(TimestampMixin, Base):
    """Citizen sighting report. Never auto-confirms a match or reunites a case."""

    __tablename__ = "public_sightings"
    __table_args__ = (
        CheckConstraint("length(btrim(description)) > 0", name="ck_public_sightings_description_not_blank"),
        CheckConstraint("length(btrim(sighting_location)) > 0", name="ck_public_sightings_location_not_blank"),
        Index("ix_public_sightings_case_created", "case_id", "created_at"),
        Index("ix_public_sightings_station_status_created", "station_id", "status", "created_at"),
        Index("ix_public_sightings_mobile_hash", "reporter_mobile_lookup_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL")
    )
    case_number_submitted: Mapped[str | None] = mapped_column(String(32))
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), nullable=False
    )
    sighting_location: Mapped[str] = mapped_column(String(500), nullable=False)
    sighting_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reporter_mobile_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    reporter_mobile_lookup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reporter_mobile_hint: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[SightingStatus] = mapped_column(
        enum_type(SightingStatus, "sighting_status"),
        nullable=False,
        server_default=SightingStatus.UNVERIFIED.value,
    )
    client_ip: Mapped[str | None] = mapped_column(String(64))


class PushDeviceToken(TimestampMixin, Base):
    """Registered FCM/APNs device token for staff users or verified citizen sessions."""

    __tablename__ = "push_device_tokens"
    __table_args__ = (
        UniqueConstraint("token_lookup_hash", name="uq_push_device_tokens_token_hash"),
        CheckConstraint(
            "(user_id IS NOT NULL AND case_id IS NULL) OR (user_id IS NULL AND case_id IS NOT NULL)",
            name="ck_push_device_tokens_owner",
        ),
        Index("ix_push_device_tokens_user_active", "user_id", "is_active"),
        Index("ix_push_device_tokens_case_active", "case_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE")
    )
    platform: Mapped[PushPlatform] = mapped_column(enum_type(PushPlatform, "push_platform"), nullable=False)
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    token_lookup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivation_reason: Mapped[str | None] = mapped_column(String(64))


class PublicInformationItem(TimestampMixin, Base):
    """Approved bilingual content for the citizen portal."""

    __tablename__ = "public_information_items"
    __table_args__ = (
        CheckConstraint("length(btrim(title_en)) > 0", name="ck_public_information_title_en"),
        CheckConstraint("length(btrim(title_hi)) > 0", name="ck_public_information_title_hi"),
        CheckConstraint("length(btrim(body_en)) > 0", name="ck_public_information_body_en"),
        CheckConstraint("length(btrim(body_hi)) > 0", name="ck_public_information_body_hi"),
        Index("ix_public_information_kind_published", "kind", "is_published", "sort_order"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[PublicInformationKind] = mapped_column(
        enum_type(PublicInformationKind, "public_information_kind"), nullable=False
    )
    title_en: Mapped[str] = mapped_column(String(200), nullable=False)
    title_hi: Mapped[str] = mapped_column(String(200), nullable=False)
    body_en: Mapped[str] = mapped_column(Text, nullable=False)
    body_hi: Mapped[str] = mapped_column(Text, nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    location_text: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unpublished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
