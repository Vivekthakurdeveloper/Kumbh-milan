"""Citizen grievances with staff workflow, SLA, and escalation (BE-005).

SLA due times are absolute hours from submission (or reopen); timestamps are stored in UTC and shown in
Asia/Kolkata by clients. No holiday calendar is applied until one is approved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import false, func, or_, select, text, true
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.crypto import decrypt_text, encrypt_text
from app.common.errors import Conflict, Forbidden, InvalidTransition, NotFound, ServiceUnavailable, ValidationFailed
from app.common.security_hash import keyed_hash
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseContact, Grievance, GrievanceEvent, Station, User
from app.models.enums import (
    GrievanceCategory,
    GrievanceEventKind,
    GrievanceStatus,
    Role,
    StaffNotificationKind,
    UrgencyLevel,
)
from app.notifications import mask
from app.services import staff_notifications
from app.services.cases import CASE_NUMBER_TZ

G = GrievanceStatus
OPEN_STATES = (G.OPEN, G.ASSIGNED, G.IN_PROGRESS, G.REOPENED)
TRANSITIONS: dict[GrievanceStatus, frozenset[GrievanceStatus]] = {
    G.OPEN: frozenset({G.IN_PROGRESS, G.RESOLVED, G.CLOSED}),
    G.ASSIGNED: frozenset({G.IN_PROGRESS, G.RESOLVED}),
    G.IN_PROGRESS: frozenset({G.RESOLVED}),
    G.RESOLVED: frozenset({G.CLOSED, G.REOPENED}),
    G.CLOSED: frozenset({G.REOPENED}),
    G.REOPENED: frozenset({G.IN_PROGRESS, G.RESOLVED}),
}
NOT_FOUND = "Grievance not found"


@dataclass(frozen=True)
class Citizen:
    """Either an authenticated PUBLIC account or a mobile number verified by OTP."""

    user_id: uuid.UUID | None = None
    mobile: str | None = None


def _sla() -> timedelta:
    return timedelta(hours=get_settings().grievance_sla_hours)


def _next_reference(db: Session) -> str:
    year = utcnow().astimezone(CASE_NUMBER_TZ).year
    value = db.execute(text("SELECT nextval('grievance_reference_seq')")).scalar_one()
    return f"GRV-{year}-{value:06d}"


def _citizen_clause(citizen: Citizen):
    clauses = []
    if citizen.user_id is not None:
        clauses.append(Grievance.citizen_user_id == citizen.user_id)
    if citizen.mobile is not None:
        clauses.append(Grievance.mobile_lookup_hash == keyed_hash(citizen.mobile))
    if not clauses:
        raise Forbidden("Citizen identity required", code="citizen_identity_required")
    return or_(*clauses)


def _resolve_station(db: Session, citizen: Citizen, case_number: str | None) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Routes to the related case's station only when the citizen is that case's reporter."""
    if case_number:
        case = db.scalar(select(Case).where(Case.case_number == case_number.strip().upper()))
        if case is not None:
            owns = citizen.user_id is not None and case.reported_by_user_id == citizen.user_id
            if not owns and citizen.mobile is not None:
                contact = db.get(CaseContact, case.id)
                owns = contact is not None and contact.mobile_lookup_hash == keyed_hash(citizen.mobile)
            if owns:
                return case.station_id, case.id
    station_id = get_settings().public_intake_station_id
    if station_id is None or db.get(Station, station_id) is None:
        raise ServiceUnavailable("Grievance intake is not configured", code="grievance_intake_unconfigured")
    return station_id, None


def submit(
    db: Session,
    citizen: Citizen,
    *,
    category: GrievanceCategory,
    subject: str,
    description: str,
    case_number: str | None,
    locale: str,
) -> Grievance:
    station_id, case_id = _resolve_station(db, citizen, case_number)
    now = utcnow()
    mobile = citizen.mobile
    if mobile is None and citizen.user_id is not None:
        user = db.get(User, citizen.user_id)
        mobile = user.phone_number if user is not None else None
    grievance = Grievance(
        id=uuid.uuid4(),
        reference=_next_reference(db),
        category=category,
        status=G.OPEN,
        priority=UrgencyLevel.HIGH if category is GrievanceCategory.STAFF_CONDUCT else UrgencyLevel.MEDIUM,
        station_id=station_id,
        case_id=case_id,
        subject=subject,
        description_ciphertext=encrypt_text(description),
        citizen_user_id=citizen.user_id,
        mobile_ciphertext=encrypt_text(mobile) if mobile else None,
        mobile_lookup_hash=keyed_hash(mobile) if citizen.mobile else None,
        mobile_hint=mask(mobile)[:16] if mobile else None,
        locale=locale,
        due_at=now + _sla(),
    )
    db.add(grievance)
    db.flush()
    db.add(GrievanceEvent(grievance_id=grievance.id, kind=GrievanceEventKind.CREATED, to_status=G.OPEN,
                          actor_user_id=citizen.user_id, by_citizen=True))
    record_audit(
        db, action=AuditAction.GRIEVANCE_SUBMITTED, entity_type="grievance", entity_id=grievance.id,
        actor_user_id=citizen.user_id,
        after={"reference": grievance.reference, "category": category, "station_id": station_id},
    )
    db.commit()
    db.refresh(grievance)
    return grievance


def citizen_list(db: Session, citizen: Citizen, *, limit: int, offset: int) -> tuple[list[Grievance], int]:
    clause = _citizen_clause(citizen)
    total = db.scalar(select(func.count()).select_from(Grievance).where(clause)) or 0
    rows = list(
        db.scalars(
            select(Grievance).where(clause).order_by(Grievance.created_at.desc(), Grievance.id).limit(limit).offset(offset)
        )
    )
    return rows, total


def citizen_get(db: Session, citizen: Citizen, reference: str) -> Grievance:
    row = db.scalar(
        select(Grievance).where(Grievance.reference == reference.strip().upper(), _citizen_clause(citizen))
    )
    if row is None:
        raise NotFound(NOT_FOUND, code="grievance_not_found")
    return row


def public_events(db: Session, grievance: Grievance) -> list[dict]:
    """Citizen-visible history: status changes and non-internal comments, without staff identities."""
    rows = db.scalars(
        select(GrievanceEvent)
        .where(GrievanceEvent.grievance_id == grievance.id, GrievanceEvent.is_internal.is_(False))
        .order_by(GrievanceEvent.created_at, GrievanceEvent.id)
    )
    events = []
    for row in rows:
        if row.kind in (GrievanceEventKind.ASSIGNED, GrievanceEventKind.ESCALATED):
            continue
        events.append(
            {
                "kind": row.kind,
                "to_status": row.to_status,
                "message": decrypt_text(row.body_ciphertext) if row.body_ciphertext else None,
                "by_citizen": row.by_citizen,
                "created_at": row.created_at,
            }
        )
    return events


def citizen_comment(db: Session, citizen: Citizen, reference: str, body: str) -> Grievance:
    row = citizen_get(db, citizen, reference)
    if row.status == G.CLOSED:
        raise Conflict("Closed grievances cannot receive comments; reopen it first", code="grievance_closed")
    db.add(GrievanceEvent(grievance_id=row.id, kind=GrievanceEventKind.COMMENT, actor_user_id=citizen.user_id,
                          body_ciphertext=encrypt_text(body), by_citizen=True))
    record_audit(db, action=AuditAction.GRIEVANCE_COMMENTED, entity_type="grievance", entity_id=row.id,
                 actor_user_id=citizen.user_id, after={"by_citizen": True})
    row.updated_at = func.now()
    db.commit()
    db.refresh(row)
    return row


def citizen_reopen(db: Session, citizen: Citizen, reference: str, reason: str) -> Grievance:
    row = citizen_get(db, citizen, reference)
    if row.status not in (G.RESOLVED, G.CLOSED):
        raise InvalidTransition("Only resolved or closed grievances can be reopened", code="invalid_transition")
    _transition(db, row, G.REOPENED, actor_id=citizen.user_id, message=reason, by_citizen=True)
    db.commit()
    db.refresh(row)
    return row


# --- Staff ---------------------------------------------------------------------------------------


def _scope(actor: User):
    if Role(actor.role) is Role.ADMIN:
        return true()
    if actor.station_id is None:
        return false()
    return or_(Grievance.station_id == actor.station_id, Grievance.assigned_to_user_id == actor.id)


def load(db: Session, actor: User, grievance_id: uuid.UUID, *, for_update: bool = False) -> Grievance:
    require_permission(actor, Permission.GRIEVANCE_READ)
    stmt = select(Grievance).where(Grievance.id == grievance_id, _scope(actor))
    if for_update:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound(NOT_FOUND, code="grievance_not_found")
    return row


def staff_list(
    db: Session,
    actor: User,
    *,
    status: list[GrievanceStatus] | None,
    category: GrievanceCategory | None,
    assigned_to_user_id: uuid.UUID | None,
    overdue: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[Grievance], int]:
    require_permission(actor, Permission.GRIEVANCE_READ)
    conditions = [_scope(actor)]
    if status:
        conditions.append(Grievance.status.in_(status))
    if category:
        conditions.append(Grievance.category == category)
    if assigned_to_user_id:
        conditions.append(Grievance.assigned_to_user_id == assigned_to_user_id)
    if overdue is True:
        conditions.extend([Grievance.status.in_(OPEN_STATES), Grievance.due_at < func.now()])
    elif overdue is False:
        conditions.append(or_(Grievance.status.not_in(OPEN_STATES), Grievance.due_at >= func.now()))
    total = db.scalar(select(func.count()).select_from(Grievance).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Grievance).where(*conditions).order_by(Grievance.due_at, Grievance.id).limit(limit).offset(offset)
        )
    )
    return rows, total


def staff_events(db: Session, grievance: Grievance) -> list[dict]:
    rows = db.scalars(
        select(GrievanceEvent).where(GrievanceEvent.grievance_id == grievance.id)
        .order_by(GrievanceEvent.created_at, GrievanceEvent.id)
    )
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "actor_user_id": row.actor_user_id,
            "assignee_user_id": row.assignee_user_id,
            "message": decrypt_text(row.body_ciphertext) if row.body_ciphertext else None,
            "is_internal": row.is_internal,
            "by_citizen": row.by_citizen,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def _transition(
    db: Session,
    row: Grievance,
    target: GrievanceStatus,
    *,
    actor_id: uuid.UUID | None,
    message: str | None,
    by_citizen: bool = False,
) -> None:
    previous = GrievanceStatus(row.status)
    if target not in TRANSITIONS[previous]:
        raise InvalidTransition(f"Cannot move a grievance from {previous} to {target}", code="invalid_transition")
    now = utcnow()
    row.status = target
    if target is G.RESOLVED:
        row.resolved_at = now
        if message:
            row.resolution_ciphertext = encrypt_text(message)
    if target is G.CLOSED:
        row.closed_at = now
    if target is G.REOPENED:
        row.closed_at = None
        row.resolved_at = None
        row.due_at = now + _sla()
        row.escalation_level = 0
    db.add(GrievanceEvent(
        grievance_id=row.id, kind=GrievanceEventKind.STATUS_CHANGED, from_status=previous, to_status=target,
        actor_user_id=actor_id, body_ciphertext=encrypt_text(message) if message else None, by_citizen=by_citizen,
    ))
    record_audit(
        db, action=AuditAction.GRIEVANCE_STATUS_CHANGED, entity_type="grievance", entity_id=row.id,
        actor_user_id=actor_id, before={"status": previous}, after={"status": target, "by_citizen": by_citizen},
    )


def change_status(
    db: Session, actor: User, grievance_id: uuid.UUID, target: GrievanceStatus, message: str | None,
    expected_version: int | None,
) -> Grievance:
    require_permission(actor, Permission.GRIEVANCE_MANAGE)
    row = load(db, actor, grievance_id, for_update=True)
    _check_version(row, expected_version)
    if target is G.ASSIGNED:
        raise ValidationFailed("Use the assign endpoint to assign a grievance", code="use_assign")
    if target is G.RESOLVED and not message:
        raise ValidationFailed("A resolution summary is required", code="resolution_required")
    if target is G.REOPENED and GrievanceStatus(row.status) is G.CLOSED and Role(actor.role) is not Role.ADMIN:
        raise Forbidden("Only administrators can reopen a closed grievance", code="permission_denied")
    _transition(db, row, target, actor_id=actor.id, message=message)
    db.commit()
    db.refresh(row)
    return row


def assign(
    db: Session, actor: User, grievance_id: uuid.UUID, assignee_user_id: uuid.UUID, reason: str | None,
    expected_version: int | None,
) -> Grievance:
    require_permission(actor, Permission.GRIEVANCE_MANAGE)
    row = load(db, actor, grievance_id, for_update=True)
    _check_version(row, expected_version)
    if row.status in (G.RESOLVED, G.CLOSED):
        raise Conflict("Resolved or closed grievances cannot be assigned", code="grievance_inactive")
    assignee = db.get(User, assignee_user_id)
    if assignee is None or not assignee.is_active or Role(assignee.role) is Role.PUBLIC:
        raise ValidationFailed("Assignee must be an active staff member", code="invalid_assignee")
    if Role(assignee.role) is not Role.ADMIN and assignee.station_id != row.station_id:
        raise ValidationFailed("Assignee must belong to the grievance's station", code="invalid_assignee")
    if row.assigned_to_user_id == assignee.id:
        raise Conflict("Grievance is already assigned to this person", code="already_assigned")
    previous = row.assigned_to_user_id
    row.assigned_to_user_id = assignee.id
    if row.status in (G.OPEN, G.REOPENED):
        previous_status = GrievanceStatus(row.status)
        row.status = G.ASSIGNED
        db.add(GrievanceEvent(grievance_id=row.id, kind=GrievanceEventKind.STATUS_CHANGED,
                              from_status=previous_status, to_status=G.ASSIGNED, actor_user_id=actor.id))
    db.add(GrievanceEvent(grievance_id=row.id, kind=GrievanceEventKind.ASSIGNED, actor_user_id=actor.id,
                          assignee_user_id=assignee.id, is_internal=True,
                          body_ciphertext=encrypt_text(reason) if reason else None))
    record_audit(
        db, action=AuditAction.GRIEVANCE_ASSIGNED, entity_type="grievance", entity_id=row.id, actor_user_id=actor.id,
        before={"assigned_to_user_id": previous}, after={"assigned_to_user_id": assignee.id}, reason=reason,
    )
    staff_notifications.notify(
        db, [assignee.id], StaffNotificationKind.GRIEVANCE_ASSIGNED, link=f"/grievances/{row.id}",
        dedupe=f"{row.id}:{assignee.id}:{row.version}",
    )
    db.commit()
    db.refresh(row)
    return row


def comment(db: Session, actor: User, grievance_id: uuid.UUID, body: str, internal: bool) -> Grievance:
    require_permission(actor, Permission.GRIEVANCE_MANAGE)
    row = load(db, actor, grievance_id, for_update=True)
    db.add(GrievanceEvent(grievance_id=row.id, kind=GrievanceEventKind.COMMENT, actor_user_id=actor.id,
                          body_ciphertext=encrypt_text(body), is_internal=internal))
    record_audit(db, action=AuditAction.GRIEVANCE_COMMENTED, entity_type="grievance", entity_id=row.id,
                 actor_user_id=actor.id, after={"internal": internal})
    row.updated_at = func.now()
    db.commit()
    db.refresh(row)
    return row


def _check_version(row: Grievance, expected: int | None) -> None:
    if expected is not None and expected != row.version:
        raise Conflict("The grievance changed since you loaded it; reload and try again", code="grievance_changed")


def escalate_overdue(db: Session, *, limit: int = 200) -> int:
    """Raises the escalation level of overdue open grievances and notifies supervisors and the assignee."""
    settings = get_settings()
    rows = list(
        db.scalars(
            select(Grievance)
            .where(
                Grievance.status.in_(OPEN_STATES),
                Grievance.due_at < func.now(),
                Grievance.escalation_level < settings.grievance_max_escalation_level,
            )
            .order_by(Grievance.due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    admins = list(db.scalars(select(User.id).where(User.role == Role.ADMIN, User.is_active.is_(True))))
    now = utcnow()
    for row in rows:
        row.escalation_level += 1
        row.escalated_at = now
        row.due_at = now + _sla() / 2
        db.add(GrievanceEvent(grievance_id=row.id, kind=GrievanceEventKind.ESCALATED, is_internal=True))
        record_audit(db, action=AuditAction.GRIEVANCE_ESCALATED, entity_type="grievance", entity_id=row.id,
                     actor_user_id=None, after={"escalation_level": row.escalation_level})
        recipients = set(admins)
        if row.assigned_to_user_id:
            recipients.add(row.assigned_to_user_id)
        staff_notifications.notify(
            db, recipients, StaffNotificationKind.GRIEVANCE_ESCALATED, link=f"/grievances/{row.id}",
            dedupe=f"{row.id}:{row.escalation_level}",
        )
    db.commit()
    return len(rows)


def summary(db: Session, actor: User) -> dict:
    """Scoped aggregates without any grievance text."""
    require_permission(actor, Permission.GRIEVANCE_READ)
    scope = _scope(actor)
    by_status = dict(db.execute(select(Grievance.status, func.count()).where(scope).group_by(Grievance.status)).all())
    by_category = dict(
        db.execute(select(Grievance.category, func.count()).where(scope).group_by(Grievance.category)).all()
    )
    overdue = db.scalar(
        select(func.count()).select_from(Grievance)
        .where(scope, Grievance.status.in_(OPEN_STATES), Grievance.due_at < func.now())
    ) or 0
    escalated = db.scalar(
        select(func.count()).select_from(Grievance)
        .where(scope, Grievance.status.in_(OPEN_STATES), Grievance.escalation_level > 0)
    ) or 0
    return {
        "generated_at": utcnow(),
        "by_status": {str(k): v for k, v in by_status.items()},
        "by_category": {str(k): v for k, v in by_category.items()},
        "open_total": sum(v for k, v in by_status.items() if k in OPEN_STATES),
        "overdue": overdue,
        "escalated_open": escalated,
        "sla_hours": get_settings().grievance_sla_hours,
    }
