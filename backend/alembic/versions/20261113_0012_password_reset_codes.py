"""Replace opaque PUBLIC reset tokens with one-time email/SMS codes.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-13

Outstanding reset tokens are intentionally invalidated because the old opaque-token
format is not compatible with the new short-code verification flow.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_active_hash", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_active", table_name="password_reset_tokens")
    op.drop_constraint(
        "ck_password_reset_tokens_hash_format",
        "password_reset_tokens",
        type_="check",
    )

    op.add_column("password_reset_tokens", sa.Column("channel", sa.String(length=8), nullable=False))
    op.add_column("password_reset_tokens", sa.Column("code_hash", sa.String(length=64), nullable=False))
    op.add_column(
        "password_reset_tokens",
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("password_reset_tokens", sa.Column("consumed_reason", sa.String(length=32), nullable=True))
    op.drop_column("password_reset_tokens", "token_hash")

    op.create_check_constraint(
        "ck_password_reset_tokens_attempts_non_negative",
        "password_reset_tokens",
        "attempts >= 0",
    )
    op.create_check_constraint(
        "ck_password_reset_tokens_channel_valid",
        "password_reset_tokens",
        "channel IN ('EMAIL', 'SMS')",
    )
    op.create_index(
        "ix_password_reset_tokens_user_created",
        "password_reset_tokens",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_created", table_name="password_reset_tokens")
    op.drop_constraint(
        "ck_password_reset_tokens_channel_valid",
        "password_reset_tokens",
        type_="check",
    )
    op.drop_constraint(
        "ck_password_reset_tokens_attempts_non_negative",
        "password_reset_tokens",
        type_="check",
    )

    op.add_column("password_reset_tokens", sa.Column("token_hash", sa.String(length=64), nullable=False))
    op.drop_column("password_reset_tokens", "consumed_reason")
    op.drop_column("password_reset_tokens", "attempts")
    op.drop_column("password_reset_tokens", "code_hash")
    op.drop_column("password_reset_tokens", "channel")

    op.create_check_constraint(
        "ck_password_reset_tokens_hash_format",
        "password_reset_tokens",
        "length(btrim(token_hash)) = 64",
    )
    op.create_index(
        "ix_password_reset_tokens_user_active",
        "password_reset_tokens",
        ["user_id", "consumed_at"],
    )
    op.create_index(
        "ix_password_reset_tokens_active_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
