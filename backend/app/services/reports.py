"""Bounded, station-scoped operational exports. Rows never include contact details or notes."""

import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import ValidationFailed
from app.models import Case, User

EXPORT_LIMIT = 1000
MAX_RANGE = timedelta(days=31)


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text[:1] in "=+-@":
        return "'" + text
    return text


def export_cases(db: Session, actor: User, created_from: datetime, created_to: datetime) -> tuple[str, int, bool]:
    require_permission(actor, Permission.CASE_READ)
    if created_to <= created_from:
        raise ValidationFailed("created_to must be after created_from", code="invalid_range")
    if created_to - created_from > MAX_RANGE:
        raise ValidationFailed("Export range cannot exceed 31 days", code="range_too_large")
    rows = db.execute(
        select(
            Case.case_number, Case.case_type, Case.status, Case.station_id, Case.urgency_level, Case.created_at,
        )
        .where(
            case_visibility_clause(actor),
            Case.archived_at.is_(None),
            Case.created_at >= created_from,
            Case.created_at < created_to,
        )
        .order_by(Case.created_at, Case.id)
        .limit(EXPORT_LIMIT + 1)
    ).all()
    truncated = len(rows) > EXPORT_LIMIT
    rows = rows[:EXPORT_LIMIT]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["case_number", "case_type", "status", "station_id", "urgency_level", "created_at"])
    for row in rows:
        writer.writerow([
            _cell(row.case_number), _cell(row.case_type), _cell(row.status), _cell(row.station_id),
            _cell(row.urgency_level), row.created_at.isoformat() if row.created_at else "",
        ])
    record_audit(
        db, action=AuditAction.CASE_EXPORTED, entity_type="report", entity_id=None, actor_user_id=actor.id,
        after={"rows": len(rows), "truncated": truncated},
    )
    db.commit()
    return buffer.getvalue(), len(rows), truncated
