"""Add public registration profile fields and normalized mobile uniqueness.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-21

Existing usernames and phone numbers are deliberately left unchanged. The partial
functional index treats legacy E.164 and new ten-digit PUBLIC phone values with
the same final ten digits as one registration identity.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NORMALIZED_MOBILE_SQL = "right(regexp_replace(phone_number, '[^0-9]', '', 'g'), 10)"


def upgrade() -> None:
    duplicate_count = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT count(*)
            FROM (
                SELECT {_NORMALIZED_MOBILE_SQL}
                FROM users
                WHERE role = 'PUBLIC' AND phone_number IS NOT NULL
                GROUP BY 1
                HAVING count(*) > 1
            ) AS duplicate_public_mobiles
            """
        )
    )
    if duplicate_count:
        raise RuntimeError(
            "Cannot enforce PUBLIC mobile uniqueness: duplicate normalized mobile numbers exist"
        )

    op.add_column("users", sa.Column("first_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("middle_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("address", sa.String(length=500), nullable=True))
    op.create_index(
        "uq_users_public_mobile_normalized",
        "users",
        [sa.text(_NORMALIZED_MOBILE_SQL)],
        unique=True,
        postgresql_where=sa.text("role = 'PUBLIC' AND phone_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_public_mobile_normalized", table_name="users")
    op.drop_column("users", "address")
    op.drop_column("users", "last_name")
    op.drop_column("users", "middle_name")
    op.drop_column("users", "first_name")
