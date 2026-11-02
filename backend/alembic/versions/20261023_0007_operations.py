"""Worker heartbeats, handover evidence, safeguarding flag, and optional TOTP metadata.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-23

Forward recovery: these are additive nullable columns and one new table. If upgrade fails
after the table is created, rerun `alembic upgrade head`; each step is idempotent enough
for a retry only when the failing statement is re-run on a clean transaction. Do not
downgrade a database that already stored handover or MFA data. Restore the pre-migration
backup instead of downgrading production.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.database.migration_support import grant_tables

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(length=32), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("worker_name", name="pk_worker_heartbeats"),
    )
    op.add_column("found_person_details", sa.Column("safeguarding_review_required", sa.Boolean(), nullable=True))
    op.add_column("found_item_details", sa.Column("handover_acknowledgement", sa.Text(), nullable=True))
    op.add_column("found_item_details", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_secret_ciphertext", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    grant_tables("worker_heartbeats")


def downgrade() -> None:
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_ciphertext")
    op.drop_column("found_item_details", "released_at")
    op.drop_column("found_item_details", "handover_acknowledgement")
    op.drop_column("found_person_details", "safeguarding_review_required")
    op.drop_table("worker_heartbeats")
