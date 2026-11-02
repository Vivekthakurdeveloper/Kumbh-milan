"""retention: legal holds, two-person retention runs, archive/anonymise markers, redaction-only trigger path

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21

case_notes and case_status_history stay append-only. The single exception: while an approved
ANONYMIZE_ARCHIVED_CASES run is EXECUTING and the transaction sets kumbh.retention_run_id to that run,
free-text columns may be replaced by the exact redaction marker for that run; every other column must stay
identical. The runtime role has no UPDATE privilege on these tables, so only the owner connection used by
the retention executor can reach this path. audit_events has no exception at all.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import enum_check, grant_tables

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICIES = ["ARCHIVE_CLOSED_CASES", "ANONYMIZE_ARCHIVED_CASES", "PURGE_ATTACHMENT_OBJECTS"]
STATUSES = ["PLANNED", "APPROVED", "EXECUTING", "EXECUTED", "FAILED", "CANCELLED"]

_ACTIVE_RUN = """
    run_id text := current_setting('kumbh.retention_run_id', true);
    marker text;
BEGIN
    IF TG_OP = 'UPDATE' AND coalesce(run_id, '') <> '' AND EXISTS (
        SELECT 1 FROM retention_runs r
        WHERE r.id::text = run_id AND r.status = 'EXECUTING' AND r.policy = 'ANONYMIZE_ARCHIVED_CASES'
    ) THEN
        marker := '[redacted by retention run ' || run_id || ']';
"""

NOTES_FUNCTION = f"""
CREATE FUNCTION case_notes_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE{_ACTIVE_RUN}
        IF NEW.note_text = marker
           AND ROW(NEW.id, NEW.case_id, NEW.author_user_id, NEW.note_type, NEW.is_sensitive, NEW.corrects_note_id, NEW.created_at)
               IS NOT DISTINCT FROM
               ROW(OLD.id, OLD.case_id, OLD.author_user_id, OLD.note_type, OLD.is_sensitive, OLD.corrects_note_id, OLD.created_at)
        THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;
"""

HISTORY_FUNCTION = f"""
CREATE FUNCTION case_status_history_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE{_ACTIVE_RUN}
        IF (NEW.reason IS NOT DISTINCT FROM OLD.reason OR NEW.reason = marker)
           AND (NEW.verification_evidence IS NOT DISTINCT FROM OLD.verification_evidence OR NEW.verification_evidence = marker)
           AND ROW(NEW.id, NEW.case_id, NEW.from_status, NEW.to_status, NEW.changed_by_user_id, NEW.match_id, NEW.created_at)
               IS NOT DISTINCT FROM
               ROW(OLD.id, OLD.case_id, OLD.from_status, OLD.to_status, OLD.changed_by_user_id, OLD.match_id, OLD.created_at)
        THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;
"""


def _user_fk(name: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], ["users.id"], name=op.f(f"fk_{name}_{column}_users"), ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "legal_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("placed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("released_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(released_at IS NULL) = (released_by_user_id IS NULL) AND (released_at IS NULL) = (release_reason IS NULL)",
            name=op.f("ck_legal_holds_release_fields_together"),
        ),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_legal_holds_reason_not_blank")),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_legal_holds_case_id_cases"), ondelete="RESTRICT"),
        _user_fk("legal_holds", "placed_by_user_id"),
        _user_fk("legal_holds", "released_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_holds")),
    )
    op.create_index(op.f("ix_legal_holds_case_id"), "legal_holds", ["case_id"])
    op.create_index("ix_legal_holds_active_case", "legal_holds", ["case_id"], postgresql_where=sa.text("released_at IS NULL"))

    op.create_table(
        "retention_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("execution_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("planned_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(enum_check("policy", POLICIES), name=op.f("ck_retention_runs_retention_policy")),
        sa.CheckConstraint(enum_check("status", STATUSES), name=op.f("ck_retention_runs_retention_run_status")),
        sa.CheckConstraint(
            "approved_by_user_id IS NULL OR approved_by_user_id <> planned_by_user_id",
            name=op.f("ck_retention_runs_two_person_approval"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('APPROVED', 'EXECUTING', 'EXECUTED', 'FAILED') OR approved_by_user_id IS NOT NULL",
            name=op.f("ck_retention_runs_executed_runs_were_approved"),
        ),
        sa.CheckConstraint(
            "status <> 'CANCELLED' OR cancelled_by_user_id IS NOT NULL", name=op.f("ck_retention_runs_cancellation_recorded")
        ),
        _user_fk("retention_runs", "planned_by_user_id"),
        _user_fk("retention_runs", "approved_by_user_id"),
        _user_fk("retention_runs", "executed_by_user_id"),
        _user_fk("retention_runs", "cancelled_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retention_runs")),
    )
    op.create_index("ix_retention_runs_status_created", "retention_runs", ["status", "created_at"])

    op.add_column("cases", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cases", sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_cases_anonymized_cases_are_archived"), "cases", "anonymized_at IS NULL OR archived_at IS NOT NULL"
    )
    op.create_check_constraint(
        op.f("ck_cases_only_finished_cases_archived"), "cases", "archived_at IS NULL OR status IN ('CLOSED', 'RESOLVED')"
    )

    op.execute(NOTES_FUNCTION)
    op.execute(HISTORY_FUNCTION)
    for table in ("case_notes", "case_status_history"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_append_only();"
        )
    grant_tables("legal_holds", "retention_runs")


def downgrade() -> None:
    for table in ("case_notes", "case_status_history"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_append_only_mutation();"
        )
        op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")
    op.drop_constraint(op.f("ck_cases_only_finished_cases_archived"), "cases", type_="check")
    op.drop_constraint(op.f("ck_cases_anonymized_cases_are_archived"), "cases", type_="check")
    op.drop_column("cases", "anonymized_at")
    op.drop_column("cases", "archived_at")
    op.drop_index("ix_retention_runs_status_created", table_name="retention_runs")
    op.drop_table("retention_runs")
    op.drop_index("ix_legal_holds_active_case", table_name="legal_holds")
    op.drop_index(op.f("ix_legal_holds_case_id"), table_name="legal_holds")
    op.drop_table("legal_holds")
