import uuid

from sqlalchemy import Boolean, CheckConstraint, Computed, Float, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.database.base import Base
from app.models.enums import LocationKind, enum_type
from app.models.mixins import TimestampMixin, uuid_pk

POINT_EXPRESSION = "(ST_SetSRID(ST_MakePoint(longitude, latitude), 4326))::geography"


class Geography(UserDefinedType):
    """PostGIS geography(Point, 4326). Read-only from the ORM: values are generated from latitude/longitude."""

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "geography(Point,4326)"


def coordinate_checks(prefix: str = "") -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint("latitude BETWEEN -90 AND 90", name=f"{prefix}latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name=f"{prefix}longitude_range"),
    )


class Location(TimestampMixin, Base):
    """Reference places of the mela: ghats, sectors, parking areas, camps, help desks, landmarks."""

    __tablename__ = "locations"
    __table_args__ = (
        *coordinate_checks(),
        CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{1,31}$'", name="code_format"),
        CheckConstraint("kind <> 'SECTOR' OR sector_id IS NULL", name="sectors_have_no_parent_sector"),
        Index("ix_locations_geog", "geog", postgresql_using="gist"),
        Index("ix_locations_kind_name", "kind", "name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[LocationKind] = mapped_column(enum_type(LocationKind, "location_kind"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_local: Mapped[str | None] = mapped_column(String(200))
    sector_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), index=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    geog = mapped_column(Geography(), Computed(POINT_EXPRESSION, persisted=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
