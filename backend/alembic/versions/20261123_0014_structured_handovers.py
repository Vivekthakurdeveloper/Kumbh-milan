"""Add structured claimant verification and handover records.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23

Sensitive claimant, witness, verification, acknowledgement, and rejection
values are encrypted by the application before insert. Lifecycle actions are
also written to the existing append-only audit log.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_support import app_role, grant_tables

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_handovers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("counterpart_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "PERSON_REUNIFICATION",
                "ITEM_RETURN",
                name="handover_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "CLAIMANT_RECORDED",
                "VERIFIED",
                "COMPLETED",
                "REJECTED",
                name="handover_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="CLAIMANT_RECORDED",
            nullable=False,
        ),
        sa.Column("claimant_ciphertext", sa.Text(), nullable=True),
        sa.Column(
            "identity_document_type",
            sa.Enum(
                "AADHAAR",
                "VOTER_ID",
                "PASSPORT",
                "DRIVING_LICENCE",
                "OTHER",
                name="handover_identity_document_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("witness_ciphertext", sa.Text(), nullable=True),
        sa.Column(
            "evidence_attachment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "verification_method",
            sa.Enum(
                "GOVERNMENT_ID",
                "FAMILY_CORROBORATION",
                "PHOTO_OR_MARKS",
                "OTHER",
                name="handover_verification_method",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("verification_ciphertext", sa.Text(), nullable=True),
        sa.Column("verified_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handover_location", sa.String(length=500), nullable=True),
        sa.Column("handover_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledgement_ciphertext", sa.Text(), nullable=True),
        sa.Column("completed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_ciphertext", sa.Text(), nullable=True),
        sa.Column("rejected_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "counterpart_case_id IS NULL OR counterpart_case_id <> primary_case_id",
            name=op.f("ck_case_handovers_different_counterpart"),
        ),
        sa.CheckConstraint(
            "kind <> 'PERSON_REUNIFICATION' OR match_id IS NOT NULL",
            name=op.f("ck_case_handovers_person_reunification_requires_match"),
        ),
        sa.CheckConstraint(
            "claimant_ciphertext IS NOT NULL OR anonymized_at IS NOT NULL",
            name=op.f("ck_case_handovers_claimant_or_anonymized"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('VERIFIED', 'COMPLETED') "
            "OR anonymized_at IS NOT NULL "
            "OR (verified_by_user_id IS NOT NULL AND verified_at IS NOT NULL "
            "AND verification_method IS NOT NULL AND verification_ciphertext IS NOT NULL)",
            name=op.f("ck_case_handovers_verified_fields_present"),
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' "
            "OR anonymized_at IS NOT NULL "
            "OR (completed_by_user_id IS NOT NULL AND completed_at IS NOT NULL "
            "AND handover_at IS NOT NULL AND handover_location IS NOT NULL "
            "AND acknowledgement_ciphertext IS NOT NULL)",
            name=op.f("ck_case_handovers_completed_fields_present"),
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' "
            "OR anonymized_at IS NOT NULL "
            "OR (rejected_by_user_id IS NOT NULL AND rejected_at IS NOT NULL "
            "AND rejection_ciphertext IS NOT NULL)",
            name=op.f("ck_case_handovers_rejected_fields_present"),
        ),
        sa.ForeignKeyConstraint(
            ["primary_case_id"],
            ["cases.id"],
            name=op.f("fk_case_handovers_primary_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["counterpart_case_id"],
            ["cases.id"],
            name=op.f("fk_case_handovers_counterpart_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["case_matches.id"],
            name=op.f("fk_case_handovers_match_id_case_matches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_handovers_verified_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_handovers_completed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_handovers_rejected_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_handovers_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_handovers")),
    )
    op.create_index(
        "ix_case_handovers_primary_created",
        "case_handovers",
        ["primary_case_id", "created_at", "id"],
    )
    op.create_index(
        "ix_case_handovers_counterpart_created",
        "case_handovers",
        ["counterpart_case_id", "created_at", "id"],
    )
    op.create_index("ix_case_handovers_match", "case_handovers", ["match_id"])
    op.create_index(
        "uq_case_handovers_active_primary",
        "case_handovers",
        ["primary_case_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CLAIMANT_RECORDED', 'VERIFIED')"),
    )
    op.create_index(
        "uq_case_handovers_active_match",
        "case_handovers",
        ["match_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('CLAIMANT_RECORDED', 'VERIFIED') AND match_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_case_handovers_completed_match",
        "case_handovers",
        ["match_id"],
        unique=True,
        postgresql_where=sa.text("status = 'COMPLETED' AND match_id IS NOT NULL"),
    )
    op.create_index(
        "uq_case_handovers_completed_primary_without_match",
        "case_handovers",
        ["primary_case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'COMPLETED' AND match_id IS NULL"),
    )

    grant_tables("case_handovers")
    role = app_role()
    if role is not None:
        op.execute(f'REVOKE DELETE ON case_handovers FROM "{role}"')


def downgrade() -> None:
    op.drop_table("case_handovers")
