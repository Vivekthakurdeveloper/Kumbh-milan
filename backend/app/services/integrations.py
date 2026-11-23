"""Destinations, public alerts (PA / LED-VMD / social), government case exports, and the outbox dispatcher.

BE-301 framework with BE-302..BE-305 channel behaviour. Public payloads carry only approved alert text and a
case number; government exports carry a minimal case summary with a recorded legal basis. Contacts, names,
biometrics, notes, and attachments are never dispatched.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.crypto import decrypt_text, encrypt_text
from app.common.errors import Conflict, Forbidden, InvalidTransition, NotFound, Unauthenticated, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.integrations import (
    GOVERNMENT_SYSTEMS,
    AdapterError,
    Target,
    adapter_for,
    assert_public_safe,
    verify_signature,
)
from app.models import (
    Case,
    InboundIntegrationMessage,
    IntegrationDestination,
    OutboundDispatch,
    PublicAlert,
    Station,
    User,
)
from app.models.enums import (
    AlertStatus,
    DispatchOperation,
    DispatchStatus,
    InboundMessageStatus,
    IntegrationAdapter,
    IntegrationHealth,
    IntegrationKind,
)
from app.observability.metrics import DISPATCH_OUTCOMES
from app.services.access import load_case

logger = logging.getLogger("kumbh_setu.integrations")
A, D = AlertStatus, DispatchStatus
PUBLIC_KINDS = (IntegrationKind.PUBLIC_ADDRESS, IntegrationKind.DISPLAY, IntegrationKind.SOCIAL)
STALE_SENDING = timedelta(minutes=5)


# --- Destinations --------------------------------------------------------------------------------


def create_destination(db: Session, actor: User, data: dict) -> IntegrationDestination:
    require_permission(actor, Permission.INTEGRATION_MANAGE)
    kind = IntegrationKind(data["kind"])
    if kind is IntegrationKind.GOVERNMENT and data["system"] not in GOVERNMENT_SYSTEMS:
        raise ValidationFailed(f"system must be one of {', '.join(GOVERNMENT_SYSTEMS)}", code="invalid_system")
    if db.scalar(select(IntegrationDestination.id).where(IntegrationDestination.code == data["code"])):
        raise Conflict("A destination with this code already exists", code="destination_exists")
    secret = data.pop("secret", None)
    row = IntegrationDestination(id=uuid.uuid4(), **data, secret_ciphertext=encrypt_text(secret) if secret else None)
    if row.adapter == IntegrationAdapter.WEBHOOK and not row.endpoint_url:
        raise ValidationFailed("WEBHOOK destinations need endpoint_url", code="endpoint_required")
    db.add(row)
    record_audit(db, action=AuditAction.INTEGRATION_DESTINATION_CREATED, entity_type="integration_destination",
                 entity_id=row.id, actor_user_id=actor.id,
                 after={"code": row.code, "kind": kind, "system": row.system, "adapter": row.adapter,
                        "is_enabled": row.is_enabled})
    db.commit()
    db.refresh(row)
    return row


def update_destination(db: Session, actor: User, destination_id: uuid.UUID, changes: dict) -> IntegrationDestination:
    require_permission(actor, Permission.INTEGRATION_MANAGE)
    row = get_destination(db, actor, destination_id)
    secret = changes.pop("secret", None)
    before = {k: getattr(row, k) for k in changes}
    for key, value in changes.items():
        setattr(row, key, value)
    if secret:
        row.secret_ciphertext = encrypt_text(secret)
    if changes.get("is_enabled"):
        row.circuit_open_until, row.consecutive_failures = None, 0
    if row.adapter == IntegrationAdapter.WEBHOOK and not row.endpoint_url:
        raise ValidationFailed("WEBHOOK destinations need endpoint_url", code="endpoint_required")
    record_audit(db, action=AuditAction.INTEGRATION_DESTINATION_UPDATED, entity_type="integration_destination",
                 entity_id=row.id, actor_user_id=actor.id, before=before,
                 after={**changes, "secret_rotated": bool(secret)})
    db.commit()
    db.refresh(row)
    return row


def get_destination(db: Session, actor: User, destination_id: uuid.UUID) -> IntegrationDestination:
    require_permission(actor, Permission.ALERT_DRAFT)
    row = db.get(IntegrationDestination, destination_id)
    if row is None:
        raise NotFound("Destination not found", code="destination_not_found")
    return row


def list_destinations(db: Session, actor: User, kind: IntegrationKind | None) -> list[IntegrationDestination]:
    require_permission(actor, Permission.ALERT_DRAFT)
    stmt = select(IntegrationDestination).order_by(IntegrationDestination.kind, IntegrationDestination.code)
    if kind is not None:
        stmt = stmt.where(IntegrationDestination.kind == kind)
    return list(db.scalars(stmt))


# --- Public alerts -------------------------------------------------------------------------------


def _public_destinations(db: Session, ids: list[uuid.UUID]) -> list[IntegrationDestination]:
    if not ids:
        raise ValidationFailed("Select at least one destination", code="destinations_required")
    rows = list(db.scalars(select(IntegrationDestination).where(IntegrationDestination.id.in_(ids))))
    if len(rows) != len(set(ids)):
        raise ValidationFailed("Unknown destination", code="invalid_destination")
    if any(r.kind not in PUBLIC_KINDS for r in rows):
        raise ValidationFailed("Alerts can target PA, display, or social destinations only", code="invalid_destination")
    return rows


def create_alert(db: Session, actor: User, data: dict) -> PublicAlert:
    require_permission(actor, Permission.ALERT_DRAFT)
    assert_public_safe(data["title_en"], data["title_hi"], data["body_en"], data["body_hi"])
    _public_destinations(db, data["destination_ids"])
    case_id = data.pop("case_id", None)
    if case_id is not None:
        load_case(db, actor, case_id)
    if data["expires_at"] <= data["starts_at"]:
        raise ValidationFailed("expires_at must be after starts_at", code="invalid_period")
    alert = PublicAlert(
        id=uuid.uuid4(), case_id=case_id, **{**data, "destination_ids": [str(d) for d in data["destination_ids"]]},
        status=A.DRAFT, created_by_user_id=actor.id,
    )
    db.add(alert)
    record_audit(db, action=AuditAction.ALERT_CREATED, entity_type="public_alert", entity_id=alert.id,
                 actor_user_id=actor.id, after={"destinations": alert.destination_ids, "case_id": case_id})
    db.commit()
    db.refresh(alert)
    return alert


def get_alert(db: Session, actor: User, alert_id: uuid.UUID, *, for_update: bool = False) -> PublicAlert:
    require_permission(actor, Permission.ALERT_DRAFT)
    stmt = select(PublicAlert).where(PublicAlert.id == alert_id)
    if for_update:
        stmt = stmt.with_for_update()
    alert = db.scalar(stmt)
    if alert is None:
        raise NotFound("Alert not found", code="alert_not_found")
    return alert


def list_alerts(db: Session, actor: User, status: AlertStatus | None, *, limit: int, offset: int):
    require_permission(actor, Permission.ALERT_DRAFT)
    conditions = [PublicAlert.status == status] if status else []
    total = db.scalar(select(func.count()).select_from(PublicAlert).where(*conditions)) or 0
    rows = list(db.scalars(select(PublicAlert).where(*conditions).order_by(PublicAlert.created_at.desc())
                           .limit(limit).offset(offset)))
    return rows, total


def update_alert(db: Session, actor: User, alert_id: uuid.UUID, changes: dict) -> PublicAlert:
    alert = get_alert(db, actor, alert_id, for_update=True)
    if alert.status != A.DRAFT:
        raise InvalidTransition("Only draft alerts can be edited", code="alert_not_draft")
    if "destination_ids" in changes:
        _public_destinations(db, changes["destination_ids"])
        changes["destination_ids"] = [str(d) for d in changes["destination_ids"]]
    for key, value in changes.items():
        setattr(alert, key, value)
    assert_public_safe(alert.title_en, alert.title_hi, alert.body_en, alert.body_hi)
    if alert.expires_at <= alert.starts_at:
        raise ValidationFailed("expires_at must be after starts_at", code="invalid_period")
    record_audit(db, action=AuditAction.ALERT_UPDATED, entity_type="public_alert", entity_id=alert.id,
                 actor_user_id=actor.id, after={"fields": sorted(changes)})
    db.commit()
    db.refresh(alert)
    return alert


def _alert_payload(db: Session, alert: PublicAlert, destination: IntegrationDestination) -> dict:
    case_number = db.scalar(select(Case.case_number).where(Case.id == alert.case_id)) if alert.case_id else None
    return {
        "alert_id": str(alert.id),
        "title": {"en": alert.title_en, "hi": alert.title_hi},
        "body": {"en": alert.body_en, "hi": alert.body_hi},
        "priority": alert.priority,
        "starts_at": alert.starts_at.isoformat(),
        "expires_at": alert.expires_at.isoformat(),
        "case_number": case_number,
        "zones": destination.config.get("zones", []),
    }


def _enqueue(
    db: Session, destination: IntegrationDestination, operation: DispatchOperation, payload: dict, key: str, *,
    alert_id: uuid.UUID | None = None, case_id: uuid.UUID | None = None, legal_basis: str | None = None,
    not_before=None, actor_id: uuid.UUID | None = None,
) -> OutboundDispatch:
    idempotency_key = hashlib.sha256(key.encode()).hexdigest()
    existing = db.scalar(select(OutboundDispatch).where(OutboundDispatch.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    row = OutboundDispatch(
        id=uuid.uuid4(), destination_id=destination.id, alert_id=alert_id, case_id=case_id, operation=operation,
        payload=payload, status=D.PENDING, next_attempt_at=not_before or func.now(), idempotency_key=idempotency_key,
        legal_basis=legal_basis, requested_by_user_id=actor_id,
    )
    db.add(row)
    return row


def approve_alert(db: Session, actor: User, alert_id: uuid.UUID) -> PublicAlert:
    require_permission(actor, Permission.ALERT_APPROVE)
    alert = get_alert(db, actor, alert_id, for_update=True)
    if alert.status != A.DRAFT:
        raise InvalidTransition("Only draft alerts can be approved", code="alert_not_draft")
    if alert.created_by_user_id == actor.id:
        raise Forbidden("A second authorised person must approve the alert", code="self_approval")
    now = utcnow()
    if alert.expires_at <= now:
        raise ValidationFailed("This alert has already expired", code="alert_expired")
    destinations = _public_destinations(db, [uuid.UUID(d) for d in alert.destination_ids])
    if any(d.kind is IntegrationKind.SOCIAL for d in destinations) and alert.case_id and not alert.consent_reference:
        raise ValidationFailed(
            "Social posts about a case need recorded family consent (consent_reference)", code="consent_required"
        )
    alert.status, alert.approved_by_user_id, alert.approved_at = A.APPROVED, actor.id, now
    for destination in destinations:
        _enqueue(db, destination, DispatchOperation.PUBLISH, _alert_payload(db, alert, destination),
                 f"alert:{alert.id}:{destination.id}:PUBLISH", alert_id=alert.id, case_id=alert.case_id,
                 not_before=max(alert.starts_at, now), actor_id=actor.id)
    record_audit(db, action=AuditAction.ALERT_APPROVED, entity_type="public_alert", entity_id=alert.id,
                 actor_user_id=actor.id, after={"destinations": alert.destination_ids})
    db.commit()
    db.refresh(alert)
    return alert


def _retract(db: Session, alert: PublicAlert, actor_id: uuid.UUID | None) -> None:
    """Cancels undelivered publishes and queues a withdrawal wherever the alert was delivered."""
    db.execute(
        update(OutboundDispatch)
        .where(OutboundDispatch.alert_id == alert.id, OutboundDispatch.operation == DispatchOperation.PUBLISH,
               OutboundDispatch.status.in_((D.PENDING, D.FAILED)))
        .values(status=D.CANCELLED, next_attempt_at=None)
    )
    delivered = db.scalars(
        select(OutboundDispatch).where(
            OutboundDispatch.alert_id == alert.id, OutboundDispatch.operation == DispatchOperation.PUBLISH,
            OutboundDispatch.status.in_((D.DELIVERED, D.SENDING)),
        )
    ).all()
    for publish in delivered:
        destination = db.get(IntegrationDestination, publish.destination_id)
        _enqueue(db, destination, DispatchOperation.WITHDRAW,
                 {"alert_id": str(alert.id), "remote_reference": publish.remote_reference},
                 f"alert:{alert.id}:{destination.id}:WITHDRAW", alert_id=alert.id, actor_id=actor_id)


def withdraw_alert(db: Session, actor: User, alert_id: uuid.UUID, reason: str) -> PublicAlert:
    require_permission(actor, Permission.ALERT_DRAFT)
    alert = get_alert(db, actor, alert_id, for_update=True)
    if alert.status not in (A.DRAFT, A.APPROVED, A.PUBLISHED):
        raise InvalidTransition("This alert is no longer active", code="alert_inactive")
    alert.status, alert.withdrawn_by_user_id, alert.withdrawn_at = A.WITHDRAWN, actor.id, utcnow()
    alert.withdraw_reason = reason
    _retract(db, alert, actor.id)
    record_audit(db, action=AuditAction.ALERT_WITHDRAWN, entity_type="public_alert", entity_id=alert.id,
                 actor_user_id=actor.id, reason=reason)
    db.commit()
    db.refresh(alert)
    return alert


def expire_alerts(db: Session, *, limit: int = 100) -> int:
    rows = list(db.scalars(
        select(PublicAlert).where(PublicAlert.status.in_((A.APPROVED, A.PUBLISHED)), PublicAlert.expires_at <= func.now())
        .limit(limit).with_for_update(skip_locked=True)
    ))
    for alert in rows:
        alert.status = A.EXPIRED
        _retract(db, alert, None)
        record_audit(db, action=AuditAction.ALERT_EXPIRED, entity_type="public_alert", entity_id=alert.id,
                     actor_user_id=None)
    db.commit()
    return len(rows)


def active_alerts_for(db: Session, actor: User, destination_id: uuid.UUID) -> list[PublicAlert]:
    """Alerts currently live on a destination: publish delivered, not withdrawn, not expired."""
    get_destination(db, actor, destination_id)
    published = select(OutboundDispatch.alert_id).where(
        OutboundDispatch.destination_id == destination_id, OutboundDispatch.operation == DispatchOperation.PUBLISH,
        OutboundDispatch.status == D.DELIVERED,
    )
    return list(db.scalars(
        select(PublicAlert).where(PublicAlert.id.in_(published), PublicAlert.status == A.PUBLISHED,
                                  PublicAlert.expires_at > func.now())
        .order_by(PublicAlert.priority.desc(), PublicAlert.starts_at)
    ))


def alert_dispatches(db: Session, actor: User, alert_id: uuid.UUID) -> list[OutboundDispatch]:
    get_alert(db, actor, alert_id)
    return list(db.scalars(select(OutboundDispatch).where(OutboundDispatch.alert_id == alert_id)
                           .order_by(OutboundDispatch.created_at)))


# --- Government case export ----------------------------------------------------------------------


def _case_summary(db: Session, case: Case, system: str) -> dict:
    details = case.missing_person or case.found_person or case.lost_item or case.found_item
    station = db.get(Station, case.station_id)
    summary = {
        "source_system": "KUMBH_SETU",
        "target_system": system,
        "case_number": case.case_number,
        "case_type": case.case_type,
        "status": case.status,
        "urgency_level": case.urgency_level,
        "station_code": station.code if station else None,
        "registered_at": case.created_at.isoformat(),
        "age_group": getattr(details, "age_group", None),
        "gender": getattr(details, "gender", None) or getattr(details, "estimated_gender", None),
        "item_category": getattr(details, "item_category", None),
        "event_location": getattr(details, "last_seen_location", None) or getattr(details, "found_location", None),
        "event_at": (getattr(details, "last_seen_at", None) or getattr(details, "found_at", None)),
    }
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in summary.items()}


def export_case(
    db: Session, actor: User, destination_id: uuid.UUID, case_id: uuid.UUID, legal_basis: str
) -> OutboundDispatch:
    require_permission(actor, Permission.INTEGRATION_EXPORT)
    destination = db.get(IntegrationDestination, destination_id)
    if destination is None or destination.kind is not IntegrationKind.GOVERNMENT:
        raise NotFound("Government destination not found", code="destination_not_found")
    if not destination.is_enabled:
        raise Conflict("This destination is not enabled", code="destination_disabled")
    case = load_case(db, actor, case_id)
    if case.anonymized_at is not None:
        raise Conflict("Anonymised cases cannot be exported", code="case_anonymized")
    row = _enqueue(db, destination, DispatchOperation.CASE_EXPORT, _case_summary(db, case, destination.system),
                   f"export:{destination.id}:{case.id}:{case.version}", case_id=case.id, legal_basis=legal_basis,
                   actor_id=actor.id)
    record_audit(db, action=AuditAction.CASE_EXTERNAL_EXPORT_REQUESTED, entity_type="outbound_dispatch",
                 entity_id=row.id, actor_user_id=actor.id, case=case,
                 after={"destination": destination.code, "system": destination.system}, reason=legal_basis)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(select(OutboundDispatch).where(OutboundDispatch.idempotency_key == row.idempotency_key))
    db.refresh(row)
    return row


def list_dispatches(db: Session, actor: User, *, status: DispatchStatus | None, destination_id: uuid.UUID | None,
                    limit: int, offset: int):
    require_permission(actor, Permission.INTEGRATION_MANAGE)
    conditions = []
    if status:
        conditions.append(OutboundDispatch.status == status)
    if destination_id:
        conditions.append(OutboundDispatch.destination_id == destination_id)
    total = db.scalar(select(func.count()).select_from(OutboundDispatch).where(*conditions)) or 0
    rows = list(db.scalars(select(OutboundDispatch).where(*conditions)
                           .order_by(OutboundDispatch.created_at.desc()).limit(limit).offset(offset)))
    return rows, total


def retry_dispatch(db: Session, actor: User, dispatch_id: uuid.UUID) -> OutboundDispatch:
    require_permission(actor, Permission.INTEGRATION_MANAGE)
    row = db.get(OutboundDispatch, dispatch_id)
    if row is None:
        raise NotFound("Dispatch not found", code="dispatch_not_found")
    if row.status != D.FAILED:
        raise Conflict("Only failed dispatches can be retried", code="dispatch_not_failed")
    row.status, row.next_attempt_at, row.attempts = D.PENDING, func.now(), 0
    db.commit()
    db.refresh(row)
    return row


# --- Dispatcher ----------------------------------------------------------------------------------


def _target(destination: IntegrationDestination) -> Target:
    return Target(
        code=destination.code, system=destination.system, endpoint_url=destination.endpoint_url,
        secret=decrypt_text(destination.secret_ciphertext) if destination.secret_ciphertext else None,
        timeout_seconds=destination.timeout_seconds, config=destination.config or {},
    )


def release_stale(db: Session) -> int:
    count = db.execute(
        update(OutboundDispatch)
        .where(OutboundDispatch.status == D.SENDING, OutboundDispatch.sending_started_at < utcnow() - STALE_SENDING)
        .values(status=D.PENDING, next_attempt_at=func.now())
    ).rowcount
    db.commit()
    return count


def deliver_due(db: Session, *, limit: int = 50) -> dict[str, int]:
    outcomes = {"delivered": 0, "retry": 0, "failed": 0, "skipped": 0}
    now = utcnow()
    ids = db.scalars(
        select(OutboundDispatch.id)
        .join(IntegrationDestination, IntegrationDestination.id == OutboundDispatch.destination_id)
        .where(
            OutboundDispatch.status == D.PENDING,
            OutboundDispatch.next_attempt_at <= func.now(),
            IntegrationDestination.is_enabled.is_(True),
            (IntegrationDestination.circuit_open_until.is_(None)) | (IntegrationDestination.circuit_open_until <= now),
        )
        .order_by(OutboundDispatch.next_attempt_at).limit(limit)
    ).all()
    for dispatch_id in ids:
        outcomes[deliver_one(db, dispatch_id)] += 1
    return outcomes


def deliver_one(db: Session, dispatch_id: uuid.UUID) -> str:
    claimed = db.execute(
        update(OutboundDispatch)
        .where(OutboundDispatch.id == dispatch_id, OutboundDispatch.status == D.PENDING)
        .values(status=D.SENDING, attempts=OutboundDispatch.attempts + 1, sending_started_at=func.now())
        .returning(OutboundDispatch.id)
    ).first()
    db.commit()
    if claimed is None:
        return "skipped"
    row = db.get(OutboundDispatch, dispatch_id, populate_existing=True)
    destination = db.get(IntegrationDestination, row.destination_id, populate_existing=True)
    if row.alert_id is not None and row.operation is DispatchOperation.PUBLISH:
        alert = db.get(PublicAlert, row.alert_id, populate_existing=True)
        if alert is None or alert.status not in (A.APPROVED, A.PUBLISHED):
            row.status, row.next_attempt_at = D.CANCELLED, None
            db.commit()
            return "skipped"
    try:
        result = adapter_for(destination.adapter.value).deliver(
            _target(destination), operation=row.operation.value, payload=row.payload,
            idempotency_key=row.idempotency_key,
        )
    except AdapterError as exc:
        return _failed(db, row, destination, str(exc), permanent=exc.permanent)
    except Exception as exc:  # noqa: BLE001
        return _failed(db, row, destination, type(exc).__name__, permanent=False)

    now = utcnow()
    row.status, row.delivered_at, row.remote_reference = D.DELIVERED, now, result.remote_reference
    row.next_attempt_at, row.last_error = None, None
    destination.health, destination.consecutive_failures, destination.last_success_at = (
        IntegrationHealth.HEALTHY, 0, now,
    )
    destination.circuit_open_until = None
    if row.alert_id is not None and row.operation is DispatchOperation.PUBLISH:
        alert = db.get(PublicAlert, row.alert_id)
        if alert.status == A.APPROVED:
            alert.status, alert.published_at = A.PUBLISHED, now
            record_audit(db, action=AuditAction.ALERT_PUBLISHED, entity_type="public_alert", entity_id=alert.id,
                         actor_user_id=None, after={"first_destination": destination.code})
    record_audit(db, action=AuditAction.DISPATCH_DELIVERED, entity_type="outbound_dispatch", entity_id=row.id,
                 actor_user_id=None, after={"destination": destination.code, "operation": row.operation,
                                            "attempts": row.attempts})
    db.commit()
    DISPATCH_OUTCOMES.labels(destination.kind.value, "delivered").inc()
    return "delivered"


def _failed(db: Session, row: OutboundDispatch, destination: IntegrationDestination, error: str, *,
            permanent: bool) -> str:
    settings = get_settings()
    now = utcnow()
    row.last_error = error[:200]
    destination.consecutive_failures += 1
    destination.last_failure_at = now
    destination.health = IntegrationHealth.DEGRADED
    if destination.consecutive_failures >= settings.integration_circuit_failures:
        destination.circuit_open_until = now + timedelta(seconds=settings.integration_circuit_open_seconds)
    if permanent or row.attempts >= destination.max_attempts:
        row.status, row.next_attempt_at = D.FAILED, None
        record_audit(db, action=AuditAction.DISPATCH_FAILED, entity_type="outbound_dispatch", entity_id=row.id,
                     actor_user_id=None, after={"destination": destination.code, "error": row.last_error,
                                                "attempts": row.attempts})
        db.commit()
        DISPATCH_OUTCOMES.labels(destination.kind.value, "failed").inc()
        return "failed"
    row.status = D.PENDING
    row.next_attempt_at = now + timedelta(seconds=settings.integration_retry_base_seconds * 2 ** (row.attempts - 1))
    db.commit()
    DISPATCH_OUTCOMES.labels(destination.kind.value, "retry").inc()
    return "retry"


# --- Inbound -------------------------------------------------------------------------------------


def receive_inbound(
    db: Session, code: str, body: bytes, payload: dict, timestamp: str | None, signature: str | None
) -> tuple[InboundIntegrationMessage, bool]:
    """Verifies the destination's HMAC signature, then quarantines the payload for staff review."""
    destination = db.scalar(select(IntegrationDestination).where(IntegrationDestination.code == code))
    if destination is None or not destination.is_enabled or not destination.secret_ciphertext:
        raise Unauthenticated("Invalid signature", code="invalid_signature")
    if not verify_signature(decrypt_text(destination.secret_ciphertext), timestamp, signature, body):
        raise Unauthenticated("Invalid signature", code="invalid_signature")
    external_id = str(payload.get("external_id") or "").strip()[:200]
    message_type = str(payload.get("type") or "").strip()[:64]
    if not external_id or not message_type or not isinstance(payload.get("data"), dict):
        raise ValidationFailed("external_id, type, and data are required", code="invalid_inbound")
    existing = db.scalar(select(InboundIntegrationMessage).where(
        InboundIntegrationMessage.destination_id == destination.id,
        InboundIntegrationMessage.external_id == external_id,
    ))
    if existing is not None:
        return existing, True
    row = InboundIntegrationMessage(id=uuid.uuid4(), destination_id=destination.id, external_id=external_id,
                                    message_type=message_type, payload=payload["data"],
                                    status=InboundMessageStatus.QUARANTINED)
    db.add(row)
    record_audit(db, action=AuditAction.INBOUND_MESSAGE_RECEIVED, entity_type="inbound_integration_message",
                 entity_id=row.id, actor_user_id=None,
                 after={"destination": destination.code, "type": message_type})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(InboundIntegrationMessage).where(
            InboundIntegrationMessage.destination_id == destination.id,
            InboundIntegrationMessage.external_id == external_id,
        )), True
    db.refresh(row)
    return row, False


def list_inbound(db: Session, actor: User, status: InboundMessageStatus | None, *, limit: int, offset: int):
    require_permission(actor, Permission.INBOUND_REVIEW)
    conditions = [InboundIntegrationMessage.status == status] if status else []
    total = db.scalar(select(func.count()).select_from(InboundIntegrationMessage).where(*conditions)) or 0
    rows = list(db.scalars(select(InboundIntegrationMessage).where(*conditions)
                           .order_by(InboundIntegrationMessage.created_at.desc()).limit(limit).offset(offset)))
    return rows, total


def review_inbound(db: Session, actor: User, message_id: uuid.UUID, accept: bool, note: str | None):
    require_permission(actor, Permission.INBOUND_REVIEW)
    row = db.get(InboundIntegrationMessage, message_id)
    if row is None:
        raise NotFound("Message not found", code="inbound_message_not_found")
    if row.status != InboundMessageStatus.QUARANTINED:
        raise Conflict("This message was already reviewed", code="inbound_already_reviewed")
    row.status = InboundMessageStatus.ACCEPTED if accept else InboundMessageStatus.REJECTED
    row.reviewed_by_user_id, row.reviewed_at, row.review_note = actor.id, utcnow(), note
    record_audit(db, action=AuditAction.INBOUND_MESSAGE_REVIEWED, entity_type="inbound_integration_message",
                 entity_id=row.id, actor_user_id=actor.id, after={"status": row.status}, reason=note)
    db.commit()
    db.refresh(row)
    return row
