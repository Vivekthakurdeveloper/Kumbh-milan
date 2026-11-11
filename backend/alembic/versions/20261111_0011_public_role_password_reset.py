"""PUBLIC role and self-service password reset tokens.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11

Additive only. Existing staff roles and passwords are unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import enum_check, grant_tables, replace_check

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    replace_check(
        "users",
        "ck_users_user_role",
        enum_check("role", ["ADMIN", "POLICE_OFFICER", "HELP_DESK_OPERATOR", "PUBLIC"]),
    )
    replace_check(
        "users",
        "ck_users_station_required_for_non_admin",
        "role IN ('ADMIN', 'PUBLIC') OR station_id IS NOT NULL",
    )
    replace_check(
        "notifications",
        "ck_notifications_notification_template",
        enum_check(
            "template",
            [
                "CASE_REGISTERED",
                "CASE_UPDATE_VISIT_DESK",
                "CASE_SUBMITTED",
                "CASE_APPROVED",
                "CASE_STATUS_UPDATED",
                "POTENTIAL_MATCH_VERIFIED",
                "CASE_REUNITED",
                "CASE_CLOSED",
                "OTP_CODE",
                "PASSWORD_RESET",
            ],
        ),
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "length(btrim(token_hash)) = 64",
            name="ck_password_reset_tokens_hash_format",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_password_reset_tokens_user_active",
        "password_reset_tokens",
        ["user_id", "consumed_at"],
    )
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])
    op.create_index(
        "ix_password_reset_tokens_active_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    grant_tables("password_reset_tokens")


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_active_hash", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_expires_at", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_active", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    replace_check(
        "notifications",
        "ck_notifications_notification_template",
        enum_check(
            "template",
            [
                "CASE_REGISTERED",
                "CASE_UPDATE_VISIT_DESK",
                "CASE_SUBMITTED",
                "CASE_APPROVED",
                "CASE_STATUS_UPDATED",
                "POTENTIAL_MATCH_VERIFIED",
                "CASE_REUNITED",
                "CASE_CLOSED",
                "OTP_CODE",
            ],
        ),
    )
    replace_check(
        "users",
        "ck_users_station_required_for_non_admin",
        "role = 'ADMIN' OR station_id IS NOT NULL",
    )
    replace_check(
        "users",
        "ck_users_user_role",
        enum_check("role", ["ADMIN", "POLICE_OFFICER", "HELP_DESK_OPERATOR"]),
    )
