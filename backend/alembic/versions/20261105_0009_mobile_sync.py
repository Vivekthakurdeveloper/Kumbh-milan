"""Mobile offline sync metadata, sync_changed_at cursor, and sync_devices.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-05

Additive only. Existing rows keep working: sync columns are nullable, sync_changed_at
is backfilled from updated_at, and client_record_id uniqueness applies only when set.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.database.migration_support import grant_tables

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SYNC_COLUMNS = (
    ("client_id", sa.String(length=128)),
    ("device_id", sa.String(length=128)),
    ("client_record_id", sa.String(length=128)),
    ("client_created_at", sa.DateTime(timezone=True)),
    ("server_synced_at", sa.DateTime(timezone=True)),
)


def _add_sync_columns(table: str) -> None:
    for name, column_type in _SYNC_COLUMNS:
        op.add_column(table, sa.Column(name, column_type, nullable=True))
    op.create_check_constraint(
        f"ck_{table}_client_record_requires_device",
        table,
        "client_record_id IS NULL OR device_id IS NOT NULL",
    )


def _drop_sync_columns(table: str) -> None:
    op.drop_constraint(f"ck_{table}_client_record_requires_device", table, type_="check")
    for name, _ in reversed(_SYNC_COLUMNS):
        op.drop_column(table, name)


def upgrade() -> None:
    _add_sync_columns("cases")
    op.add_column(
        "cases",
        sa.Column("sync_status", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_cases_sync_status",
        "cases",
        "sync_status IS NULL OR sync_status = 'SYNCED'",
    )
    op.add_column(
        "cases",
        sa.Column("sync_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE cases SET sync_changed_at = updated_at WHERE sync_changed_at IS NULL")
    op.alter_column(
        "cases",
        "sync_changed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.create_index("ix_cases_sync_changed_at_id", "cases", ["sync_changed_at", "id"])
    op.create_index(
        "uq_cases_reporter_device_client_record",
        "cases",
        ["reported_by_user_id", "device_id", "client_record_id"],
        unique=True,
        postgresql_where=sa.text("client_record_id IS NOT NULL"),
    )

    _add_sync_columns("case_notes")
    op.create_index(
        "uq_case_notes_author_device_client_record",
        "case_notes",
        ["author_user_id", "device_id", "client_record_id"],
        unique=True,
        postgresql_where=sa.text("client_record_id IS NOT NULL"),
    )

    _add_sync_columns("case_attachments")
    op.create_index(
        "uq_case_attachments_uploader_device_client_record",
        "case_attachments",
        ["uploaded_by_user_id", "device_id", "client_record_id"],
        unique=True,
        postgresql_where=sa.text("client_record_id IS NOT NULL"),
    )

    op.create_table(
        "sync_devices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=True),
        sa.Column("last_push_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_push_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_count", sa.Integer(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=True),
        sa.Column("last_pull_cursor", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("pending_count IS NULL OR pending_count >= 0", name="ck_sync_devices_pending_non_negative"),
        sa.CheckConstraint("failed_count IS NULL OR failed_count >= 0", name="ck_sync_devices_failed_non_negative"),
        sa.CheckConstraint("length(btrim(device_id)) > 0", name="ck_sync_devices_device_id_not_blank"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "device_id", name="uq_sync_devices_user_device"),
    )
    op.create_index("ix_sync_devices_user_id", "sync_devices", ["user_id"])
    grant_tables("sync_devices")

    op.execute(
        """
        CREATE FUNCTION bump_case_sync_changed_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.sync_changed_at := clock_timestamp();
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER cases_bump_sync_changed_at
        BEFORE UPDATE ON cases
        FOR EACH ROW
        WHEN (NEW.sync_changed_at IS NOT DISTINCT FROM OLD.sync_changed_at)
        EXECUTE FUNCTION bump_case_sync_changed_at();
        """
    )
    op.execute(
        """
        CREATE FUNCTION bump_parent_case_sync_changed_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE cases
               SET sync_changed_at = clock_timestamp()
             WHERE id = NEW.case_id;
            RETURN NEW;
        END;
        $$;
        """
    )
    # case_notes is append-only: INSERT only. Attachments get INSERT and UPDATE (scan, soft delete).
    op.execute(
        """
        CREATE TRIGGER case_notes_bump_case_sync_changed_at
        AFTER INSERT ON case_notes
        FOR EACH ROW EXECUTE FUNCTION bump_parent_case_sync_changed_at();
        """
    )
    op.execute(
        """
        CREATE TRIGGER case_attachments_bump_case_sync_changed_at
        AFTER INSERT OR UPDATE ON case_attachments
        FOR EACH ROW EXECUTE FUNCTION bump_parent_case_sync_changed_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS case_attachments_bump_case_sync_changed_at ON case_attachments")
    op.execute("DROP TRIGGER IF EXISTS case_notes_bump_case_sync_changed_at ON case_notes")
    op.execute("DROP TRIGGER IF EXISTS cases_bump_sync_changed_at ON cases")
    op.execute("DROP FUNCTION IF EXISTS bump_parent_case_sync_changed_at()")
    op.execute("DROP FUNCTION IF EXISTS bump_case_sync_changed_at()")

    op.drop_index("ix_sync_devices_user_id", table_name="sync_devices")
    op.drop_table("sync_devices")

    op.drop_index("uq_case_attachments_uploader_device_client_record", table_name="case_attachments")
    _drop_sync_columns("case_attachments")

    op.drop_index("uq_case_notes_author_device_client_record", table_name="case_notes")
    _drop_sync_columns("case_notes")

    op.drop_index("uq_cases_reporter_device_client_record", table_name="cases")
    op.drop_index("ix_cases_sync_changed_at_id", table_name="cases")
    op.drop_constraint("ck_cases_sync_status", "cases", type_="check")
    op.drop_column("cases", "sync_changed_at")
    op.drop_column("cases", "sync_status")
    _drop_sync_columns("cases")
