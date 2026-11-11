"""Public citizen portal, OTP, sightings, push tokens, and expanded notifications.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-07

Additive only. Existing staff cases keep source=STAFF and null public_review_status.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import enum_check, grant_tables, replace_check

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_sightings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("case_number_submitted", sa.String(length=32), nullable=True),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sighting_location", sa.String(length=500), nullable=False),
        sa.Column("sighting_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reporter_mobile_ciphertext", sa.Text(), nullable=False),
        sa.Column("reporter_mobile_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column("reporter_mobile_hint", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="UNVERIFIED"),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(enum_check("status", ["UNVERIFIED", "REVIEWED", "DISMISSED"]),
                           name="ck_public_sightings_sighting_status"),
        sa.CheckConstraint("length(btrim(description)) > 0", name="ck_public_sightings_description_not_blank"),
        sa.CheckConstraint("length(btrim(sighting_location)) > 0", name="ck_public_sightings_location_not_blank"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_public_sightings_case_created", "public_sightings", ["case_id", "created_at"])
    op.create_index(
        "ix_public_sightings_station_status_created",
        "public_sightings",
        ["station_id", "status", "created_at"],
    )
    op.create_index("ix_public_sightings_mobile_hash", "public_sightings", ["reporter_mobile_lookup_hash"])
    grant_tables("public_sightings")

    op.create_table(
        "otp_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("mobile_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column("mobile_hint", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("case_number", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("locale", sa.String(length=8), server_default="hi", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            enum_check("purpose", ["REPORT_MISSING", "REPORT_SIGHTING", "CASE_STATUS"]),
            name="ck_otp_challenges_otp_purpose",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_otp_challenges_attempts_non_negative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_otp_challenges_max_attempts_positive"),
        sa.CheckConstraint("length(btrim(code_hash)) = 64", name="ck_otp_challenges_code_hash_format"),
        sa.CheckConstraint(
            "length(btrim(mobile_lookup_hash)) = 64", name="ck_otp_challenges_mobile_hash_format"
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_otp_challenges_mobile_purpose_created",
        "otp_challenges",
        ["mobile_lookup_hash", "purpose", "created_at"],
    )
    op.create_index(
        "ix_otp_challenges_active",
        "otp_challenges",
        ["mobile_lookup_hash", "purpose"],
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    grant_tables("otp_challenges")

    op.create_table(
        "push_device_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("token_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivation_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(enum_check("platform", ["ANDROID", "IOS"]), name="ck_push_device_tokens_push_platform"),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND case_id IS NULL) OR (user_id IS NULL AND case_id IS NOT NULL)",
            name="ck_push_device_tokens_owner",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_lookup_hash", name="uq_push_device_tokens_token_hash"),
    )
    op.create_index("ix_push_device_tokens_user_active", "push_device_tokens", ["user_id", "is_active"])
    op.create_index("ix_push_device_tokens_case_active", "push_device_tokens", ["case_id", "is_active"])
    grant_tables("push_device_tokens")

    op.create_table(
        "public_information_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title_en", sa.String(length=200), nullable=False),
        sa.Column("title_hi", sa.String(length=200), nullable=False),
        sa.Column("body_en", sa.Text(), nullable=False),
        sa.Column("body_hi", sa.Text(), nullable=False),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("location_text", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unpublished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            enum_check("kind", ["HELP_DESK", "EMERGENCY_CONTACT", "INSTRUCTION", "ANNOUNCEMENT"]),
            name="ck_public_information_items_public_information_kind",
        ),
        sa.CheckConstraint("length(btrim(title_en)) > 0", name="ck_public_information_title_en"),
        sa.CheckConstraint("length(btrim(title_hi)) > 0", name="ck_public_information_title_hi"),
        sa.CheckConstraint("length(btrim(body_en)) > 0", name="ck_public_information_body_en"),
        sa.CheckConstraint("length(btrim(body_hi)) > 0", name="ck_public_information_body_hi"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_public_information_kind_published",
        "public_information_items",
        ["kind", "is_published", "sort_order"],
    )
    grant_tables("public_information_items")

    op.add_column(
        "cases",
        sa.Column("source", sa.String(length=32), server_default="STAFF", nullable=False),
    )
    op.add_column("cases", sa.Column("public_review_status", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_cases_case_source",
        "cases",
        enum_check("source", ["STAFF", "PUBLIC"]),
    )
    op.create_check_constraint(
        "ck_cases_public_review_status",
        "cases",
        "public_review_status IS NULL OR public_review_status IN "
        "('PENDING_VERIFICATION', 'VERIFIED', 'REJECTED')",
    )
    op.create_check_constraint(
        "ck_cases_public_review_matches_source",
        "cases",
        "(source = 'PUBLIC') = (public_review_status IS NOT NULL)",
    )
    op.create_index("ix_cases_source_public_review", "cases", ["source", "public_review_status"])

    op.add_column("case_contacts", sa.Column("reporter_name", sa.String(length=200), nullable=True))
    op.add_column("case_contacts", sa.Column("mobile_lookup_hash", sa.String(length=64), nullable=True))
    op.add_column("case_contacts", sa.Column("mobile_hint", sa.String(length=16), nullable=True))
    op.add_column("case_contacts", sa.Column("mobile_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_contacts", sa.Column("email_ciphertext", sa.Text(), nullable=True))
    op.add_column("case_contacts", sa.Column("email_hint", sa.String(length=64), nullable=True))
    op.add_column("case_contacts", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_contacts", sa.Column("preferred_locale", sa.String(length=8), nullable=True))
    op.add_column("case_contacts", sa.Column("notification_consent_statement", sa.Text(), nullable=True))
    op.add_column("case_contacts", sa.Column("notification_consent_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("case_contacts", "updated_by_user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_index("ix_case_contacts_mobile_lookup_hash", "case_contacts", ["mobile_lookup_hash"])

    op.add_column("case_attachments", sa.Column("sighting_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.alter_column("case_attachments", "case_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column(
        "case_attachments", "uploaded_by_user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True
    )
    op.create_foreign_key(
        "fk_case_attachments_sighting_id_public_sightings",
        "case_attachments",
        "public_sightings",
        ["sighting_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_case_attachments_exclusive_parent",
        "case_attachments",
        "(case_id IS NOT NULL AND sighting_id IS NULL) OR (case_id IS NULL AND sighting_id IS NOT NULL)",
    )
    op.create_index("ix_case_attachments_sighting_created", "case_attachments", ["sighting_id", "created_at"])

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
        "notifications",
        "ck_notifications_notification_channel",
        enum_check("channel", ["SMS", "WHATSAPP", "EMAIL", "PUSH"]),
    )
    op.alter_column(
        "notifications", "requested_by_user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True
    )
    # Email hints (e.g. f••••@example.com) exceed the original phone-oriented 16-char width.
    op.alter_column(
        "notifications",
        "recipient_hint",
        existing_type=sa.String(length=16),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.add_column("notifications", sa.Column("subject", sa.String(length=200), nullable=True))
    op.add_column("notifications", sa.Column("device_token_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_notifications_device_token_id_push_device_tokens",
        "notifications",
        "push_device_tokens",
        ["device_token_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS fk_notifications_device_token_id_push_device_tokens"
    )
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS device_token_id")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS subject")
    op.alter_column(
        "notifications",
        "recipient_hint",
        existing_type=sa.String(length=64),
        type_=sa.String(length=16),
        existing_nullable=True,
    )
    op.alter_column(
        "notifications", "requested_by_user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False
    )
    replace_check(
        "notifications",
        "ck_notifications_notification_channel",
        enum_check("channel", ["SMS", "WHATSAPP"]),
    )
    replace_check(
        "notifications",
        "ck_notifications_notification_template",
        enum_check("template", ["CASE_REGISTERED", "CASE_UPDATE_VISIT_DESK"]),
    )

    op.execute("DROP INDEX IF EXISTS ix_case_attachments_sighting_created")
    op.execute("ALTER TABLE case_attachments DROP CONSTRAINT IF EXISTS ck_case_attachments_exclusive_parent")
    op.execute(
        "ALTER TABLE case_attachments DROP CONSTRAINT IF EXISTS fk_case_attachments_sighting_id_public_sightings"
    )
    op.execute("ALTER TABLE case_attachments DROP COLUMN IF EXISTS sighting_id")
    op.alter_column(
        "case_attachments", "uploaded_by_user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False
    )
    op.alter_column("case_attachments", "case_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.drop_index("ix_case_contacts_mobile_lookup_hash", table_name="case_contacts")
    op.alter_column("case_contacts", "updated_by_user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    for column in (
        "notification_consent_at",
        "notification_consent_statement",
        "preferred_locale",
        "email_verified_at",
        "email_hint",
        "email_ciphertext",
        "mobile_verified_at",
        "mobile_hint",
        "mobile_lookup_hash",
        "reporter_name",
    ):
        op.drop_column("case_contacts", column)

    op.drop_index("ix_cases_source_public_review", table_name="cases")
    op.drop_constraint("ck_cases_public_review_matches_source", "cases", type_="check")
    op.drop_constraint("ck_cases_public_review_status", "cases", type_="check")
    op.drop_constraint("ck_cases_case_source", "cases", type_="check")
    op.drop_column("cases", "public_review_status")
    op.drop_column("cases", "source")

    op.drop_index("ix_public_information_kind_published", table_name="public_information_items")
    op.drop_table("public_information_items")
    op.drop_index("ix_push_device_tokens_case_active", table_name="push_device_tokens")
    op.drop_index("ix_push_device_tokens_user_active", table_name="push_device_tokens")
    op.drop_table("push_device_tokens")
    op.drop_index("ix_otp_challenges_active", table_name="otp_challenges")
    op.drop_index("ix_otp_challenges_mobile_purpose_created", table_name="otp_challenges")
    op.drop_table("otp_challenges")
    op.drop_index("ix_public_sightings_mobile_hash", table_name="public_sightings")
    op.drop_index("ix_public_sightings_station_status_created", table_name="public_sightings")
    op.drop_index("ix_public_sightings_case_created", table_name="public_sightings")
    op.drop_table("public_sightings")
