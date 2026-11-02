"""PostGIS: reference locations and case coordinates

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21

Requires the PostGIS extension (available on Amazon RDS for PostgreSQL; locally via the
imresamu/postgis image). Existing cases receive no coordinates: positions are only ever captured
explicitly (device GPS or map pin) or by referencing a known location.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import enum_check, grant_tables
from app.models.geo import POINT_EXPRESSION, Geography

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ["GHAT", "SECTOR", "PARKING", "CAMP", "HELP_DESK", "LANDMARK"]
SOURCES = ["DEVICE_GPS", "MAP_PIN"]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_local", sa.String(length=200), nullable=True),
        sa.Column("sector_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("geog", Geography(), sa.Computed(POINT_EXPRESSION, persisted=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(enum_check("kind", KINDS), name=op.f("ck_locations_location_kind")),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name=op.f("ck_locations_latitude_range")),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name=op.f("ck_locations_longitude_range")),
        sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{1,31}$'", name=op.f("ck_locations_code_format")),
        sa.CheckConstraint("kind <> 'SECTOR' OR sector_id IS NULL", name=op.f("ck_locations_sectors_have_no_parent_sector")),
        sa.ForeignKeyConstraint(["sector_id"], ["locations.id"], name=op.f("fk_locations_sector_id_locations"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], name=op.f("fk_locations_station_id_stations"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_locations")),
        sa.UniqueConstraint("code", name=op.f("uq_locations_code")),
    )
    op.create_index("ix_locations_geog", "locations", ["geog"], postgresql_using="gist")
    op.create_index("ix_locations_kind_name", "locations", ["kind", "name"])
    op.create_index(op.f("ix_locations_sector_id"), "locations", ["sector_id"])
    op.create_index(op.f("ix_locations_station_id"), "locations", ["station_id"])

    op.add_column("cases", sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("cases", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("cases", sa.Column("longitude", sa.Float(), nullable=True))
    op.add_column("cases", sa.Column("coordinate_source", sa.String(length=32), nullable=True))
    op.add_column("cases", sa.Column("coordinate_accuracy_m", sa.Float(), nullable=True))
    op.add_column("cases", sa.Column("geo", Geography(), sa.Computed(POINT_EXPRESSION, persisted=True), nullable=True))
    op.create_foreign_key(op.f("fk_cases_location_id_locations"), "cases", "locations", ["location_id"], ["id"], ondelete="RESTRICT")
    op.create_index(op.f("ix_cases_location_id"), "cases", ["location_id"])
    op.create_index("ix_cases_geo", "cases", ["geo"], postgresql_using="gist")
    for name, condition in {
        "ck_cases_coordinate_source": enum_check("coordinate_source", SOURCES),
        "ck_cases_latitude_range": "latitude BETWEEN -90 AND 90",
        "ck_cases_longitude_range": "longitude BETWEEN -180 AND 180",
        "ck_cases_coordinates_paired": "(latitude IS NULL) = (longitude IS NULL)",
        "ck_cases_coordinates_have_source": "(latitude IS NULL) = (coordinate_source IS NULL)",
        "ck_cases_accuracy_needs_coordinates": "coordinate_accuracy_m IS NULL OR latitude IS NOT NULL",
        "ck_cases_accuracy_range": "coordinate_accuracy_m IS NULL OR coordinate_accuracy_m BETWEEN 0 AND 100000",
    }.items():
        op.create_check_constraint(op.f(name), "cases", condition)

    grant_tables("locations")


def downgrade() -> None:
    for name in ("ck_cases_accuracy_range", "ck_cases_accuracy_needs_coordinates", "ck_cases_coordinates_have_source",
                 "ck_cases_coordinates_paired", "ck_cases_longitude_range", "ck_cases_latitude_range",
                 "ck_cases_coordinate_source"):
        op.drop_constraint(op.f(name), "cases", type_="check")
    op.drop_index("ix_cases_geo", table_name="cases")
    op.drop_index(op.f("ix_cases_location_id"), table_name="cases")
    op.drop_constraint(op.f("fk_cases_location_id_locations"), "cases", type_="foreignkey")
    for column in ("geo", "coordinate_accuracy_m", "coordinate_source", "longitude", "latitude", "location_id"):
        op.drop_column("cases", column)
    op.drop_table("locations")
    # The extension is left installed: other databases objects may rely on it.
