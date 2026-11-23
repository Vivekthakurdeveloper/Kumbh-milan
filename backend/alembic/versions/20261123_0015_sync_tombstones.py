"""Add delivered-case ledger and per-user sync tombstones.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-23

The ledger records only cases actually returned to a user. Tombstones are
therefore non-enumerating: a user never receives a removal event for a case
they did not previously possess.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import grant_tables

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_delivered_cases",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "first_delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_currently_delivered",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_sync_delivered_cases_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_sync_delivered_cases_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", "case_id", name=op.f("pk_sync_delivered_cases")),
    )
    op.create_index(
        "ix_sync_delivered_cases_user_last",
        "sync_delivered_cases",
        ["user_id", "last_delivered_at"],
    )

    op.create_table(
        "sync_tombstones",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "ARCHIVED",
                "SCOPE_REVOKED",
                name="sync_tombstone_reason",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_sync_tombstones_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_sync_tombstones_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_tombstones")),
    )
    op.create_index(
        "ix_sync_tombstones_user_occurred_id",
        "sync_tombstones",
        ["user_id", "occurred_at", "id"],
    )
    op.execute(
        """
        CREATE TRIGGER sync_tombstones_append_only
        BEFORE UPDATE OR DELETE ON sync_tombstones
        FOR EACH ROW EXECUTE FUNCTION forbid_append_only_mutation()
        """
    )

    grant_tables("sync_delivered_cases")
    grant_tables("sync_tombstones", append_only=True)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS sync_tombstones_append_only ON sync_tombstones")
    op.drop_table("sync_tombstones")
    op.drop_table("sync_delivered_cases")
