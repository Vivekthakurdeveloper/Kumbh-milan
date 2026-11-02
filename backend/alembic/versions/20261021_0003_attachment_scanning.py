"""quarantine-first attachment scanning

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21

Scan states QUARANTINED -> SCANNING -> CLEAN | REJECTED (SCAN_FAILED after retries; SKIPPED_DEV without a
scanner in development). Rows stored before this revision were never scanned, so UNSCANNED becomes
SKIPPED_DEV: such files are not downloadable in staging or production.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.database.migration_support import enum_check, replace_check

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_STATES = ["QUARANTINED", "SCANNING", "CLEAN", "REJECTED", "SCAN_FAILED", "SKIPPED_DEV"]


def upgrade() -> None:
    op.add_column("case_attachments", sa.Column("scan_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("case_attachments", sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_attachments", sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_attachments", sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_attachments", sa.Column("scan_signature", sa.String(length=200), nullable=True))
    op.add_column("case_attachments", sa.Column("last_scan_error", sa.String(length=200), nullable=True))
    op.add_column("case_attachments", sa.Column("object_purged_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("ALTER TABLE case_attachments DROP CONSTRAINT IF EXISTS ck_case_attachments_scan_status")
    op.execute("UPDATE case_attachments SET scan_status = 'SKIPPED_DEV' WHERE scan_status = 'UNSCANNED'")
    replace_check("case_attachments", "ck_case_attachments_scan_status", enum_check("scan_status", NEW_STATES))
    replace_check("case_attachments", "ck_case_attachments_scan_attempts_non_negative", "scan_attempts >= 0")
    op.create_index(
        "ix_case_attachments_scan_queue", "case_attachments", ["next_scan_at"],
        postgresql_where=sa.text("scan_status IN ('QUARANTINED', 'SCANNING')"),
    )


def downgrade() -> None:
    op.drop_index("ix_case_attachments_scan_queue", table_name="case_attachments")
    op.execute("ALTER TABLE case_attachments DROP CONSTRAINT IF EXISTS ck_case_attachments_scan_attempts_non_negative")
    op.execute("ALTER TABLE case_attachments DROP CONSTRAINT IF EXISTS ck_case_attachments_scan_status")
    op.execute("UPDATE case_attachments SET scan_status = 'UNSCANNED' WHERE scan_status <> 'CLEAN'")
    replace_check("case_attachments", "ck_case_attachments_scan_status", enum_check("scan_status", ["UNSCANNED", "CLEAN"]))
    for column in ("object_purged_at", "last_scan_error", "scan_signature", "scanned_at", "scan_started_at",
                   "next_scan_at", "scan_attempts"):
        op.drop_column("case_attachments", column)
