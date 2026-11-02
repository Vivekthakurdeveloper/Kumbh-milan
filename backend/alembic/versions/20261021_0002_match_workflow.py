"""match workflow: case versions, automatic/manual match sources, revocation, one confirmed match per case

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21

- cases.version: optimistic-concurrency counter (SQLAlchemy version_id_col).
- case_matches: source, score, confidence band, reasons, engine version, revocation fields;
  proposer becomes nullable for AUTOMATIC suggestions.
- Partial unique indexes: a case can have at most one CONFIRMED match.
- Default privileges so tables created by later revisions are granted to the runtime role.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import app_role, enum_check, replace_check

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False))

    op.add_column(
        "case_matches",
        sa.Column("source", sa.String(length=32), server_default="MANUAL", nullable=False),
    )
    op.add_column("case_matches", sa.Column("score", sa.Numeric(5, 4), nullable=True))
    op.add_column("case_matches", sa.Column("confidence_band", sa.String(length=32), nullable=True))
    op.add_column("case_matches", sa.Column("reasons", postgresql.JSONB(), nullable=True))
    op.add_column("case_matches", sa.Column("engine_version", sa.String(length=32), nullable=True))
    op.add_column("case_matches", sa.Column("revoked_by_user_id", sa.UUID(), nullable=True))
    op.add_column("case_matches", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_matches", sa.Column("revocation_reason", sa.Text(), nullable=True))
    op.alter_column("case_matches", "proposed_by_user_id", existing_type=sa.UUID(), nullable=True)
    op.create_foreign_key(
        op.f("fk_case_matches_revoked_by_user_id_users"), "case_matches", "users",
        ["revoked_by_user_id"], ["id"], ondelete="RESTRICT",
    )

    replace_check("case_matches", "ck_case_matches_match_status",
                  enum_check("status", ["PROPOSED", "CONFIRMED", "REJECTED", "REVOKED"]))
    replace_check("case_matches", "ck_case_matches_match_source", enum_check("source", ["MANUAL", "AUTOMATIC"]))
    replace_check("case_matches", "ck_case_matches_confidence_band",
                  enum_check("confidence_band", ["HIGH", "MEDIUM", "LOW"]))
    replace_check("case_matches", "ck_case_matches_manual_matches_have_proposer",
                  "(source = 'MANUAL') = (proposed_by_user_id IS NOT NULL)")
    replace_check("case_matches", "ck_case_matches_automatic_matches_are_scored",
                  "source <> 'AUTOMATIC' OR (score IS NOT NULL AND confidence_band IS NOT NULL AND engine_version IS NOT NULL)")
    replace_check("case_matches", "ck_case_matches_score_range", "score IS NULL OR (score >= 0 AND score <= 1)")
    replace_check("case_matches", "ck_case_matches_revocation_fields_match_status",
                  "(status = 'REVOKED') = (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)")

    op.create_index("ix_case_matches_status_created", "case_matches", ["status", "created_at"])
    op.create_index("uq_case_matches_confirmed_left", "case_matches", ["left_case_id"], unique=True,
                    postgresql_where=sa.text("status = 'CONFIRMED'"))
    op.create_index("uq_case_matches_confirmed_right", "case_matches", ["right_case_id"], unique=True,
                    postgresql_where=sa.text("status = 'CONFIRMED'"))

    role = app_role()
    if role:
        op.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"'
        )
        op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "{role}"')


def downgrade() -> None:
    role = app_role()
    if role:
        op.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{role}"'
        )
        op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM "{role}"')
    op.drop_index("uq_case_matches_confirmed_right", table_name="case_matches")
    op.drop_index("uq_case_matches_confirmed_left", table_name="case_matches")
    op.drop_index("ix_case_matches_status_created", table_name="case_matches")
    for name in (
        "ck_case_matches_revocation_fields_match_status",
        "ck_case_matches_score_range",
        "ck_case_matches_automatic_matches_are_scored",
        "ck_case_matches_manual_matches_have_proposer",
        "ck_case_matches_confidence_band",
        "ck_case_matches_match_source",
    ):
        op.execute(f"ALTER TABLE case_matches DROP CONSTRAINT IF EXISTS {name}")
    op.execute("DELETE FROM case_matches WHERE source = 'AUTOMATIC' OR status = 'REVOKED'")
    replace_check("case_matches", "ck_case_matches_match_status",
                  enum_check("status", ["PROPOSED", "CONFIRMED", "REJECTED"]))
    op.drop_constraint(op.f("fk_case_matches_revoked_by_user_id_users"), "case_matches", type_="foreignkey")
    op.alter_column("case_matches", "proposed_by_user_id", existing_type=sa.UUID(), nullable=False)
    for column in ("revocation_reason", "revoked_at", "revoked_by_user_id", "engine_version", "reasons",
                   "confidence_band", "score", "source"):
        op.drop_column("case_matches", column)
    op.drop_column("cases", "version")
