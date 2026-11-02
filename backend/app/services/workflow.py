"""Assignment, status, custody, notes, matches, history. Shared by all four case types."""

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, has_permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import Conflict, Forbidden, InvalidTransition, NotFound, ValidationFailed
from app.common.time import utcnow
from app.models import AuditEvent, Case, CaseAssignment, CaseMatch, CaseNote, CaseStatusHistory, IdempotencyKey, User
from app.models.enums import CaseStatus, CaseType, CustodyStatus, MatchDomain, MatchSource, MatchStatus, NoteType, Role
from app.repositories import cases as case_repo
from app.schemas.workflow import (
    AssignmentRead,
    AssignRequest,
    CaseHistory,
    CustodyChangeRequest,
    MatchProposeRequest,
    MatchRead,
    MatchReviewRequest,
    MatchRevokeRequest,
    NoteCreate,
    StatusChangeRequest,
    StatusHistoryRead,
)
from app.observability.metrics import STATUS_TRANSITIONS, count_after_commit
from app.services.access import load_case
from app.services.case_types import CaseTypeSpec
from app.services.cases import to_summary
from app.services.idempotency import run_idempotent_operation

S = CaseStatus

TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    S.OPEN: frozenset({S.UNDER_REVIEW, S.ASSIGNED, S.CLOSED}),
    S.UNDER_REVIEW: frozenset({S.ASSIGNED, S.MATCH_FOUND, S.CLOSED}),
    S.ASSIGNED: frozenset({S.UNDER_REVIEW, S.MATCH_FOUND, S.RESOLVED, S.CLOSED}),
    S.MATCH_FOUND: frozenset({S.ASSIGNED, S.RESOLVED, S.CLOSED}),
    S.RESOLVED: frozenset({S.CLOSED, S.REOPENED}),
    S.CLOSED: frozenset({S.REOPENED}),
    S.REOPENED: frozenset({S.UNDER_REVIEW, S.ASSIGNED}),
}

TARGET_PERMISSION: dict[CaseStatus, Permission] = {
    S.UNDER_REVIEW: Permission.CASE_REVIEW,
    S.ASSIGNED: Permission.CASE_ASSIGN,
    S.MATCH_FOUND: Permission.CASE_RESOLVE,
    S.RESOLVED: Permission.CASE_RESOLVE,
    S.CLOSED: Permission.CASE_RESOLVE,
    S.REOPENED: Permission.CASE_RESOLVE,
}

CUSTODY_TRANSITIONS: dict[CustodyStatus, frozenset[CustodyStatus]] = {
    CustodyStatus.IN_CUSTODY: frozenset({CustodyStatus.TRANSFERRED, CustodyStatus.RELEASED}),
    CustodyStatus.TRANSFERRED: frozenset({CustodyStatus.IN_CUSTODY, CustodyStatus.TRANSFERRED, CustodyStatus.RELEASED}),
    CustodyStatus.RELEASED: frozenset(),
}

MATCH_PAIRS: dict[tuple[CaseType, CaseType], MatchDomain] = {
    (CaseType.MISSING_PERSON, CaseType.FOUND_PERSON): MatchDomain.PERSON,
    (CaseType.LOST_ITEM, CaseType.FOUND_ITEM): MatchDomain.ITEM,
}

INACTIVE_STATUSES = frozenset({S.RESOLVED, S.CLOSED})

# Confirming a match is a reviewed, evidence-backed workflow, so it may move any active, not-yet-matched case
# (including a freshly registered OPEN case) to MATCH_FOUND. The generic /status endpoint keeps TRANSITIONS.
MATCH_CONFIRMATION_SOURCES = frozenset({S.OPEN, S.UNDER_REVIEW, S.ASSIGNED, S.REOPENED})
AUTO_ASSIGN_SOURCES = frozenset({S.OPEN, S.UNDER_REVIEW, S.REOPENED})


def check_version(case: Case, expected: int | None) -> None:
    if expected is not None and expected != case.version:
        raise Conflict(
            "The case changed since you loaded it; reload and try again",
            code="case_changed",
            details={"case_id": case.id, "expected_version": expected, "current_version": case.version},
        )


def _apply_transition(
    db: Session,
    case: Case,
    target: CaseStatus,
    actor: User,
    reason: str | None,
    *,
    evidence: str | None = None,
    match_id: uuid.UUID | None = None,
) -> None:
    previous = CaseStatus(case.status)
    case.status = target
    count_after_commit(db, STATUS_TRANSITIONS, target.value)
    if target is S.CLOSED:
        case.closed_at = utcnow()
    elif previous is S.CLOSED:
        case.closed_at = None
    if target is S.REOPENED:
        case.archived_at = None
    case.updated_at = func.now()
    db.add(
        CaseStatusHistory(
            case_id=case.id,
            from_status=previous,
            to_status=target,
            changed_by_user_id=actor.id,
            reason=reason,
            verification_evidence=evidence,
            match_id=match_id,
        )
    )
    action = {S.CLOSED: AuditAction.CASE_CLOSED, S.REOPENED: AuditAction.CASE_REOPENED}.get(
        target, AuditAction.CASE_STATUS_CHANGED
    )
    record_audit(
        db, action=action, entity_type="case", entity_id=case.id, actor_user_id=actor.id, case=case,
        before={"status": previous}, after={"status": target, "match_id": match_id, "evidence_recorded": bool(evidence)},
        reason=reason,
    )


def _confirmed_match_for(db: Session, case: Case, match_id: uuid.UUID) -> CaseMatch:
    match = db.get(CaseMatch, match_id)
    if match is None or case.id not in (match.left_case_id, match.right_case_id):
        raise ValidationFailed("match_id does not refer to a match on this case", code="invalid_match")
    if match.status != MatchStatus.CONFIRMED:
        raise ValidationFailed("Only a confirmed match can support this status", code="match_not_confirmed")
    return match


# --- Assignment ----------------------------------------------------------------------------------


def assign_case(
    db: Session, actor: User, spec: CaseTypeSpec, case_id: uuid.UUID, req: AssignRequest,
    *, idempotency_key: str | None = None,
) -> bool:
    require_permission(actor, Permission.CASE_ASSIGN)
    return run_idempotent_operation(
        db, user=actor, key=idempotency_key, endpoint=f"assign:{case_id}", payload=req.model_dump(),
        resource_id=case_id, operation=lambda: _assign(db, actor, spec, case_id, req),
    )


def _assign(db: Session, actor: User, spec: CaseTypeSpec, case_id: uuid.UUID, req: AssignRequest) -> None:
    case = load_case(db, actor, case_id, spec=spec, for_update=True)
    check_version(case, req.expected_version)
    if case.status in INACTIVE_STATUSES:
        raise Conflict("Resolved or closed cases cannot be reassigned", code="case_inactive")
    assignee = db.get(User, req.assignee_user_id)
    if assignee is None or not assignee.is_active or assignee.role != Role.POLICE_OFFICER:
        raise ValidationFailed("Assignee must be an active police officer", code="invalid_assignee")
    if Role(actor.role) is not Role.ADMIN and assignee.station_id != actor.station_id:
        raise Forbidden("You can only assign officers from your own station", code="station_scope")
    if case.assigned_to_user_id == assignee.id:
        raise Conflict("Case is already assigned to this officer", code="already_assigned")

    now = utcnow()
    active = db.scalar(
        select(CaseAssignment).where(CaseAssignment.case_id == case.id, CaseAssignment.is_active.is_(True))
    )
    if active is not None:
        active.is_active = False
        active.unassigned_at = now
        db.flush()
    db.add(
        CaseAssignment(
            case_id=case.id, assigned_to_user_id=assignee.id, assigned_by_user_id=actor.id,
            assigned_at=now, reason=req.reason, is_active=True,
        )
    )
    previous_assignee = case.assigned_to_user_id
    case.assigned_to_user_id = assignee.id
    case.updated_at = func.now()
    record_audit(
        db, action=AuditAction.CASE_ASSIGNED, entity_type="case", entity_id=case.id, actor_user_id=actor.id,
        case=case, before={"assigned_to_user_id": previous_assignee}, after={"assigned_to_user_id": assignee.id},
        reason=req.reason,
    )
    if CaseStatus(case.status) in AUTO_ASSIGN_SOURCES:
        _apply_transition(db, case, S.ASSIGNED, actor, req.reason or "Assigned to officer")
    db.commit()


# --- Status --------------------------------------------------------------------------------------


def change_status(
    db: Session, actor: User, spec: CaseTypeSpec, case_id: uuid.UUID, req: StatusChangeRequest,
    *, idempotency_key: str | None = None,
) -> bool:
    target = req.status
    permission = TARGET_PERMISSION.get(target)
    if permission is None:
        raise InvalidTransition(f"Cases cannot be moved to {target}", code="invalid_status_transition")
    require_permission(actor, permission)
    return run_idempotent_operation(
        db, user=actor, key=idempotency_key, endpoint=f"status:{case_id}", payload=req.model_dump(),
        resource_id=case_id, operation=lambda: _change_status(db, actor, spec, case_id, req),
    )


def _change_status(db: Session, actor: User, spec: CaseTypeSpec, case_id: uuid.UUID, req: StatusChangeRequest) -> None:
    target = req.status
    case = load_case(db, actor, case_id, spec=spec, for_update=True)
    check_version(case, req.expected_version)
    current = CaseStatus(case.status)
    allowed = TRANSITIONS[current]
    if target not in allowed:
        raise InvalidTransition(
            f"Cannot move a case from {current} to {target}",
            details={"from": current, "to": target, "allowed": sorted(allowed)},
        )
    if target is S.REOPENED and case.anonymized_at is not None:
        raise Conflict("Anonymised cases cannot be reopened; register a new case", code="case_anonymized")

    if target is S.ASSIGNED and case.assigned_to_user_id is None:
        raise Conflict("Assign an officer with the assign endpoint first", code="assignee_required")
    if target is S.MATCH_FOUND:
        if req.match_id is None:
            raise ValidationFailed("match_id of a confirmed match is required", code="match_required")
        _confirmed_match_for(db, case, req.match_id)
    if target is S.RESOLVED:
        if _safeguarding_review_required(case) and not req.verification_evidence:
            raise ValidationFailed(
                "This case is flagged for safeguarding review and needs recorded verification evidence",
                code="safeguarding_evidence_required",
            )
        if req.match_id is not None:
            _confirmed_match_for(db, case, req.match_id)
        elif not req.verification_evidence:
            raise ValidationFailed(
                "Resolving requires a confirmed match_id or verification_evidence recorded by the officer",
                code="verification_required",
            )
        if spec.case_type is CaseType.FOUND_ITEM and case.found_item.custody_status != CustodyStatus.RELEASED:
            raise Conflict("Release the item from custody before resolving", code="custody_not_released")

    _apply_transition(
        db, case, target, actor, req.reason, evidence=req.verification_evidence, match_id=req.match_id
    )
    db.commit()


def change_custody(
    db: Session, actor: User, spec: CaseTypeSpec, case_id: uuid.UUID, req: CustodyChangeRequest,
    *, idempotency_key: str | None = None,
) -> bool:
    require_permission(actor, Permission.CUSTODY_UPDATE)
    return run_idempotent_operation(
        db, user=actor, key=idempotency_key, endpoint=f"custody:{case_id}", payload=req.model_dump(),
        resource_id=case_id, operation=lambda: _change_custody(db, actor, spec, case_id, req),
    )


def _change_custody(db: Session, actor: User, spec: CaseTypeSpec, case_id: uuid.UUID, req: CustodyChangeRequest) -> None:
    case = load_case(db, actor, case_id, spec=spec, for_update=True)
    if case.status == S.CLOSED:
        raise Conflict("Closed cases cannot change custody", code="case_closed")
    detail = case.found_item
    current = CustodyStatus(detail.custody_status)
    if req.custody_status not in CUSTODY_TRANSITIONS[current]:
        raise InvalidTransition(
            f"Cannot move custody from {current} to {req.custody_status}",
            code="invalid_custody_transition",
            details={"from": current, "to": req.custody_status, "allowed": sorted(CUSTODY_TRANSITIONS[current])},
        )
    if req.custody_status is CustodyStatus.TRANSFERRED and not req.storage_location:
        raise ValidationFailed("storage_location is required when transferring", code="storage_location_required")
    if req.custody_status is CustodyStatus.RELEASED and not req.recipient_acknowledgement:
        raise ValidationFailed(
            "recipient_acknowledgement is required before an item can be released",
            code="handover_required",
        )
    detail.custody_status = req.custody_status
    if req.storage_location:
        detail.storage_location = req.storage_location
    if req.custody_status is CustodyStatus.RELEASED:
        detail.handover_acknowledgement = req.recipient_acknowledgement
        detail.released_at = utcnow()
    case.updated_at = func.now()
    record_audit(
        db, action=AuditAction.CUSTODY_CHANGED, entity_type="case", entity_id=case.id, actor_user_id=actor.id,
        case=case, before={"custody_status": current},
        after={"custody_status": req.custody_status, "storage_location_changed": bool(req.storage_location)},
        reason=req.reason,
    )
    db.commit()


# --- Notes ---------------------------------------------------------------------------------------


def list_notes(db: Session, actor: User, case_id: uuid.UUID, *, limit: int, offset: int) -> tuple[list[CaseNote], int]:
    require_permission(actor, Permission.CASE_READ)
    load_case(db, actor, case_id)
    conditions = [CaseNote.case_id == case_id]
    if not has_permission(actor, Permission.NOTE_SENSITIVE):
        conditions.append(CaseNote.is_sensitive.is_(False))
    total = db.scalar(select(func.count(CaseNote.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(CaseNote).where(*conditions).order_by(CaseNote.created_at, CaseNote.id).limit(limit).offset(offset)
    )
    return list(rows), total


def add_note(
    db: Session, actor: User, case_id: uuid.UUID, req: NoteCreate, *, idempotency_key: str | None = None
) -> tuple[CaseNote, bool]:
    require_permission(actor, Permission.NOTE_CREATE)
    load_case(db, actor, case_id)
    note_id = uuid.uuid4()
    replayed = run_idempotent_operation(
        db, user=actor, key=idempotency_key, endpoint=f"note:{case_id}", payload=req.model_dump(),
        resource_id=note_id, operation=lambda: _add_note(db, actor, case_id, req, note_id),
    )
    if replayed:
        note_id = db.scalar(
            select(IdempotencyKey.resource_id).where(
                IdempotencyKey.user_id == actor.id, IdempotencyKey.key == idempotency_key
            )
        )
    note = db.scalar(select(CaseNote).where(CaseNote.id == note_id, CaseNote.case_id == case_id))
    if note is None:
        raise NotFound("Note not found", code="note_not_found")
    return note, replayed


def _add_note(db: Session, actor: User, case_id: uuid.UUID, req: NoteCreate, note_id: uuid.UUID) -> None:
    case = load_case(db, actor, case_id)
    can_sensitive = has_permission(actor, Permission.NOTE_SENSITIVE)
    if req.is_sensitive and not can_sensitive:
        raise Forbidden("You cannot record sensitive notes", code="permission_denied")
    if not req.note_text.strip():
        raise ValidationFailed("note_text must not be blank", code="blank_note")

    is_sensitive = req.is_sensitive
    if req.note_type is NoteType.CORRECTION:
        if req.corrects_note_id is None:
            raise ValidationFailed("corrects_note_id is required for a correction", code="correction_target_required")
        target = db.scalar(
            select(CaseNote).where(CaseNote.id == req.corrects_note_id, CaseNote.case_id == case.id)
        )
        if target is None or (target.is_sensitive and not can_sensitive):
            raise NotFound("Note to correct was not found on this case", code="note_not_found")
        is_sensitive = is_sensitive or target.is_sensitive
    elif req.corrects_note_id is not None:
        raise ValidationFailed("corrects_note_id is only valid for CORRECTION notes", code="unexpected_correction_target")

    note = CaseNote(
        id=note_id, case_id=case.id, author_user_id=actor.id, note_text=req.note_text,
        note_type=req.note_type, is_sensitive=is_sensitive, corrects_note_id=req.corrects_note_id,
    )
    db.add(note)
    record_audit(
        db, action=AuditAction.NOTE_ADDED, entity_type="case_note", entity_id=note.id, actor_user_id=actor.id,
        case=case, after={"note_type": req.note_type, "is_sensitive": is_sensitive, "corrects_note_id": req.corrects_note_id},
    )
    db.commit()


# --- History -------------------------------------------------------------------------------------


def get_history(db: Session, actor: User, case_id: uuid.UUID) -> CaseHistory:
    require_permission(actor, Permission.CASE_READ)
    load_case(db, actor, case_id)
    statuses = list(
        db.scalars(
            select(CaseStatusHistory)
            .where(CaseStatusHistory.case_id == case_id)
            .order_by(CaseStatusHistory.created_at, CaseStatusHistory.id)
        )
    )
    assignments = list(
        db.scalars(
            select(CaseAssignment)
            .where(CaseAssignment.case_id == case_id)
            .order_by(CaseAssignment.assigned_at, CaseAssignment.id)
        )
    )
    status_views = [StatusHistoryRead.model_validate(row) for row in statuses]
    if not has_permission(actor, Permission.NOTE_SENSITIVE):
        status_views = [v.model_copy(update={"verification_evidence": None}) for v in status_views]
    return CaseHistory(
        status_history=status_views,
        assignments=[AssignmentRead.model_validate(row) for row in assignments],
    )


# --- Matches -------------------------------------------------------------------------------------


def _safeguarding_review_required(case: Case) -> bool:
    """Uses only an explicit operator flag. Automatic age rules are not a substitute for an approved procedure."""
    if case.case_type != CaseType.FOUND_PERSON:
        return False
    return bool(case.found_person.safeguarding_review_required)


def _counterpart(actor: User, other: Case, in_scope: bool):
    summary = to_summary(other, in_scope=in_scope)
    if in_scope or has_permission(actor, Permission.CASE_SEARCH_ALL_STATIONS):
        return summary
    # Help-desk staff see that a counterpart exists at another station, not who or where.
    return summary.model_copy(update={"title": None, "location": None, "event_at": None, "location_id": None})


def _match_view(db: Session, actor: User, match: CaseMatch, case_id: uuid.UUID) -> MatchRead:
    other_id = match.right_case_id if match.left_case_id == case_id else match.left_case_id
    other = case_repo.get_case(db, other_id, true())
    versions = dict(db.execute(select(Case.id, Case.version).where(Case.id.in_((match.left_case_id, match.right_case_id)))).all())
    in_scope = case_repo.case_exists_in_scope(db, other_id, case_visibility_clause(actor))
    sensitive = has_permission(actor, Permission.NOTE_SENSITIVE)
    can_see_explanation = in_scope or has_permission(actor, Permission.CASE_SEARCH_ALL_STATIONS)
    return MatchRead(
        id=match.id,
        match_domain=match.match_domain,
        status=match.status,
        source=match.source,
        score=float(match.score) if can_see_explanation and match.score is not None else None,
        confidence_band=match.confidence_band if can_see_explanation else None,
        reasons=match.reasons if can_see_explanation else None,
        engine_version=match.engine_version,
        left_case_id=match.left_case_id,
        right_case_id=match.right_case_id,
        left_case_version=versions[match.left_case_id],
        right_case_version=versions[match.right_case_id],
        counterpart=_counterpart(actor, other, in_scope),
        proposed_by_user_id=match.proposed_by_user_id,
        rationale=match.rationale if can_see_explanation else None,
        reviewed_by_user_id=match.reviewed_by_user_id,
        reviewed_at=match.reviewed_at,
        review_evidence=match.review_evidence if sensitive else None,
        revoked_by_user_id=match.revoked_by_user_id,
        revoked_at=match.revoked_at,
        revocation_reason=match.revocation_reason if sensitive else None,
        created_at=match.created_at,
    )


def list_matches(db: Session, actor: User, case_id: uuid.UUID) -> list[MatchRead]:
    require_permission(actor, Permission.CASE_READ)
    load_case(db, actor, case_id)
    rows = db.scalars(
        select(CaseMatch)
        .where(or_(CaseMatch.left_case_id == case_id, CaseMatch.right_case_id == case_id))
        .order_by(CaseMatch.score.desc().nulls_last(), CaseMatch.created_at, CaseMatch.id)
    )
    return [_match_view(db, actor, m, case_id) for m in rows]


def orient_pair(a: Case, b: Case) -> tuple[Case, Case, MatchDomain] | None:
    """Returns (left, right, domain) for a compatible pair, or None."""
    pair = (CaseType(a.case_type), CaseType(b.case_type))
    if pair in MATCH_PAIRS:
        return a, b, MATCH_PAIRS[pair]
    if pair[::-1] in MATCH_PAIRS:
        return b, a, MATCH_PAIRS[pair[::-1]]
    return None


def propose_match(db: Session, actor: User, case_id: uuid.UUID, req: MatchProposeRequest) -> MatchRead:
    require_permission(actor, Permission.MATCH_PROPOSE)
    case = load_case(db, actor, case_id)
    other_visibility = (
        true() if has_permission(actor, Permission.CASE_SEARCH_ALL_STATIONS) else case_visibility_clause(actor)
    )
    other = case_repo.get_case(db, req.other_case_id, other_visibility)
    if other is None or other.id == case.id:
        raise NotFound("Other case not found", code="case_not_found")

    oriented = orient_pair(case, other)
    if oriented is None:
        raise ValidationFailed(
            "Matches link a missing person to a found person, or a lost item to a found item",
            code="incompatible_case_types",
        )
    left, right, domain = oriented
    if left.status == S.CLOSED or right.status == S.CLOSED:
        raise Conflict("Closed cases cannot be matched", code="case_closed")

    match = CaseMatch(
        id=uuid.uuid4(),
        match_domain=domain,
        left_case_id=left.id,
        left_case_type=left.case_type,
        right_case_id=right.id,
        right_case_type=right.case_type,
        status=MatchStatus.PROPOSED,
        source=MatchSource.MANUAL,
        proposed_by_user_id=actor.id,
        rationale=req.rationale,
    )
    db.add(match)
    for side, counterpart in ((case, other), (other, case)):
        record_audit(
            db, action=AuditAction.MATCH_PROPOSED, entity_type="case_match", entity_id=match.id,
            actor_user_id=actor.id, case=side, after={"counterpart_case_number": counterpart.case_number},
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _constraint(exc) == "uq_case_matches_pair":
            raise Conflict("These cases are already linked by a match", code="duplicate_match") from exc
        raise
    return _match_view(db, actor, match, case_id)


def _constraint(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _lock_match_cases(db: Session, match: CaseMatch) -> dict[uuid.UUID, Case]:
    """Locks both cases in a stable order so concurrent reviews cannot deadlock."""
    return {
        cid: case_repo.get_case(db, cid, true(), for_update=True)
        for cid in sorted((match.left_case_id, match.right_case_id))
    }


def _load_match_for_review(db: Session, actor: User, case_id: uuid.UUID, match_id: uuid.UUID) -> CaseMatch:
    require_permission(actor, Permission.MATCH_REVIEW)
    load_case(db, actor, case_id)
    match = db.scalar(select(CaseMatch).where(CaseMatch.id == match_id).with_for_update())
    if match is None or case_id not in (match.left_case_id, match.right_case_id):
        raise NotFound("Match not found on this case", code="match_not_found")
    return match


def review_match(
    db: Session, actor: User, case_id: uuid.UUID, match_id: uuid.UUID, req: MatchReviewRequest,
    *, idempotency_key: str | None = None,
) -> tuple[MatchRead, bool]:
    require_permission(actor, Permission.MATCH_REVIEW)
    replayed = run_idempotent_operation(
        db, user=actor, key=idempotency_key, endpoint=f"review:{match_id}", payload=req.model_dump(),
        resource_id=match_id, operation=lambda: _review(db, actor, case_id, match_id, req),
    )
    match = db.get(CaseMatch, match_id)
    return _match_view(db, actor, match, case_id), replayed


def _review(db: Session, actor: User, case_id: uuid.UUID, match_id: uuid.UUID, req: MatchReviewRequest) -> None:
    match = _load_match_for_review(db, actor, case_id, match_id)
    if match.status != MatchStatus.PROPOSED:
        raise Conflict(f"Match has already been {match.status.lower()}", code="match_already_reviewed")
    confirming = req.decision is MatchStatus.CONFIRMED
    if confirming and match.proposed_by_user_id == actor.id:
        raise Forbidden("A match must be confirmed by someone other than its proposer", code="separation_of_duties")

    cases = _lock_match_cases(db, match)
    check_version(cases[match.left_case_id], req.left_case_version)
    check_version(cases[match.right_case_id], req.right_case_version)

    if confirming:
        blocked = {
            str(c.id): c.status for c in cases.values() if CaseStatus(c.status) not in MATCH_CONFIRMATION_SOURCES
        }
        if blocked:
            raise Conflict(
                "Both cases must be active and not already matched to confirm; reopen or revoke first",
                code="case_not_confirmable",
                details={"blocked_cases": blocked, "confirmable_statuses": sorted(MATCH_CONFIRMATION_SOURCES)},
            )
        conflicting = db.scalar(
            select(CaseMatch.id).where(
                CaseMatch.status == MatchStatus.CONFIRMED,
                or_(CaseMatch.left_case_id == match.left_case_id, CaseMatch.right_case_id == match.right_case_id),
            )
        )
        if conflicting is not None:
            raise Conflict(
                "One of these cases is already confirmed against another case",
                code="conflicting_confirmed_match",
                details={"confirmed_match_id": conflicting},
            )

    match.status = req.decision
    match.reviewed_by_user_id = actor.id
    match.reviewed_at = utcnow()
    match.review_evidence = req.evidence
    for case in cases.values():
        record_audit(
            db, action=AuditAction.MATCH_REVIEWED, entity_type="case_match", entity_id=match.id,
            actor_user_id=actor.id, case=case,
            after={"decision": req.decision, "source": match.source, "score": float(match.score) if match.score is not None else None},
        )
        if confirming:
            _apply_transition(db, case, S.MATCH_FOUND, actor, "Match confirmed by reviewing officer", match_id=match.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _constraint(exc) in ("uq_case_matches_confirmed_left", "uq_case_matches_confirmed_right"):
            raise Conflict(
                "One of these cases is already confirmed against another case", code="conflicting_confirmed_match"
            ) from exc
        raise


def revoke_match(
    db: Session, actor: User, case_id: uuid.UUID, match_id: uuid.UUID, req: MatchRevokeRequest
) -> MatchRead:
    """Withdraws a confirmed match that proved wrong. Cases return to active work; history is kept."""
    match = _load_match_for_review(db, actor, case_id, match_id)
    if match.status != MatchStatus.CONFIRMED:
        raise Conflict("Only a confirmed match can be revoked", code="match_not_confirmed")
    cases = _lock_match_cases(db, match)
    inactive = {str(c.id): c.status for c in cases.values() if c.status in INACTIVE_STATUSES}
    if inactive:
        raise Conflict(
            "Reopen resolved or closed cases before revoking their match",
            code="case_inactive", details={"inactive_cases": inactive},
        )
    match.status = MatchStatus.REVOKED
    match.revoked_by_user_id = actor.id
    match.revoked_at = utcnow()
    match.revocation_reason = req.reason
    for case in cases.values():
        record_audit(
            db, action=AuditAction.MATCH_REVOKED, entity_type="case_match", entity_id=match.id,
            actor_user_id=actor.id, case=case, reason=req.reason,
        )
        if case.status == S.MATCH_FOUND:
            target = S.ASSIGNED if case.assigned_to_user_id else S.UNDER_REVIEW
            _apply_transition(db, case, target, actor, "Confirmed match revoked", match_id=match.id)
    db.commit()
    return _match_view(db, actor, match, case_id)


# --- Audit ---------------------------------------------------------------------------------------


def list_audit_events(
    db: Session,
    actor: User,
    *,
    case_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[AuditEvent], int]:
    require_permission(actor, Permission.AUDIT_READ)
    conditions = []
    if case_id:
        conditions.append(AuditEvent.case_id == case_id)
    if actor_user_id:
        conditions.append(AuditEvent.actor_user_id == actor_user_id)
    if action:
        conditions.append(AuditEvent.action == action)
    if created_from:
        conditions.append(AuditEvent.created_at >= created_from)
    if created_to:
        conditions.append(AuditEvent.created_at < created_to)
    total = db.scalar(select(func.count(AuditEvent.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total
