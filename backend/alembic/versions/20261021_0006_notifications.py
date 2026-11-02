"""notification outbox

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import enum_check, grant_tables

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("recipient_ciphertext", sa.Text(), nullable=True),
        sa.Column("recipient_hint", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sending_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("consent_statement", sa.Text(), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(enum_check("template", ["CASE_REGISTERED", "CASE_UPDATE_VISIT_DESK"]),
                           name=op.f("ck_notifications_notification_template")),
        sa.CheckConstraint(enum_check("channel", ["SMS", "WHATSAPP"]), name=op.f("ck_notifications_notification_channel")),
        sa.CheckConstraint(enum_check("status", ["PENDING", "SENDING", "SENT", "FAILED", "CANCELLED"]),
                           name=op.f("ck_notifications_notification_status")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_notifications_attempts_non_negative")),
        sa.CheckConstraint("(status = 'SENT') = (sent_at IS NOT NULL)", name=op.f("ck_notifications_sent_at_matches_status")),
        sa.CheckConstraint("length(btrim(consent_statement)) > 0", name=op.f("ck_notifications_consent_recorded")),
        sa.CheckConstraint("recipient_ciphertext IS NOT NULL OR status IN ('SENT', 'FAILED', 'CANCELLED')",
                           name=op.f("ck_notifications_pending_has_recipient")),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_notifications_case_id_cases"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"],
                                name=op.f("fk_notifications_requested_by_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_notifications_idempotency_key")),
    )
    op.create_index("ix_notifications_queue", "notifications", ["next_attempt_at"],
                    postgresql_where=sa.text("status IN ('PENDING', 'SENDING')"))
    op.create_index("ix_notifications_case_created", "notifications", ["case_id", "created_at"])
    grant_tables("notifications")


def downgrade() -> None:
    op.drop_index("ix_notifications_case_created", table_name="notifications")
    op.drop_index("ix_notifications_queue", table_name="notifications")
    op.drop_table("notifications")
