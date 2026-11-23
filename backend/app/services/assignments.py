"""Supervisor-approved assignment requests and bulk assignment (BE-104)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, has_permission, require_permission
from app.common.errors import AppError, Conflict, Forbidden, NotFound
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseAssignmentRequest, User
from app.models.enums import AssignmentRequestStatus, Role, StaffNotificationKind
from app.schemas.workflow import BulkAssignItem, BulkAssignRequest
from app.services import staff_notifications
from app.services.access import load_case
from app.services.workflow import apply_assignment, validate_assignee

R = AssignmentRequestStatus


def approval_required(actor: User) -> bool:
    return get_settings().assignment_requires_approval and Role(actor.role) is not Role.ADMIN


def _supervisors(db: Session) -> list[uuid.UUID]:
    return list(db.scalars(select(User.id).where(User.role == Role.ADMIN, User.is_active.is_(True))))


def request_assignment(
    db: Session, actor: User, case: Case, assignee_user_id: uuid.UUID, reason: str | None
) -> CaseAssignmentRequest:
    """Records a pending request in the caller's transaction after the same checks a direct assignment uses."""
    assignee = validate_assignee(db, actor, case, assignee_user_id)
    pending = db.scalar(
        select(CaseAssignmentRequest).where(
            CaseAssignmentRequest.case_id == case.id, CaseAssignmentRequest.status == R.PENDING
        )
    )
    if pending is not None:
        raise Conflict(
            "An assignment request for this case is already awaiting approval",
            code="assignment_request_pending",
            details={"request_id": pending.id},
        )
    row = CaseAssignmentRequest(
        id=uuid.uuid4(), case_id=case.id, assignee_user_id=assignee.id, requested_by_user_id=actor.id,
        reason=reason, status=R.PENDING,
    )
    db.add(row)
    record_audit(
        db, action=AuditAction.ASSIGNMENT_REQUESTED, entity_type="assignment_request", entity_id=row.id,
        actor_user_id=actor.id, case=case, after={"assignee_user_id": assignee.id}, reason=reason,
    )
    staff_notifications.notify(
        db, _supervisors(db), StaffNotificationKind.ASSIGNMENT_APPROVAL_REQUESTED,
        link=f"/assignment-requests/{row.id}", dedupe=str(row.id), case_id=case.id,
    )
    return row


def create_request(
    db: Session, actor: User, case_id: uuid.UUID, assignee_user_id: uuid.UUID, reason: str | None
) -> CaseAssignmentRequest:
    require_permission(actor, Permission.CASE_ASSIGN)
    case = load_case(db, actor, case_id, for_update=True)
    row = request_assignment(db, actor, case, assignee_user_id, reason)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("An assignment request for this case is already awaiting approval",
                       code="assignment_request_pending") from None
    db.refresh(row)
    return row


def _load(db: Session, actor: User, request_id: uuid.UUID, *, for_update: bool = False) -> CaseAssignmentRequest:
    stmt = select(CaseAssignmentRequest).where(CaseAssignmentRequest.id == request_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("Assignment request not found", code="assignment_request_not_found")
    if not has_permission(actor, Permission.ASSIGNMENT_APPROVE) and row.requested_by_user_id != actor.id:
        raise NotFound("Assignment request not found", code="assignment_request_not_found")
    return row


def list_requests(
    db: Session, actor: User, *, status: AssignmentRequestStatus | None, limit: int, offset: int
) -> tuple[list[CaseAssignmentRequest], int]:
    require_permission(actor, Permission.CASE_ASSIGN)
    conditions = []
    if not has_permission(actor, Permission.ASSIGNMENT_APPROVE):
        conditions.append(CaseAssignmentRequest.requested_by_user_id == actor.id)
    if status is not None:
        conditions.append(CaseAssignmentRequest.status == status)
    total = db.scalar(select(func.count()).select_from(CaseAssignmentRequest).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(CaseAssignmentRequest)
            .where(*conditions)
            .order_by(CaseAssignmentRequest.created_at.desc(), CaseAssignmentRequest.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_request(db: Session, actor: User, request_id: uuid.UUID) -> CaseAssignmentRequest:
    require_permission(actor, Permission.CASE_ASSIGN)
    return _load(db, actor, request_id)


def _decide(row: CaseAssignmentRequest, actor: User, status: AssignmentRequestStatus, reason: str | None) -> None:
    if row.status != R.PENDING:
        raise Conflict("This request has already been decided", code="assignment_request_decided")
    row.status = status
    row.decided_by_user_id = actor.id
    row.decided_at = utcnow()
    row.decision_reason = reason


def approve(db: Session, actor: User, request_id: uuid.UUID, reason: str | None) -> CaseAssignmentRequest:
    require_permission(actor, Permission.ASSIGNMENT_APPROVE)
    row = _load(db, actor, request_id, for_update=True)
    if row.status != R.PENDING:
        raise Conflict("This request has already been decided", code="assignment_request_decided")
    if row.requested_by_user_id == actor.id:
        raise Forbidden("A supervisor cannot approve their own request", code="self_approval")
    case = load_case(db, actor, row.case_id, for_update=True)
    assignee = validate_assignee(db, actor, case, row.assignee_user_id)
    _decide(row, actor, R.APPROVED, reason)
    apply_assignment(db, actor, case, assignee, row.reason)
    record_audit(
        db, action=AuditAction.ASSIGNMENT_APPROVED, entity_type="assignment_request", entity_id=row.id,
        actor_user_id=actor.id, case=case, after={"assignee_user_id": assignee.id}, reason=reason,
    )
    _notify_requester(db, row)
    db.commit()
    db.refresh(row)
    return row


def reject(db: Session, actor: User, request_id: uuid.UUID, reason: str | None) -> CaseAssignmentRequest:
    require_permission(actor, Permission.ASSIGNMENT_APPROVE)
    row = _load(db, actor, request_id, for_update=True)
    _decide(row, actor, R.REJECTED, reason)
    case = db.get(Case, row.case_id)
    record_audit(
        db, action=AuditAction.ASSIGNMENT_REJECTED, entity_type="assignment_request", entity_id=row.id,
        actor_user_id=actor.id, case=case, reason=reason,
    )
    _notify_requester(db, row)
    db.commit()
    db.refresh(row)
    return row


def cancel(db: Session, actor: User, request_id: uuid.UUID, reason: str | None) -> CaseAssignmentRequest:
    require_permission(actor, Permission.CASE_ASSIGN)
    row = _load(db, actor, request_id, for_update=True)
    if row.requested_by_user_id != actor.id:
        raise Forbidden("Only the requester can cancel this request", code="not_requester")
    _decide(row, actor, R.CANCELLED, reason)
    record_audit(
        db, action=AuditAction.ASSIGNMENT_CANCELLED, entity_type="assignment_request", entity_id=row.id,
        actor_user_id=actor.id, case=db.get(Case, row.case_id), reason=reason,
    )
    db.commit()
    db.refresh(row)
    return row


def _notify_requester(db: Session, row: CaseAssignmentRequest) -> None:
    staff_notifications.notify(
        db, [row.requested_by_user_id], StaffNotificationKind.ASSIGNMENT_DECIDED,
        link=f"/assignment-requests/{row.id}", dedupe=f"{row.id}:{row.status}", case_id=row.case_id,
    )


def bulk_assign(db: Session, actor: User, req: BulkAssignRequest) -> list[BulkAssignItem]:
    """Each case is applied (or requested) in its own transaction so one failure does not block the rest."""
    require_permission(actor, Permission.CASE_ASSIGN)
    needs_approval = approval_required(actor)
    items: list[BulkAssignItem] = []
    for case_id in dict.fromkeys(req.case_ids):
        try:
            case = load_case(db, actor, case_id, for_update=True)
            if needs_approval:
                row = request_assignment(db, actor, case, req.assignee_user_id, req.reason)
                db.commit()
                items.append(BulkAssignItem(case_id=case_id, outcome="REQUESTED", request_id=row.id))
            else:
                assignee = validate_assignee(db, actor, case, req.assignee_user_id)
                apply_assignment(db, actor, case, assignee, req.reason)
                db.commit()
                items.append(BulkAssignItem(case_id=case_id, outcome="ASSIGNED"))
        except AppError as exc:
            db.rollback()
            items.append(BulkAssignItem(case_id=case_id, outcome="FAILED", error_code=exc.code))
        except IntegrityError:
            db.rollback()
            items.append(BulkAssignItem(case_id=case_id, outcome="FAILED", error_code="conflict"))
    return items
