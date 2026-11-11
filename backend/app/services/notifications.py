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
from app.common.errors import Conflict, ValidationFailed
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseContact, Notification, PushDeviceToken, Station, User
from app.models.enums import CaseStatus, NotificationChannel, NotificationStatus, NotificationTemplate
from app.notifications import (
    LOCALES,
    NotificationProvider,
    ProviderError,
    mask,
    normalise_phone,
    render,
    render_subject,
)
from app.observability.metrics import NOTIFICATION_OUTCOMES
from app.schemas.notifications import NotificationCreate
from app.services.access import load_case

logger = logging.getLogger("kumbh_setu.notifications")
S = NotificationStatus

AUTOMATED_CONSENT = "Automated notification to verified consenting recipient for case lifecycle update"


def queue(db: Session, actor: User, case_id: uuid.UUID, req: NotificationCreate, idempotency_key: str | None
          ) -> tuple[Notification, bool]:
    require_permission(actor, Permission.NOTIFICATION_SEND)
    case = load_case(db, actor, case_id)
    if case.anonymized_at is not None or case.status == CaseStatus.CLOSED:
        raise Conflict("Notifications cannot be sent for closed or anonymised cases", code="case_closed")
    if req.locale not in LOCALES:
        raise ValidationFailed(f"locale must be one of {', '.join(LOCALES)}", code="unsupported_locale")
    phone = normalise_phone(req.recipient_phone, get_settings().notification_default_country_code)
    if phone is None:
        raise ValidationFailed("recipient_phone is not a valid phone number", code="invalid_phone")

    raw_key = idempotency_key or uuid.uuid4().hex
    key = hashlib.sha256(f"{case.id}:{req.template}:{raw_key}".encode()).hexdigest()
    existing = db.scalar(select(Notification).where(Notification.idempotency_key == key))
    if existing is not None:
        return existing, True

    notification = Notification(
        id=uuid.uuid4(), case_id=case.id, template=req.template, channel=req.channel, locale=req.locale,
        recipient_ciphertext=encrypt_text(phone), recipient_hint=mask(phone), status=S.PENDING,
        next_attempt_at=func.now(), idempotency_key=key, consent_statement=req.consent_statement,
        requested_by_user_id=actor.id, subject=render_subject(req.template, req.locale, case_number=case.case_number),
    )
    db.add(notification)
    record_audit(
        db, action=AuditAction.NOTIFICATION_QUEUED, entity_type="notification", entity_id=notification.id,
        actor_user_id=actor.id, case=case,
        after={"template": req.template, "channel": req.channel, "locale": req.locale, "recipient": mask(phone),
               "consent_statement": req.consent_statement},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Notification).where(Notification.idempotency_key == key))
        if existing is None:
            raise
        return existing, True
    db.refresh(notification)
    return notification, False


def enqueue_event(
    db: Session,
    case: Case,
    template: NotificationTemplate,
    *,
    channels: list[NotificationChannel] | None = None,
    locale: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    idempotency_suffix: str | None = None,
) -> list[Notification]:
    """
    Queue lifecycle notifications in the caller's open transaction (no commit).
    Skips when no verified consenting recipient exists.
    """
    contact = db.get(CaseContact, case.id)
    if contact is None or not contact.notification_consent_statement or not contact.mobile_lookup_hash:
        return []
    if case.anonymized_at is not None:
        return []
    preferred = locale or contact.preferred_locale or "hi"
    if preferred not in LOCALES:
        preferred = "hi"
    channels = channels or [NotificationChannel.SMS]
    created: list[Notification] = []
    for channel in channels:
        suffix = idempotency_suffix or template.value
        key = hashlib.sha256(f"{case.id}:{template}:{channel}:{suffix}".encode()).hexdigest()
        existing = db.scalar(select(Notification).where(Notification.idempotency_key == key))
        if existing is not None:
            created.append(existing)
            continue
        if channel is NotificationChannel.EMAIL and not contact.email_ciphertext:
            continue
        if channel is NotificationChannel.PUSH:
            tokens = list(
                db.scalars(
                    select(PushDeviceToken).where(
                        PushDeviceToken.case_id == case.id, PushDeviceToken.is_active.is_(True)
                    )
                )
            )
            for token in tokens:
                push_key = hashlib.sha256(f"{key}:{token.id}".encode()).hexdigest()
                if db.scalar(select(Notification.id).where(Notification.idempotency_key == push_key)):
                    continue
                row = Notification(
                    id=uuid.uuid4(),
                    case_id=case.id,
                    template=template,
                    channel=channel,
                    locale=preferred,
                    recipient_ciphertext=token.token_ciphertext,
                    recipient_hint="device",
                    status=S.PENDING,
                    next_attempt_at=func.now(),
                    idempotency_key=push_key,
                    consent_statement=contact.notification_consent_statement or AUTOMATED_CONSENT,
                    requested_by_user_id=actor_user_id,
                    subject=render_subject(template, preferred, case_number=case.case_number),
                    device_token_id=token.id,
                )
                db.add(row)
                created.append(row)
            continue

        if channel in (NotificationChannel.SMS, NotificationChannel.WHATSAPP):
            if not contact.mobile_lookup_hash:
                continue
            recipient = decrypt_text(contact.contact_ciphertext)
            if normalise_phone(recipient, get_settings().notification_default_country_code) is None:
                continue
            hint = contact.mobile_hint or mask(recipient)
            ciphertext = contact.contact_ciphertext
        else:
            if not contact.email_ciphertext:
                continue
            hint = contact.email_hint
            ciphertext = contact.email_ciphertext

        row = Notification(
            id=uuid.uuid4(),
            case_id=case.id,
            template=template,
            channel=channel,
            locale=preferred,
            recipient_ciphertext=ciphertext,
            recipient_hint=(hint or "••••")[:64],
            status=S.PENDING,
            next_attempt_at=func.now(),
            idempotency_key=key,
            consent_statement=contact.notification_consent_statement or AUTOMATED_CONSENT,
            requested_by_user_id=actor_user_id,
            subject=render_subject(template, preferred, case_number=case.case_number),
        )
        db.add(row)
        record_audit(
            db,
            action=AuditAction.NOTIFICATION_QUEUED,
            entity_type="notification",
            entity_id=row.id,
            actor_user_id=actor_user_id,
            case=case,
            after={"template": template, "channel": channel, "locale": preferred, "automated": True},
        )
        created.append(row)
    return created


def list_for_case(db: Session, actor: User, case_id: uuid.UUID) -> list[Notification]:
    require_permission(actor, Permission.CASE_READ)
    load_case(db, actor, case_id)
    return list(db.scalars(select(Notification).where(Notification.case_id == case_id).order_by(Notification.created_at)))


def cancel_for_case(db: Session, case_id: uuid.UUID) -> int:
    cancelled = db.execute(
        update(Notification)
        .where(Notification.case_id == case_id, Notification.status.in_((S.PENDING, S.SENDING)))
        .values(status=S.CANCELLED, next_attempt_at=None)
    ).rowcount
    db.execute(update(Notification).where(Notification.case_id == case_id)
               .values(recipient_ciphertext=None, recipient_hint=None))
    return cancelled


def release_stale(db: Session) -> int:
    cutoff = utcnow() - timedelta(seconds=get_settings().notification_stale_after_seconds)
    count = db.execute(
        update(Notification)
        .where(Notification.status == S.SENDING, Notification.sending_started_at < cutoff)
        .values(status=S.PENDING, next_attempt_at=func.now())
    ).rowcount
    db.commit()
    return count


def deliver_due(db: Session, provider: NotificationProvider, *, limit: int = 50) -> dict[str, int]:
    outcomes = {"sent": 0, "retry": 0, "failed": 0}
    if not provider.enabled:
        return outcomes
    for notification_id in db.scalars(
        select(Notification.id)
        .where(Notification.status == S.PENDING, Notification.next_attempt_at <= func.now())
        .order_by(Notification.next_attempt_at).limit(limit)
    ).all():
        outcome = deliver_one(db, provider, notification_id)
        if outcome:
            outcomes[outcome] += 1
    return outcomes


def deliver_one(db: Session, provider: NotificationProvider, notification_id: uuid.UUID) -> str | None:
    claimed = db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.status == S.PENDING)
        .values(status=S.SENDING, attempts=Notification.attempts + 1, sending_started_at=func.now(),
                provider=provider.name)
        .returning(Notification.id)
    ).first()
    db.commit()
    if claimed is None:
        return None
    n = db.get(Notification, notification_id, populate_existing=True)
    case = db.get(Case, n.case_id)
    station = db.get(Station, case.station_id)
    body = render(n.template, n.locale, case_number=case.case_number, station=station.name)
    subject = n.subject or render_subject(n.template, n.locale, case_number=case.case_number)
    try:
        delivery = provider.send(
            channel=n.channel,
            to=decrypt_text(n.recipient_ciphertext),
            body=body,
            idempotency_key=str(n.id),
            subject=subject,
            template=n.template,
            locale=n.locale,
        )
    except ProviderError as exc:
        if n.channel is NotificationChannel.PUSH and exc.permanent and n.device_token_id:
            token = db.get(PushDeviceToken, n.device_token_id)
            if token is not None:
                token.is_active = False
                token.deactivated_at = utcnow()
                token.deactivation_reason = "provider_invalid"
        return _failed_attempt(db, n, case, str(exc), permanent=exc.permanent)
    except Exception as exc:  # noqa: BLE001
        return _failed_attempt(db, n, case, type(exc).__name__, permanent=False)

    n.status, n.sent_at, n.provider_message_id = S.SENT, utcnow(), delivery.provider_message_id
    n.next_attempt_at, n.last_error = None, None
    record_audit(db, action=AuditAction.NOTIFICATION_SENT, entity_type="notification", entity_id=n.id,
                 actor_user_id=None, case=case, after={"provider": provider.name, "attempts": n.attempts})
    db.commit()
    NOTIFICATION_OUTCOMES.labels(n.channel, "sent").inc()
    return "sent"


def _failed_attempt(db: Session, n: Notification, case: Case, error: str, *, permanent: bool) -> str:
    settings = get_settings()
    n.last_error = error[:200]
    if permanent or n.attempts >= settings.notification_max_attempts:
        n.status, n.next_attempt_at = S.FAILED, None
        record_audit(db, action=AuditAction.NOTIFICATION_FAILED, entity_type="notification", entity_id=n.id,
                     actor_user_id=None, case=case, after={"error": n.last_error, "attempts": n.attempts})
        db.commit()
        NOTIFICATION_OUTCOMES.labels(n.channel, "failed").inc()
        return "failed"
    n.status = S.PENDING
    n.next_attempt_at = func.now() + timedelta(seconds=settings.notification_retry_base_seconds * 2 ** (n.attempts - 1))
    db.commit()
    NOTIFICATION_OUTCOMES.labels(n.channel, "retry").inc()
    return "retry"
