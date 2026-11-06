"""Face embeddings, FACE match source, two-person approval fields, staff notifications.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.database.migration_support import enum_check, grant_tables, replace_check

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "face_embeddings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("attachment_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("face_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("detection_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("embedding_ciphertext", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'FACE_FOUND') = (embedding_ciphertext IS NOT NULL)",
            name="face_found_requires_embedding",
        ),
        sa.CheckConstraint("face_count >= 0", name="face_count_non_negative"),
        sa.CheckConstraint(
            "detection_score IS NULL OR (detection_score >= 0 AND detection_score <= 1)",
            name="detection_score_range",
        ),
        sa.CheckConstraint(
            enum_check("case_type", ["MISSING_PERSON", "FOUND_PERSON", "LOST_ITEM", "FOUND_ITEM"]),
            name="face_case_type",
        ),
        sa.CheckConstraint(
            enum_check("status", ["FACE_FOUND", "NO_FACE", "MULTIPLE_FACES", "UNREADABLE"]),
            name="face_status",
        ),
        sa.ForeignKeyConstraint(["attachment_id"], ["case_attachments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attachment_id"),
    )
    op.create_index("ix_face_embeddings_case_type_status", "face_embeddings", ["case_type", "status"])
    op.create_index("ix_face_embeddings_case_id", "face_embeddings", ["case_id"])

    op.add_column("case_matches", sa.Column("left_attachment_id", sa.UUID(), nullable=True))
    op.add_column("case_matches", sa.Column("right_attachment_id", sa.UUID(), nullable=True))
    op.add_column("case_matches", sa.Column("face_similarity", sa.Numeric(precision=5, scale=4), nullable=True))
    op.add_column("case_matches", sa.Column("face_model", sa.String(length=64), nullable=True))
    op.add_column("case_matches", sa.Column("first_approved_by_user_id", sa.UUID(), nullable=True))
    op.add_column("case_matches", sa.Column("first_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_matches", sa.Column("first_approval_evidence", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_case_matches_left_attachment",
        "case_matches",
        "case_attachments",
        ["left_attachment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_case_matches_right_attachment",
        "case_matches",
        "case_attachments",
        ["right_attachment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_case_matches_first_approved_by",
        "case_matches",
        "users",
        ["first_approved_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    replace_check(
        "case_matches",
        "ck_case_matches_match_source",
        enum_check("source", ["MANUAL", "AUTOMATIC", "FACE"]),
    )
    op.create_check_constraint(
        "face_matches_are_scored",
        "case_matches",
        "source <> 'FACE' OR ("
        "face_similarity IS NOT NULL AND left_attachment_id IS NOT NULL AND right_attachment_id IS NOT NULL "
        "AND face_model IS NOT NULL AND score IS NOT NULL)",
    )
    op.create_check_constraint(
        "face_similarity_range",
        "case_matches",
        "face_similarity IS NULL OR (face_similarity >= 0 AND face_similarity <= 1)",
    )
    op.create_check_constraint(
        "face_two_person_approval",
        "case_matches",
        "first_approved_by_user_id IS NULL OR first_approved_by_user_id IS DISTINCT FROM reviewed_by_user_id",
    )
    op.create_check_constraint(
        "first_approval_fields_together",
        "case_matches",
        "(first_approved_by_user_id IS NULL) = (first_approved_at IS NULL)",
    )

    op.create_table(
        "staff_notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipient_user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="UNREAD", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'READ') = (read_at IS NOT NULL)",
            name="staff_notification_read_at_matches_status",
        ),
        sa.CheckConstraint(enum_check("kind", ["MATCH_CANDIDATE"]), name="staff_notification_kind"),
        sa.CheckConstraint(enum_check("status", ["UNREAD", "READ"]), name="staff_notification_status"),
        sa.ForeignKeyConstraint(["match_id"], ["case_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recipient_user_id", "match_id", "kind", name="uq_staff_notifications_recipient_match_kind"
        ),
    )
    op.create_index(
        "ix_staff_notifications_recipient_created", "staff_notifications", ["recipient_user_id", "created_at"]
    )
    op.create_index(
        "ix_staff_notifications_recipient_unread",
        "staff_notifications",
        ["recipient_user_id"],
        postgresql_where=sa.text("status = 'UNREAD'"),
    )

    grant_tables("face_embeddings", "staff_notifications")


def downgrade() -> None:
    op.drop_table("staff_notifications")
    op.drop_constraint("first_approval_fields_together", "case_matches", type_="check")
    op.drop_constraint("face_two_person_approval", "case_matches", type_="check")
    op.drop_constraint("face_similarity_range", "case_matches", type_="check")
    op.drop_constraint("face_matches_are_scored", "case_matches", type_="check")
    replace_check("case_matches", "ck_case_matches_match_source", enum_check("source", ["MANUAL", "AUTOMATIC"]))
    op.drop_constraint("fk_case_matches_first_approved_by", "case_matches", type_="foreignkey")
    op.drop_constraint("fk_case_matches_right_attachment", "case_matches", type_="foreignkey")
    op.drop_constraint("fk_case_matches_left_attachment", "case_matches", type_="foreignkey")
    op.drop_column("case_matches", "first_approval_evidence")
    op.drop_column("case_matches", "first_approved_at")
    op.drop_column("case_matches", "first_approved_by_user_id")
    op.drop_column("case_matches", "face_model")
    op.drop_column("case_matches", "face_similarity")
    op.drop_column("case_matches", "right_attachment_id")
    op.drop_column("case_matches", "left_attachment_id")
    op.drop_index("ix_face_embeddings_case_id", table_name="face_embeddings")
    op.drop_index("ix_face_embeddings_case_type_status", table_name="face_embeddings")
    op.drop_table("face_embeddings")
