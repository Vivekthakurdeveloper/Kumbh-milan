"""Structured claimant verification and physical handover workflow."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.crypto import decrypt_text, encrypt_text
from app.common.errors import Conflict, NotFound, ValidationFailed
from app.common.time import utcnow
from app.models import (
    AuditEvent,
    Case,
    CaseAttachment,
    CaseHandover,
    CaseMatch,
    FoundItemDetails,
    User,
)
from app.models.enums import (
    CaseStatus,
    CaseType,
    CustodyStatus,
    HandoverKind,
    HandoverStatus,
    IdentityVerificationMethod,
    MatchDomain,
    MatchStatus,
)
from app.schemas.handover import (
    HandoverCompleteRequest,
    HandoverCreateRequest,
    HandoverHistoryEventRead,
    HandoverRead,
    HandoverVerifyRequest,
)
from app.services.access import load_case
from app.services.idempotency import run_idempotent, run_idempotent_operation

ACTIVE_HANDOVER_STATUSES = (HandoverStatus.CLAIMANT_RECORDED, HandoverStatus.VERIFIED)
INACTIVE_CASE_STATUSES = (CaseStatus.RESOLVED, CaseStatus.CLOSED)


def _encrypt_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    return encrypt_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def _decrypt_payload(ciphertext: str | None) -> dict[str, Any]:
    if not ciphertext:
        return {}
    value = json.loads(decrypt_text(ciphertext))
    return value if isinstance(value, dict) else {}


def _check_case_version(case: Case, expected: int | None) -> None:
    if expected is not None and expected != case.version:
        raise Conflict(
            "The case changed since you loaded it; reload and try again",
            code="case_changed",
            details={"case_id": case.id, "expected_version": expected, "current_version": case.version},
        )


def _check_handover_version(handover: CaseHandover, expected: int | None) -> None:
    if expected is not None and expected != handover.version:
        raise Conflict(
            "The handover changed since you loaded it; reload and try again",
            code="handover_changed",
            details={
                "handover_id": handover.id,
                "expected_version": expected,
                "current_version": handover.version,
            },
        )


def _kind(case: Case) -> HandoverKind:
    case_type = CaseType(case.case_type)
    if case_type in (CaseType.MISSING_PERSON, CaseType.FOUND_PERSON):
        return HandoverKind.PERSON_REUNIFICATION
    return HandoverKind.ITEM_RETURN


def _confirmed_match(
    db: Session,
    case: Case,
    match_id: uuid.UUID | None,
    kind: HandoverKind,
) -> tuple[CaseMatch | None, uuid.UUID | None]:
    if match_id is None:
        if kind is HandoverKind.PERSON_REUNIFICATION:
            raise ValidationFailed(
                "A confirmed person match is required before claimant handover",
                code="match_required",
            )
        if CaseType(case.case_type) is not CaseType.FOUND_ITEM:
            raise ValidationFailed(
                "A match-less item handover must be opened from the found-item case",
                code="match_required",
            )
        return None, None

    match = db.get(CaseMatch, match_id)
    if match is None or case.id not in (match.left_case_id, match.right_case_id):
        raise ValidationFailed("match_id does not refer to this case", code="invalid_match")
    if MatchStatus(match.status) is not MatchStatus.CONFIRMED:
        raise ValidationFailed("Only a confirmed match can support handover", code="match_not_confirmed")
    expected_domain = MatchDomain.PERSON if kind is HandoverKind.PERSON_REUNIFICATION else MatchDomain.ITEM
    if MatchDomain(match.match_domain) is not expected_domain:
        raise ValidationFailed("The match domain is incompatible with this handover", code="invalid_match")
    counterpart = match.right_case_id if case.id == match.left_case_id else match.left_case_id
    return match, counterpart


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _evidence_attachment_ids(
    db: Session,
    *,
    primary_case_id: uuid.UUID,
    counterpart_case_id: uuid.UUID | None,
    attachment_ids: list[uuid.UUID],
) -> list[str]:
    if not attachment_ids:
        return []
    if len(set(attachment_ids)) != len(attachment_ids):
        raise ValidationFailed("Evidence attachments must be unique", code="invalid_handover_attachment")
    rows = list(
        db.scalars(select(CaseAttachment).where(CaseAttachment.id.in_(attachment_ids)))
    )
    allowed_case_ids = {primary_case_id}
    if counterpart_case_id is not None:
        allowed_case_ids.add(counterpart_case_id)
    from app.services.attachments import is_downloadable

    if (
        len(rows) != len(attachment_ids)
        or any(
            row.case_id not in allowed_case_ids
            or row.deleted_at is not None
            or not is_downloadable(row)
            for row in rows
        )
    ):
        raise ValidationFailed(
            "Every handover evidence attachment must be a downloadable file on one of the linked cases",
            code="invalid_handover_attachment",
        )
    return [str(attachment_id) for attachment_id in attachment_ids]


def create_handover(
    db: Session,
    actor: User,
    case_id: uuid.UUID,
    req: HandoverCreateRequest,
    *,
    idempotency_key: str | None = None,
) -> tuple[CaseHandover, bool]:
    require_permission(actor, Permission.HANDOVER_MANAGE)
    case = load_case(db, actor, case_id)
    kind = _kind(case)

    handover_id = uuid.uuid4()
    claimant = {
        "full_name": req.claimant_full_name,
        "relationship": req.claimant_relationship,
        "mobile_number": req.claimant_mobile_number,
        "address": req.claimant_address,
        "identity_document_reference": req.identity_document_reference,
    }
    witness = None
    if req.witness_full_name is not None:
        witness = {
            "full_name": req.witness_full_name,
            "mobile_number": req.witness_mobile_number,
            "relationship": req.witness_relationship,
            "statement": req.witness_statement,
        }

    def _create() -> uuid.UUID:
        locked_case = load_case(db, actor, case_id, for_update=True)
        _check_case_version(locked_case, req.expected_case_version)
        if CaseStatus(locked_case.status) in INACTIVE_CASE_STATUSES or locked_case.anonymized_at is not None:
            raise Conflict("Inactive cases cannot start a handover", code="case_inactive")
        match, counterpart_id = _confirmed_match(db, locked_case, req.match_id, kind)
        evidence_attachment_ids = _evidence_attachment_ids(
            db,
            primary_case_id=locked_case.id,
            counterpart_case_id=counterpart_id,
            attachment_ids=req.evidence_attachment_ids,
        )

        completed_conditions = [CaseHandover.status == HandoverStatus.COMPLETED]
        if req.match_id is not None:
            completed_conditions.append(CaseHandover.match_id == req.match_id)
        else:
            completed_conditions.extend(
                [
                    CaseHandover.primary_case_id == locked_case.id,
                    CaseHandover.match_id.is_(None),
                ]
            )
        completed = db.scalar(select(CaseHandover.id).where(*completed_conditions))
        if completed is not None:
            raise Conflict(
                "A completed handover already exists for this case or match",
                code="handover_already_completed",
                details={"handover_id": completed},
            )

        active_scope = [
            CaseHandover.primary_case_id == locked_case.id,
            CaseHandover.counterpart_case_id == locked_case.id,
        ]
        if req.match_id is not None:
            active_scope.append(CaseHandover.match_id == req.match_id)
        active = db.scalar(
            select(CaseHandover.id).where(
                or_(*active_scope),
                CaseHandover.status.in_(ACTIVE_HANDOVER_STATUSES),
            )
        )
        if active is not None:
            raise Conflict(
                "An active handover already exists for this case or match",
                code="handover_session_open",
                details={"handover_id": active},
            )

        handover = CaseHandover(
            id=handover_id,
            primary_case_id=locked_case.id,
            counterpart_case_id=counterpart_id,
            match_id=match.id if match is not None else None,
            kind=kind,
            status=HandoverStatus.CLAIMANT_RECORDED,
            claimant_ciphertext=_encrypt_payload(claimant),
            identity_document_type=req.identity_document_type,
            witness_ciphertext=_encrypt_payload(witness),
            evidence_attachment_ids=evidence_attachment_ids,
            created_by_user_id=actor.id,
        )
        db.add(handover)
        record_audit(
            db,
            action=AuditAction.HANDOVER_CREATED,
            entity_type="case_handover",
            entity_id=handover.id,
            actor_user_id=actor.id,
            case=locked_case,
            after={
                "status": HandoverStatus.CLAIMANT_RECORDED,
                "kind": kind,
                "match_id": match.id if match is not None else None,
                "counterpart_case_id": counterpart_id,
                "claimant_recorded": True,
                "witness_recorded": witness is not None,
                "evidence_attachment_count": len(evidence_attachment_ids),
                "identity_document_type": req.identity_document_type,
            },
        )
        return handover.id

    try:
        resource_id, replayed = run_idempotent(
            db,
            user=actor,
            key=idempotency_key,
            endpoint=f"handover:create:{case.id}",
            payload=req.model_dump(mode="json"),
            create=_create,
        )
    except IntegrityError as exc:
        db.rollback()
        if (_constraint_name(exc) or "").startswith("uq_case_handovers_active"):
            raise Conflict("An active handover already exists", code="handover_session_open") from exc
        raise
    row = db.get(CaseHandover, resource_id)
    if row is None:
        raise NotFound("Handover not found", code="handover_not_found")
    return row, replayed


def _load_handover(
    db: Session,
    actor: User,
    case_id: uuid.UUID,
    handover_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> CaseHandover:
    require_permission(actor, Permission.HANDOVER_READ)
    load_case(db, actor, case_id)
    stmt = select(CaseHandover).where(
        CaseHandover.id == handover_id,
        or_(
            CaseHandover.primary_case_id == case_id,
            CaseHandover.counterpart_case_id == case_id,
        ),
    )
    row = db.scalar(stmt.with_for_update() if for_update else stmt)
    if row is None:
        raise NotFound("Handover not found", code="handover_not_found")
    return row


def list_handovers(db: Session, actor: User, case_id: uuid.UUID) -> list[CaseHandover]:
    require_permission(actor, Permission.HANDOVER_READ)
    load_case(db, actor, case_id)
    return list(
        db.scalars(
            select(CaseHandover)
            .where(
                or_(
                    CaseHandover.primary_case_id == case_id,
                    CaseHandover.counterpart_case_id == case_id,
                )
            )
            .order_by(CaseHandover.created_at, CaseHandover.id)
        )
    )


def get_handover(
    db: Session, actor: User, case_id: uuid.UUID, handover_id: uuid.UUID
) -> CaseHandover:
    return _load_handover(db, actor, case_id, handover_id)


def verify_handover(
    db: Session,
    actor: User,
    case_id: uuid.UUID,
    handover_id: uuid.UUID,
    req: HandoverVerifyRequest,
    *,
    idempotency_key: str | None = None,
) -> tuple[CaseHandover, bool]:
    require_permission(actor, Permission.HANDOVER_MANAGE)

    def _operation() -> None:
        row = _load_handover(db, actor, case_id, handover_id, for_update=True)
        _check_handover_version(row, req.expected_version)
        if HandoverStatus(row.status) is not HandoverStatus.CLAIMANT_RECORDED:
            raise Conflict("Handover is not awaiting verification", code="handover_state")
        case = load_case(db, actor, case_id)
        if req.decision == "REJECTED":
            row.status = HandoverStatus.REJECTED
            row.rejection_ciphertext = _encrypt_payload({"reason": req.evidence})
            row.rejected_by_user_id = actor.id
            row.rejected_at = utcnow()
            action = AuditAction.HANDOVER_REJECTED
        else:
            if (
                req.verification_method is IdentityVerificationMethod.GOVERNMENT_ID
                and row.identity_document_type is None
            ):
                raise ValidationFailed(
                    "Government-ID verification requires claimant document details",
                    code="identity_document_required",
                )
            row.status = HandoverStatus.VERIFIED
            row.verification_method = req.verification_method
            row.verification_ciphertext = _encrypt_payload({"evidence": req.evidence})
            row.verified_by_user_id = actor.id
            row.verified_at = utcnow()
            action = AuditAction.HANDOVER_VERIFIED
        record_audit(
            db,
            action=action,
            entity_type="case_handover",
            entity_id=row.id,
            actor_user_id=actor.id,
            case=case,
            after={"status": row.status, "verification_method": row.verification_method},
        )
        db.commit()

    replayed = run_idempotent_operation(
        db,
        user=actor,
        key=idempotency_key,
        endpoint=f"handover:verify:{handover_id}",
        payload=req.model_dump(mode="json"),
        resource_id=handover_id,
        operation=_operation,
    )
    row = _load_handover(db, actor, case_id, handover_id)
    return row, replayed


def _require_released_found_item(db: Session, row: CaseHandover) -> None:
    for case_id in (row.primary_case_id, row.counterpart_case_id):
        if case_id is None:
            continue
        case = db.get(Case, case_id)
        if case is None or CaseType(case.case_type) is not CaseType.FOUND_ITEM:
            continue
        detail = db.get(FoundItemDetails, case_id)
        if detail is None or CustodyStatus(detail.custody_status) is not CustodyStatus.RELEASED:
            raise Conflict(
                "Release the found item from custody before completing handover",
                code="custody_not_released",
            )


def complete_handover(
    db: Session,
    actor: User,
    case_id: uuid.UUID,
    handover_id: uuid.UUID,
    req: HandoverCompleteRequest,
    *,
    idempotency_key: str | None = None,
) -> tuple[CaseHandover, bool]:
    require_permission(actor, Permission.HANDOVER_MANAGE)

    def _operation() -> None:
        row = _load_handover(db, actor, case_id, handover_id, for_update=True)
        _check_handover_version(row, req.expected_version)
        if HandoverStatus(row.status) is not HandoverStatus.VERIFIED:
            raise Conflict("Only a verified handover can be completed", code="handover_state")
        _require_released_found_item(db, row)
        case = load_case(db, actor, case_id)
        row.status = HandoverStatus.COMPLETED
        row.handover_location = req.handover_location
        row.handover_at = req.handover_at
        row.acknowledgement_ciphertext = _encrypt_payload({"acknowledgement": req.acknowledgement})
        row.completed_by_user_id = actor.id
        row.completed_at = utcnow()
        record_audit(
            db,
            action=AuditAction.HANDOVER_COMPLETED,
            entity_type="case_handover",
            entity_id=row.id,
            actor_user_id=actor.id,
            case=case,
            after={
                "status": HandoverStatus.COMPLETED,
                "kind": row.kind,
                "match_id": row.match_id,
                "handover_at": row.handover_at,
                "handover_location": row.handover_location,
                "acknowledgement_recorded": True,
            },
        )
        db.commit()

    replayed = run_idempotent_operation(
        db,
        user=actor,
        key=idempotency_key,
        endpoint=f"handover:complete:{handover_id}",
        payload=req.model_dump(mode="json"),
        resource_id=handover_id,
        operation=_operation,
    )
    row = _load_handover(db, actor, case_id, handover_id)
    return row, replayed


def to_read(row: CaseHandover) -> HandoverRead:
    claimant = _decrypt_payload(row.claimant_ciphertext) if row.anonymized_at is None else {}
    witness = _decrypt_payload(row.witness_ciphertext) if row.anonymized_at is None else {}
    verification = _decrypt_payload(row.verification_ciphertext) if row.anonymized_at is None else {}
    acknowledgement = (
        _decrypt_payload(row.acknowledgement_ciphertext) if row.anonymized_at is None else {}
    )
    rejection = _decrypt_payload(row.rejection_ciphertext) if row.anonymized_at is None else {}
    return HandoverRead(
        id=row.id,
        primary_case_id=row.primary_case_id,
        counterpart_case_id=row.counterpart_case_id,
        match_id=row.match_id,
        kind=row.kind,
        status=row.status,
        claimant_full_name=claimant.get("full_name"),
        claimant_relationship=claimant.get("relationship"),
        claimant_mobile_number=claimant.get("mobile_number"),
        claimant_address=claimant.get("address"),
        identity_document_type=row.identity_document_type,
        identity_document_reference=claimant.get("identity_document_reference"),
        witness_full_name=witness.get("full_name"),
        witness_mobile_number=witness.get("mobile_number"),
        witness_relationship=witness.get("relationship"),
        witness_statement=witness.get("statement"),
        evidence_attachment_ids=[uuid.UUID(value) for value in row.evidence_attachment_ids],
        verification_method=row.verification_method,
        verification_evidence=verification.get("evidence"),
        verified_by_user_id=row.verified_by_user_id,
        verified_at=row.verified_at,
        handover_location=row.handover_location,
        handover_at=row.handover_at,
        acknowledgement=acknowledgement.get("acknowledgement"),
        completed_by_user_id=row.completed_by_user_id,
        completed_at=row.completed_at,
        rejection_reason=rejection.get("reason"),
        rejected_by_user_id=row.rejected_by_user_id,
        rejected_at=row.rejected_at,
        created_by_user_id=row.created_by_user_id,
        anonymized_at=row.anonymized_at,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def handover_history(
    db: Session,
    actor: User,
    case_id: uuid.UUID,
    handover_id: uuid.UUID,
) -> list[HandoverHistoryEventRead]:
    _load_handover(db, actor, case_id, handover_id)
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == "case_handover", AuditEvent.entity_id == handover_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    )
    result: list[HandoverHistoryEventRead] = []
    for event in rows:
        status_value = (event.after_state or {}).get("status")
        try:
            status = HandoverStatus(status_value) if status_value is not None else None
        except ValueError:
            status = None
        result.append(
            HandoverHistoryEventRead(
                id=event.id,
                action=event.action,
                actor_user_id=event.actor_user_id,
                status=status,
                created_at=event.created_at,
            )
        )
    return result


def require_completed_for_resolve(
    db: Session,
    case: Case,
    *,
    match_id: uuid.UUID | None,
) -> None:
    conditions = [
        or_(
            CaseHandover.primary_case_id == case.id,
            CaseHandover.counterpart_case_id == case.id,
        ),
        CaseHandover.status == HandoverStatus.COMPLETED,
        CaseHandover.anonymized_at.is_(None),
    ]
    if match_id is not None:
        conditions.append(CaseHandover.match_id == match_id)
    handover_id = db.scalar(select(CaseHandover.id).where(*conditions).limit(1))
    if handover_id is None:
        raise ValidationFailed(
            "Complete structured claimant verification and handover before resolving this person case",
            code="handover_not_completed",
        )
