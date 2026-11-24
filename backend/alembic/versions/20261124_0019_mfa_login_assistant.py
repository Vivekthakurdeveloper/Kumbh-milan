"""MFA login challenges, TOTP replay guard, staff help-assistant idempotent answers.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-24

Additive only: existing users keep totp_enabled as it was, and accounts without MFA log in unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import grant_tables

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_last_used_step", sa.BigInteger(), nullable=True))

    op.create_table(
        "mfa_login_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("login_key", sa.String(length=64), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_reason", sa.String(length=32), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_mfa_login_challenges_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_mfa_login_challenges_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_login_challenges")),
    )
    op.create_index("ix_mfa_login_challenges_user_created", "mfa_login_challenges", ["user_id", "created_at"])
    op.create_index("ix_mfa_login_challenges_expires_at", "mfa_login_challenges", ["expires_at"])

    op.create_table(
        "assistant_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column(
            "sources", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_assistant_answers_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_answers")),
    )
    op.create_index("ix_assistant_answers_created_at", "assistant_answers", ["created_at"])

    grant_tables("mfa_login_challenges", "assistant_answers")


def downgrade() -> None:
    op.drop_table("assistant_answers")
    op.drop_table("mfa_login_challenges")
    op.drop_column("users", "totp_last_used_step")
