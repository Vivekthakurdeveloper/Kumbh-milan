"""Helpers shared by Alembic revisions. Kept import-light: only settings and SQLAlchemy."""

import os
import re

import sqlalchemy as sa
from alembic import op

APPEND_ONLY_TABLES = ("audit_events", "case_notes", "case_status_history")


def app_role() -> str | None:
    from app.config import get_settings

    role = (os.environ.get("KUMBH_APP_DB_ROLE") or get_settings().kumbh_app_db_role or "").strip()
    if not role:
        return None
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("KUMBH_APP_DB_ROLE must be a plain lowercase PostgreSQL identifier")
    exists = op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar()
    if not exists:
        raise RuntimeError(f"Role {role!r} does not exist; create it before migrating")
    return role


def grant_tables(*tables: str, append_only: bool = False) -> None:
    """DML for the runtime role. Append-only tables get SELECT and INSERT only."""
    role = app_role()
    if role is None:
        return
    privileges = "SELECT, INSERT" if append_only else "SELECT, INSERT, UPDATE, DELETE"
    for table in tables:
        op.execute(f'GRANT {privileges} ON {table} TO "{role}"')


def replace_check(table: str, name: str, condition: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition})")


def enum_check(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"
