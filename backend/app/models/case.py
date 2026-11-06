import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.database.base import Base
from app.models.enums import (
    AgeGroup,
    CaseStatus,
    CaseType,
    CommunicationStatus,
    CoordinateSource,
    CustodyStatus,
    Gender,
    UrgencyLevel,
    enum_type,
)
from app.models.geo import POINT_EXPRESSION, Geography, coordinate_checks
from app.models.mixins import TimestampMixin, uuid_pk
from app.models.organization import Station, User


class CaseNumberCounter(Base):
    """Per type and year counter. Allocated with INSERT .. ON CONFLICT DO UPDATE .. RETURNING."""

    __tablename__ = "case_number_counters"

    case_type: Mapped[CaseType] = mapped_column(
        enum_type(CaseType, "counter_case_type"), primary_key=True
    )
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_value: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Case(TimestampMixin, Base):
    """Supertype row shared by every case type; workflow tables reference cases.id."""

    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("id", "case_type", name="uq_cases_id_case_type"),
        CheckConstraint(
            "(status = 'CLOSED') = (closed_at IS NOT NULL)", name="closed_at_matches_status"
        ),
        Index("ix_cases_type_status_created", "case_type", "status", "created_at"),
        Index("ix_cases_station_created", "station_id", "created_at"),
        Index("ix_cases_created_id", "created_at", "id"),
        Index("ix_cases_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_cases_geo", "geo", postgresql_using="gist"),
        CheckConstraint("anonymized_at IS NULL OR archived_at IS NOT NULL", name="anonymized_cases_are_archived"),
        CheckConstraint("archived_at IS NULL OR status IN ('CLOSED', 'RESOLVED')", name="only_finished_cases_archived"),
        *coordinate_checks(),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="coordinates_paired"),
        CheckConstraint("(latitude IS NULL) = (coordinate_source IS NULL)", name="coordinates_have_source"),
        CheckConstraint("coordinate_accuracy_m IS NULL OR latitude IS NOT NULL", name="accuracy_needs_coordinates"),
        CheckConstraint("coordinate_accuracy_m IS NULL OR coordinate_accuracy_m BETWEEN 0 AND 100000", name="accuracy_range"),
        CheckConstraint("client_record_id IS NULL OR device_id IS NOT NULL", name="ck_cases_client_record_requires_device"),
        CheckConstraint("sync_status IS NULL OR sync_status = 'SYNCED'", name="ck_cases_sync_status"),
        Index("ix_cases_sync_changed_at_id", "sync_changed_at", "id"),
        Index(
            "uq_cases_reporter_device_client_record",
            "reported_by_user_id",
            "device_id",
            "client_record_id",
            unique=True,
            postgresql_where=text("client_record_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    case_type: Mapped[CaseType] = mapped_column(enum_type(CaseType, "case_type"), nullable=False)
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), nullable=False
    )
    reported_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[CaseStatus] = mapped_column(
        enum_type(CaseStatus, "case_status"), nullable=False, server_default=CaseStatus.OPEN.value
    )
    urgency_level: Mapped[UrgencyLevel | None] = mapped_column(enum_type(UrgencyLevel, "urgency_level"))
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, search_text)", persisted=True)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    coordinate_source: Mapped[CoordinateSource | None] = mapped_column(enum_type(CoordinateSource, "coordinate_source"))
    coordinate_accuracy_m: Mapped[float | None] = mapped_column(Float)
    geo = mapped_column(Geography(), Computed(POINT_EXPRESSION, persisted=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_id: Mapped[str | None] = mapped_column(String(128))
    device_id: Mapped[str | None] = mapped_column(String(128))
    client_record_id: Mapped[str | None] = mapped_column(String(128))
    client_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[str | None] = mapped_column(String(32))
    sync_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __mapper_args__ = {"version_id_col": version}

    station: Mapped[Station] = relationship(lazy="raise")
    reported_by: Mapped[User] = relationship(foreign_keys=[reported_by_user_id], lazy="raise")
    assigned_to: Mapped[User | None] = relationship(foreign_keys=[assigned_to_user_id], lazy="raise")

    missing_person: Mapped["MissingPersonDetails | None"] = relationship(
        back_populates="case", uselist=False, lazy="raise"
    )
    found_person: Mapped["FoundPersonDetails | None"] = relationship(
        back_populates="case", uselist=False, lazy="raise"
    )
    lost_item: Mapped["LostItemDetails | None"] = relationship(
        back_populates="case", uselist=False, lazy="raise"
    )
    found_item: Mapped["FoundItemDetails | None"] = relationship(
        back_populates="case", uselist=False, lazy="raise"
    )


class _CaseSubtype:
    """Subtype row: 1:1 with cases, pinned to one case_type by a composite FK and a CHECK."""

    SUBTYPE: CaseType

    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    @declared_attr
    def case_type(cls) -> Mapped[CaseType]:
        return mapped_column(
            enum_type(CaseType, "case_type"),
            nullable=False,
            default=cls.SUBTYPE,
            server_default=cls.SUBTYPE.value,
        )

    @declared_attr.directive
    def __table_args__(cls):
        args = [
            ForeignKeyConstraint(
                ["case_id", "case_type"],
                ["cases.id", "cases.case_type"],
                ondelete="RESTRICT",
                name=f"fk_{cls.__tablename__}_case",
            ),
            CheckConstraint(f"case_type = '{cls.SUBTYPE.value}'", name="fixed_case_type"),
        ]
        if "approximate_age" in cls.__dict__:
            args.append(
                CheckConstraint(
                    "approximate_age IS NULL OR approximate_age BETWEEN 0 AND 120",
                    name="approximate_age_range",
                )
            )
        return tuple(args)


class MissingPersonDetails(_CaseSubtype, Base):
    __tablename__ = "missing_person_details"
    SUBTYPE = CaseType.MISSING_PERSON

    person_name: Mapped[str | None] = mapped_column(String(200))
    approximate_age: Mapped[int | None] = mapped_column(SmallInteger)
    age_group: Mapped[AgeGroup | None] = mapped_column(enum_type(AgeGroup, "mp_age_group"))
    gender: Mapped[Gender | None] = mapped_column(enum_type(Gender, "mp_gender"))
    height_description: Mapped[str | None] = mapped_column(String(200))
    clothing_description: Mapped[str | None] = mapped_column(Text)
    identifying_marks: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(100))
    last_seen_location: Mapped[str | None] = mapped_column(String(500))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    has_disability: Mapped[bool | None] = mapped_column(Boolean)
    medical_needs: Mapped[str | None] = mapped_column(Text)
    additional_description: Mapped[str | None] = mapped_column(Text)

    case: Mapped[Case] = relationship(back_populates="missing_person", lazy="raise")


class FoundPersonDetails(_CaseSubtype, Base):
    __tablename__ = "found_person_details"
    SUBTYPE = CaseType.FOUND_PERSON

    approximate_age: Mapped[int | None] = mapped_column(SmallInteger)
    age_group: Mapped[AgeGroup | None] = mapped_column(enum_type(AgeGroup, "fp_age_group"))
    estimated_gender: Mapped[Gender | None] = mapped_column(enum_type(Gender, "fp_gender"))
    physical_description: Mapped[str | None] = mapped_column(Text)
    clothing_description: Mapped[str | None] = mapped_column(Text)
    identifying_marks: Mapped[str | None] = mapped_column(Text)
    found_location: Mapped[str | None] = mapped_column(String(500))
    found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    communication_status: Mapped[CommunicationStatus | None] = mapped_column(
        enum_type(CommunicationStatus, "fp_communication_status")
    )
    language: Mapped[str | None] = mapped_column(String(100))
    has_disability: Mapped[bool | None] = mapped_column(Boolean)
    care_or_assistance_required: Mapped[str | None] = mapped_column(Text)
    current_care_location: Mapped[str | None] = mapped_column(String(500))
    safeguarding_review_required: Mapped[bool | None] = mapped_column(Boolean)

    case: Mapped[Case] = relationship(back_populates="found_person", lazy="raise")


class LostItemDetails(_CaseSubtype, Base):
    __tablename__ = "lost_item_details"
    SUBTYPE = CaseType.LOST_ITEM

    item_category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    item_description: Mapped[str | None] = mapped_column(Text)
    brand_or_color: Mapped[str | None] = mapped_column(String(200))
    identifying_features: Mapped[str | None] = mapped_column(Text)
    last_seen_location: Mapped[str | None] = mapped_column(String(500))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    case: Mapped[Case] = relationship(back_populates="lost_item", lazy="raise")


class FoundItemDetails(_CaseSubtype, Base):
    __tablename__ = "found_item_details"
    SUBTYPE = CaseType.FOUND_ITEM

    item_category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    item_description: Mapped[str | None] = mapped_column(Text)
    item_condition: Mapped[str | None] = mapped_column(String(200))
    found_location: Mapped[str | None] = mapped_column(String(500))
    found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    storage_location: Mapped[str | None] = mapped_column(String(500))
    custody_status: Mapped[CustodyStatus] = mapped_column(
        enum_type(CustodyStatus, "custody_status"),
        nullable=False,
        default=CustodyStatus.IN_CUSTODY,
        server_default=CustodyStatus.IN_CUSTODY.value,
    )
    handover_acknowledgement: Mapped[str | None] = mapped_column(Text)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[Case] = relationship(back_populates="found_item", lazy="raise")
