"""Add resumable offline attachment upload sessions.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import grant_tables

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_upload_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("client_record_id", sa.String(length=128), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=True),
        sa.Column(
            "media_type",
            sa.Enum(
                "PHOTO",
                "AUDIO",
                name="attachment_upload_media_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("expected_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("declared_content_type", sa.String(length=100), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "READY",
                "FINALIZED",
                "FAILED",
                "ABORTED",
                "EXPIRED",
                name="attachment_upload_session_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column("bytes_received", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("staging_storage_key", sa.String(length=512), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "expected_size_bytes > 0",
            name=op.f("ck_attachment_upload_sessions_expected_size_positive"),
        ),
        sa.CheckConstraint(
            "bytes_received >= 0 AND bytes_received <= expected_size_bytes",
            name=op.f("ck_attachment_upload_sessions_bytes_received_range"),
        ),
        sa.CheckConstraint(
            "expected_checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_attachment_upload_sessions_expected_checksum_format"),
        ),
        sa.CheckConstraint(
            "status <> 'FINALIZED' OR attachment_id IS NOT NULL",
            name=op.f("ck_attachment_upload_sessions_finalized_has_attachment"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_attachment_upload_sessions_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_attachment_upload_sessions_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["case_attachments.id"],
            name=op.f("fk_attachment_upload_sessions_attachment_id_case_attachments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attachment_upload_sessions")),
        sa.UniqueConstraint(
            "staging_storage_key",
            name=op.f("uq_attachment_upload_sessions_staging_storage_key"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "device_id",
            "client_record_id",
            name="uq_attachment_upload_sessions_user_device_record",
        ),
    )
    op.create_index(
        "ix_attachment_upload_sessions_user_device_status",
        "attachment_upload_sessions",
        ["user_id", "device_id", "status"],
    )
    op.create_index(
        "ix_attachment_upload_sessions_expires",
        "attachment_upload_sessions",
        ["expires_at"],
    )
    grant_tables("attachment_upload_sessions")


def downgrade() -> None:
    op.drop_table("attachment_upload_sessions")
