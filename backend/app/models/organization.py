import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import Role, StationKind, enum_type
from app.models.mixins import TimestampMixin, uuid_pk


class Station(TimestampMixin, Base):
    __tablename__ = "stations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    kind: Mapped[StationKind] = mapped_column(enum_type(StationKind, "station_kind"), nullable=False)
    location_description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    users: Mapped[list["User"]] = relationship(back_populates="station")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'PUBLIC') OR station_id IS NOT NULL",
            name="station_required_for_non_admin",
        ),
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        Index("ix_users_station_role_active", "station_id", "role", "is_active"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(enum_type(Role, "user_role"), nullable=False)
    police_badge_number: Mapped[str | None] = mapped_column(String(64), unique=True)
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)

    station: Mapped[Station | None] = relationship(back_populates="users")
