"""Grievance attachments, grievance legal holds, grievance anonymisation, PDF reports, WhatsApp chat sessions.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-24

grievance_events stays append-only. The single exception mirrors revision 0005: while an approved
ANONYMIZE_CLOSED_GRIEVANCES run is EXECUTING and the transaction sets kumbh.retention_run_id to that run,
body_ciphertext may be cleared to NULL; every other column must stay identical. The runtime role has no
UPDATE privilege on grievance_events, so only the retention owner connection can reach this path.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.database.migration_support import enum_check, replace_check

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_POLICIES = ["ARCHIVE_CLOSED_CASES", "ANONYMIZE_ARCHIVED_CASES", "PURGE_ATTACHMENT_OBJECTS"]
NEW_POLICIES = [*OLD_POLICIES, "ANONYMIZE_CLOSED_GRIEVANCES"]
PARENT_CHECK = "ck_case_attachments_ck_case_attachments_exclusive_parent"
REPORT_FORMAT_COLUMNS = (("report_jobs", "report_format"), ("report_schedules", "schedule_report_format"))

EVENTS_FUNCTION = """
CREATE FUNCTION grievance_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    run_id text := current_setting('kumbh.retention_run_id', true);
    rotating boolean := coalesce(current_setting('kumbh.key_rotation', true), '') = 'on';
    same_metadata boolean;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        same_metadata := ROW(NEW.id, NEW.grievance_id, NEW.kind, NEW.from_status, NEW.to_status, NEW.actor_user_id,
                             NEW.assignee_user_id, NEW.is_internal, NEW.by_citizen, NEW.created_at)
                         IS NOT DISTINCT FROM
                         ROW(OLD.id, OLD.grievance_id, OLD.kind, OLD.from_status, OLD.to_status, OLD.actor_user_id,
                             OLD.assignee_user_id, OLD.is_internal, OLD.by_citizen, OLD.created_at);
        IF same_metadata AND NEW.body_ciphertext IS NULL AND coalesce(run_id, '') <> '' AND EXISTS (
            SELECT 1 FROM retention_runs r
            WHERE r.id::text = run_id AND r.status = 'EXECUTING' AND r.policy = 'ANONYMIZE_CLOSED_GRIEVANCES'
        ) THEN
            RETURN NEW;
        END IF;
        IF same_metadata AND rotating AND OLD.body_ciphertext IS NOT NULL AND NEW.body_ciphertext IS NOT NULL THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;
"""


def upgrade() -> None:
    op.add_column("case_attachments", sa.Column("grievance_id", sa.UUID(), nullable=True))
    op.add_column(
        "case_attachments",
        sa.Column("by_citizen", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_case_attachments_grievance_id_grievances"), "case_attachments", "grievances",
        ["grievance_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_case_attachments_grievance_created", "case_attachments", ["grievance_id", "created_at"])
    op.create_index(
        "uq_case_attachments_grievance_checksum", "case_attachments", ["grievance_id", "checksum_sha256"],
        unique=True, postgresql_where=sa.text("grievance_id IS NOT NULL AND deleted_at IS NULL"),
    )
    replace_check("case_attachments", PARENT_CHECK, "num_nonnulls(case_id, sighting_id, grievance_id) = 1")

    op.add_column("grievances", sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_column("grievances", "legal_hold")
    replace_check(
        "grievances", "ck_grievances_has_citizen",
        "citizen_user_id IS NOT NULL OR mobile_lookup_hash IS NOT NULL OR anonymized_at IS NOT NULL",
    )

    op.add_column("legal_holds", sa.Column("grievance_id", sa.UUID(), nullable=True))
    op.alter_column("legal_holds", "case_id", existing_type=sa.UUID(), nullable=True)
    op.create_foreign_key(
        op.f("fk_legal_holds_grievance_id_grievances"), "legal_holds", "grievances",
        ["grievance_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_legal_holds_exactly_one_subject"), "legal_holds", "(case_id IS NULL) <> (grievance_id IS NULL)"
    )
    op.create_index(
        "ix_legal_holds_active_grievance", "legal_holds", ["grievance_id"],
        postgresql_where=sa.text("released_at IS NULL"),
    )

    replace_check("retention_runs", "ck_retention_runs_retention_policy", enum_check("policy", NEW_POLICIES))

    op.execute(EVENTS_FUNCTION)
    op.execute("DROP TRIGGER IF EXISTS grievance_events_append_only ON grievance_events")
    op.execute(
        "CREATE TRIGGER grievance_events_append_only BEFORE UPDATE OR DELETE ON grievance_events "
        "FOR EACH ROW EXECUTE FUNCTION grievance_events_append_only()"
    )

    for table, name in REPORT_FORMAT_COLUMNS:
        op.add_column(table, sa.Column("format", sa.String(length=32), server_default="CSV", nullable=False))
        op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, enum_check("format", ["CSV", "PDF"]))

    op.add_column("chat_sessions", sa.Column("external_ref_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_chat_sessions_external_ref", "chat_sessions", ["external_ref_hash", "last_activity_at"])
    op.add_column("chat_messages", sa.Column("external_id", sa.String(length=128), nullable=True))
    op.create_index(
        "uq_chat_messages_external_id", "chat_messages", ["external_id"], unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_chat_messages_external_id", table_name="chat_messages")
    op.drop_column("chat_messages", "external_id")
    op.drop_index("ix_chat_sessions_external_ref", table_name="chat_sessions")
    op.drop_column("chat_sessions", "external_ref_hash")

    for table, _name in REPORT_FORMAT_COLUMNS:
        op.drop_column(table, "format")

    op.execute("DROP TRIGGER IF EXISTS grievance_events_append_only ON grievance_events")
    op.execute(
        "CREATE TRIGGER grievance_events_append_only BEFORE UPDATE OR DELETE ON grievance_events "
        "FOR EACH ROW EXECUTE FUNCTION forbid_append_only_mutation()"
    )
    op.execute("DROP FUNCTION IF EXISTS grievance_events_append_only()")

    op.execute("DELETE FROM retention_runs WHERE policy = 'ANONYMIZE_CLOSED_GRIEVANCES'")
    replace_check("retention_runs", "ck_retention_runs_retention_policy", enum_check("policy", OLD_POLICIES))

    op.execute("DELETE FROM legal_holds WHERE grievance_id IS NOT NULL")
    op.drop_index("ix_legal_holds_active_grievance", table_name="legal_holds")
    op.drop_constraint(op.f("ck_legal_holds_exactly_one_subject"), "legal_holds", type_="check")
    op.drop_constraint(op.f("fk_legal_holds_grievance_id_grievances"), "legal_holds", type_="foreignkey")
    op.alter_column("legal_holds", "case_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("legal_holds", "grievance_id")

    # Anonymisation is irreversible; a placeholder that no keyed hash can produce satisfies the old check.
    op.execute(
        "UPDATE grievances SET mobile_lookup_hash = repeat('0', 64) "
        "WHERE citizen_user_id IS NULL AND mobile_lookup_hash IS NULL"
    )
    replace_check(
        "grievances", "ck_grievances_has_citizen", "citizen_user_id IS NOT NULL OR mobile_lookup_hash IS NOT NULL"
    )
    op.add_column(
        "grievances", sa.Column("legal_hold", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.drop_column("grievances", "anonymized_at")

    op.execute("DELETE FROM case_attachments WHERE grievance_id IS NOT NULL")
    replace_check(
        "case_attachments", PARENT_CHECK,
        "(case_id IS NOT NULL AND sighting_id IS NULL) OR (case_id IS NULL AND sighting_id IS NOT NULL)",
    )
    op.drop_index("uq_case_attachments_grievance_checksum", table_name="case_attachments")
    op.drop_index("ix_case_attachments_grievance_created", table_name="case_attachments")
    op.drop_constraint(op.f("fk_case_attachments_grievance_id_grievances"), "case_attachments", type_="foreignkey")
    op.drop_column("case_attachments", "by_citizen")
    op.drop_column("case_attachments", "grievance_id")
