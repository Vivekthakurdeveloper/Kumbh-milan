"""
Retention: separate policies per data type, legal holds, dry-run plans, two-person approval, audited
execution. Case records are never deleted: finished cases are archived, and archived cases can later be
anonymised (personal details removed, the case skeleton and the audit trail kept). Audit events are never
modified or deleted by the application.
"""

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, text, update
from sqlalchemy.orm import Session, joinedload

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.errors import Conflict, NotFound
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseAttachment, CaseContact, CaseMatch, CaseNote, CaseStatusHistory, LegalHold, RetentionRun, User
from app.models.enums import CaseStatus, CaseType, RetentionPolicy, RetentionRunStatus, ScanStatus
from app.models.retention import REDACTION_PREFIX
from app.observability.metrics import RETENTION_ACTIONS
from app.services.access import load_case
from app.storage import ObjectStorage

logger = logging.getLogger("kumbh_setu.retention")
P = RetentionPolicy
FINISHED = (CaseStatus.CLOSED, CaseStatus.RESOLVED)
SAMPLE_SIZE = 50

# Personal or identifying free text removed by anonymisation. Category-level facts (type, station, age group,
# gender, dates, outcome) are kept for statistics and the audit trail.
ANONYMISED_FIELDS: dict[CaseType, tuple[str, ...]] = {
    CaseType.MISSING_PERSON: ("person_name", "height_description", "clothing_description", "identifying_marks",
                              "last_seen_location", "medical_needs", "additional_description"),
    CaseType.FOUND_PERSON: ("physical_description", "clothing_description", "identifying_marks", "found_location",
                            "care_or_assistance_required", "current_care_location"),
    CaseType.LOST_ITEM: ("item_description", "brand_or_color", "identifying_features", "last_seen_location"),
    CaseType.FOUND_ITEM: ("item_description", "found_location", "storage_location"),
}
_RELATIONSHIP = {
    CaseType.MISSING_PERSON: "missing_person", CaseType.FOUND_PERSON: "found_person",
    CaseType.LOST_ITEM: "lost_item", CaseType.FOUND_ITEM: "found_item",
}


def policies() -> list[dict[str, Any]]:
    s = get_settings()
    return [
        {"policy": P.ARCHIVE_CLOSED_CASES, "data": "Closed and resolved cases",
         "after_days": s.retention_archive_after_days,
         "action": "Hide from day-to-day lists and from help-desk staff. Reversible by reopening the case."},
        {"policy": P.ANONYMIZE_ARCHIVED_CASES, "data": "Archived cases",
         "after_days": s.retention_anonymize_after_days,
         "action": "Remove names, descriptions, places, coordinates, contact details, photos/recordings, note and "
                   "evidence text. The case number, type, station, dates, outcome, and audit trail remain. Irreversible."},
        {"policy": P.PURGE_ATTACHMENT_OBJECTS, "data": "Deleted attachments and files that failed malware scanning",
         "after_days": s.retention_attachment_purge_after_days,
         "action": "Delete the stored object; the metadata row and audit trail remain."},
        {"policy": "OPERATIONAL (purge-expired CLI)", "data": "Sessions, login attempts, idempotency keys",
         "after_days": None, "action": "Deleted after expiry by `python -m app.cli purge-expired`."},
        {"policy": "AUDIT", "data": "Audit events", "after_days": None,
         "action": "Never modified or deleted by the application (database-enforced)."},
    ]


# --- Legal holds -----------------------------------------------------------------------------------


def _active_hold():
    return exists().where(LegalHold.case_id == Case.id, LegalHold.released_at.is_(None))


def place_hold(db: Session, actor: User, case_id: uuid.UUID, reason: str, reference: str | None) -> LegalHold:
    require_permission(actor, Permission.RETENTION_MANAGE)
    case = load_case(db, actor, case_id, for_update=True)
    hold = LegalHold(id=uuid.uuid4(), case_id=case.id, reason=reason, reference=reference, placed_by_user_id=actor.id)
    db.add(hold)
    record_audit(db, action=AuditAction.LEGAL_HOLD_PLACED, entity_type="legal_hold", entity_id=hold.id,
                 actor_user_id=actor.id, case=case, reason=reason, after={"reference": reference})
    db.commit()
    db.refresh(hold)
    return hold


def release_hold(db: Session, actor: User, case_id: uuid.UUID, hold_id: uuid.UUID, reason: str) -> LegalHold:
    require_permission(actor, Permission.RETENTION_MANAGE)
    case = load_case(db, actor, case_id)
    hold = db.scalar(select(LegalHold).where(LegalHold.id == hold_id, LegalHold.case_id == case.id).with_for_update())
    if hold is None:
        raise NotFound("Legal hold not found", code="legal_hold_not_found")
    if hold.released_at is not None:
        raise Conflict("This legal hold was already released", code="legal_hold_released")
    hold.released_at, hold.released_by_user_id, hold.release_reason = utcnow(), actor.id, reason
    record_audit(db, action=AuditAction.LEGAL_HOLD_RELEASED, entity_type="legal_hold", entity_id=hold.id,
                 actor_user_id=actor.id, case=case, reason=reason)
    db.commit()
    db.refresh(hold)
    return hold


def list_holds(db: Session, actor: User, case_id: uuid.UUID) -> list[LegalHold]:
    require_permission(actor, Permission.RETENTION_MANAGE)
    load_case(db, actor, case_id)
    return list(db.scalars(select(LegalHold).where(LegalHold.case_id == case_id).order_by(LegalHold.created_at)))


# --- Eligibility -----------------------------------------------------------------------------------


def _cutoff(policy: RetentionPolicy, params: dict[str, Any]):
    return utcnow() - timedelta(days=params["after_days"])


def _finished_at():
    """Archive eligibility uses close time, or the resolve time when a case is resolved but not closed."""
    resolved_at = (
        select(func.max(CaseStatusHistory.created_at))
        .where(CaseStatusHistory.case_id == Case.id, CaseStatusHistory.to_status == CaseStatus.RESOLVED)
        .correlate(Case)
        .scalar_subquery()
    )
    return func.coalesce(Case.closed_at, resolved_at)


def _case_conditions(policy: RetentionPolicy, cutoff) -> list:
    if policy is P.ARCHIVE_CLOSED_CASES:
        finished_at = _finished_at()
        return [Case.status.in_(FINISHED), Case.archived_at.is_(None), finished_at.is_not(None), finished_at < cutoff]
    return [Case.status.in_(FINISHED), Case.archived_at < cutoff, Case.anonymized_at.is_(None)]


def _attachment_conditions(cutoff) -> list:
    return [
        CaseAttachment.object_purged_at.is_(None),
        or_(
            CaseAttachment.deleted_at < cutoff,
            and_(CaseAttachment.scan_status == ScanStatus.SCAN_FAILED, CaseAttachment.created_at < cutoff),
        ),
    ]


def _plan(db: Session, policy: RetentionPolicy, params: dict[str, Any]) -> dict[str, Any]:
    cutoff = _cutoff(policy, params)
    limit = get_settings().retention_max_items_per_run
    if policy is P.PURGE_ATTACHMENT_OBJECTS:
        base = select(CaseAttachment.id, Case.case_number).join(Case, Case.id == CaseAttachment.case_id).where(
            *_attachment_conditions(cutoff)
        )
    else:
        base = select(Case.id, Case.case_number).where(*_case_conditions(policy, cutoff))
    held = db.scalar(select(text("count(*)")).select_from(base.where(_active_hold()).subquery())) or 0
    rows = db.execute(base.where(~_active_hold()).order_by(Case.created_at, Case.id).limit(limit + 1)).all()
    return {
        "cutoff": cutoff.isoformat(),
        "eligible": min(len(rows), limit),
        "excluded_by_legal_hold": held,
        "truncated": len(rows) > limit,
        "items": [str(r[0]) for r in rows[:limit]],
        "sample_case_numbers": [r[1] for r in rows[:SAMPLE_SIZE]],
    }


# --- Runs ------------------------------------------------------------------------------------------


def _params(policy: RetentionPolicy) -> dict[str, Any]:
    s = get_settings()
    days = {
        P.ARCHIVE_CLOSED_CASES: s.retention_archive_after_days,
        P.ANONYMIZE_ARCHIVED_CASES: s.retention_anonymize_after_days,
        P.PURGE_ATTACHMENT_OBJECTS: s.retention_attachment_purge_after_days,
    }[policy]
    return {"after_days": days}


def plan_run(db: Session, actor: User, policy: RetentionPolicy, note: str | None) -> RetentionRun:
    """Dry run: computes and stores what would be affected. Changes no case data."""
    require_permission(actor, Permission.RETENTION_MANAGE)
    params = _params(policy)
    run = RetentionRun(
        id=uuid.uuid4(), policy=policy, status=RetentionRunStatus.PLANNED, parameters=params,
        plan_report=_plan(db, policy, params), planned_by_user_id=actor.id, note=note,
    )
    db.add(run)
    record_audit(db, action=AuditAction.RETENTION_RUN_PLANNED, entity_type="retention_run", entity_id=run.id,
                 actor_user_id=actor.id, after={"policy": policy, "parameters": params,
                                                "eligible": run.plan_report["eligible"],
                                                "excluded_by_legal_hold": run.plan_report["excluded_by_legal_hold"]})
    db.commit()
    db.refresh(run)
    return run


def get_run(db: Session, actor: User, run_id: uuid.UUID, *, for_update: bool = False) -> RetentionRun:
    require_permission(actor, Permission.RETENTION_MANAGE)
    stmt = select(RetentionRun).where(RetentionRun.id == run_id)
    run = db.scalar(stmt.with_for_update() if for_update else stmt)
    if run is None:
        raise NotFound("Retention run not found", code="retention_run_not_found")
    return run


def list_runs(db: Session, actor: User, *, limit: int, offset: int) -> list[RetentionRun]:
    require_permission(actor, Permission.RETENTION_MANAGE)
    return list(db.scalars(select(RetentionRun).order_by(RetentionRun.created_at.desc()).limit(limit).offset(offset)))


def approve_run(db: Session, actor: User, run_id: uuid.UUID) -> RetentionRun:
    run = get_run(db, actor, run_id, for_update=True)
    if run.status != RetentionRunStatus.PLANNED:
        raise Conflict(f"Only planned runs can be approved (this run is {run.status})", code="retention_run_state")
    if run.planned_by_user_id == actor.id:
        raise Conflict("A different administrator must approve this run", code="two_person_rule")
    run.status, run.approved_by_user_id, run.approved_at = RetentionRunStatus.APPROVED, actor.id, utcnow()
    record_audit(db, action=AuditAction.RETENTION_RUN_APPROVED, entity_type="retention_run", entity_id=run.id,
                 actor_user_id=actor.id, after={"policy": run.policy})
    db.commit()
    return run


def cancel_run(db: Session, actor: User, run_id: uuid.UUID, reason: str) -> RetentionRun:
    run = get_run(db, actor, run_id, for_update=True)
    if run.status not in (RetentionRunStatus.PLANNED, RetentionRunStatus.APPROVED):
        raise Conflict(f"This run can no longer be cancelled ({run.status})", code="retention_run_state")
    run.status, run.cancelled_by_user_id, run.cancelled_at = RetentionRunStatus.CANCELLED, actor.id, utcnow()
    record_audit(db, action=AuditAction.RETENTION_RUN_CANCELLED, entity_type="retention_run", entity_id=run.id,
                 actor_user_id=actor.id, reason=reason)
    db.commit()
    return run


def execute_run(db: Session, storage: ObjectStorage, run_id: uuid.UUID, executor: User | None) -> RetentionRun:
    """
    Runs an APPROVED plan. Each item is processed in its own transaction after re-checking eligibility
    (including legal holds placed since planning); ineligible items are skipped and reported.
    `db` must be able to update notes for anonymisation (the owner connection; see RETENTION_DATABASE_URL).
    """
    if executor is not None:
        from app.auth.mfa import require_step_up

        require_step_up(executor)
    run = db.scalar(select(RetentionRun).where(RetentionRun.id == run_id).with_for_update())
    if run is None:
        raise NotFound("Retention run not found", code="retention_run_not_found")
    if run.status != RetentionRunStatus.APPROVED:
        raise Conflict(f"Only approved runs can be executed (this run is {run.status})", code="retention_run_state")
    run.status, run.execution_started_at = RetentionRunStatus.EXECUTING, utcnow()
    run.executed_by_user_id = executor.id if executor else None
    db.commit()

    policy = RetentionPolicy(run.policy)
    cutoff = _cutoff(policy, run.parameters)
    outcomes: dict[str, int] = {"done": 0, "skipped": 0, "failed": 0}
    failures: list[dict[str, str]] = []
    for raw_id in run.plan_report["items"]:
        try:
            outcome = "done" if _execute_item(db, storage, run, policy, cutoff, uuid.UUID(raw_id)) else "skipped"
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the run; it is reported
            db.rollback()
            outcome = "failed"
            failures.append({"item": raw_id, "error": type(exc).__name__})
            logger.exception("Retention item failed", extra={"run_id": str(run.id), "item": raw_id})
        outcomes[outcome] += 1
        RETENTION_ACTIONS.labels(policy.value, outcome).inc()

    run = db.scalar(select(RetentionRun).where(RetentionRun.id == run_id).with_for_update())
    run.status = RetentionRunStatus.FAILED if outcomes["failed"] else RetentionRunStatus.EXECUTED
    run.executed_at = utcnow()
    run.execution_report = {**outcomes, "failures": failures[:100]}
    record_audit(db, action=AuditAction.RETENTION_RUN_EXECUTED, entity_type="retention_run", entity_id=run.id,
                 actor_user_id=executor.id if executor else None, after={"policy": policy, **outcomes})
    db.commit()
    return run


def _execute_item(db: Session, storage: ObjectStorage, run: RetentionRun, policy: RetentionPolicy, cutoff,
                  item_id: uuid.UUID) -> bool:
    if policy is P.PURGE_ATTACHMENT_OBJECTS:
        row = db.execute(
            select(CaseAttachment, Case).join(Case, Case.id == CaseAttachment.case_id)
            .where(CaseAttachment.id == item_id, *_attachment_conditions(cutoff), ~_active_hold())
            .with_for_update(of=CaseAttachment)
        ).first()
        if row is None:
            db.rollback()
            return False
        attachment, case = row
        from app.services.faces import delete_embedding_for_attachment

        delete_embedding_for_attachment(db, attachment, actor=None, case=case)
        storage.delete(attachment.storage_key)
        attachment.object_purged_at = utcnow()
        attachment.deleted_at = attachment.deleted_at or utcnow()
        record_audit(db, action=AuditAction.ATTACHMENT_OBJECT_PURGED, entity_type="case_attachment",
                     entity_id=attachment.id, actor_user_id=None, case=case, after={"retention_run_id": run.id})
        db.commit()
        return True

    case = db.scalars(
        select(Case).where(Case.id == item_id, *_case_conditions(policy, cutoff), ~_active_hold())
        .options(joinedload(Case.missing_person), joinedload(Case.found_person),
                 joinedload(Case.lost_item), joinedload(Case.found_item))
        .with_for_update(of=Case)
    ).unique().first()
    if case is None:
        db.rollback()
        return False
    if policy is P.ARCHIVE_CLOSED_CASES:
        case.archived_at = utcnow()
        record_audit(db, action=AuditAction.CASE_ARCHIVED, entity_type="case", entity_id=case.id,
                     actor_user_id=None, case=case, after={"retention_run_id": run.id})
    else:
        _anonymise(db, storage, run, case)
    db.commit()
    return True


def _anonymise(db: Session, storage: ObjectStorage, run: RetentionRun, case: Case) -> None:
    marker = f"{REDACTION_PREFIX}{run.id}]"
    db.execute(text("SELECT set_config('kumbh.retention_run_id', :rid, true)"), {"rid": str(run.id)})

    case_type = CaseType(case.case_type)
    detail = getattr(case, _RELATIONSHIP[case_type])
    for field in ANONYMISED_FIELDS[case_type]:
        setattr(detail, field, None)
    case.search_text = case.case_number
    case.latitude = case.longitude = case.coordinate_source = case.coordinate_accuracy_m = None
    case.anonymized_at = utcnow()

    contact = db.get(CaseContact, case.id)
    if contact is not None:
        db.delete(contact)
    from app.services.notifications import cancel_for_case

    cancel_for_case(db, case.id)

    from app.services.faces import delete_embeddings_for_case

    delete_embeddings_for_case(db, case, reason="case_anonymized")

    attachments = list(db.scalars(
        select(CaseAttachment).where(CaseAttachment.case_id == case.id, CaseAttachment.object_purged_at.is_(None))
    ))
    for attachment in attachments:
        storage.delete(attachment.storage_key)
        attachment.object_purged_at = utcnow()
        attachment.deleted_at = attachment.deleted_at or utcnow()
        attachment.original_filename = None

    notes = db.execute(
        update(CaseNote).where(CaseNote.case_id == case.id, CaseNote.note_text != marker).values(note_text=marker)
    ).rowcount
    db.execute(
        update(CaseStatusHistory).where(CaseStatusHistory.case_id == case.id).values(
            reason=_redact(CaseStatusHistory.reason, marker),
            verification_evidence=_redact(CaseStatusHistory.verification_evidence, marker),
        )
    )
    db.execute(
        update(CaseMatch).where(or_(CaseMatch.left_case_id == case.id, CaseMatch.right_case_id == case.id)).values(
            rationale=_redact(CaseMatch.rationale, marker),
            review_evidence=_redact(CaseMatch.review_evidence, marker),
            revocation_reason=_redact(CaseMatch.revocation_reason, marker),
        )
    )
    record_audit(db, action=AuditAction.CASE_ANONYMIZED, entity_type="case", entity_id=case.id, actor_user_id=None,
                 case=case, after={"retention_run_id": run.id, "attachments_purged": len(attachments),
                                   "notes_redacted": notes, "contact_removed": contact is not None})


def _redact(column, marker: str):
    from sqlalchemy import case as sql_case

    return sql_case((column.is_(None), None), else_=marker)
