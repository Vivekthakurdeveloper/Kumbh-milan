"""Public missing-person submission, staff review, and citizen status reads."""

from __future__ import annotations

import json
import uuid
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.auth.public_tokens import decode_public_token
from app.common.errors import Conflict, NotFound, ServiceUnavailable, Unauthenticated, ValidationFailed
from app.common.security_hash import keyed_hash
from app.common.time import utcnow
from app.config import get_settings
from app.models import Case, CaseContact, Station, User
from app.models.enums import (
    CaseSource,
    CaseStatus,
    CaseType,
    MediaType,
    NotificationChannel,
    NotificationTemplate,
    OtpPurpose,
    PublicReviewStatus,
    Role,
)
from app.notifications import normalise_phone
from app.schemas.cases import MissingPersonCreate
from app.schemas.public import PublicMissingPersonCreate, PublicReviewRequest
from app.schemas.workflow import StatusChangeRequest
from app.services import attachments as attachment_service
from app.services import cases as case_service
from app.services import notifications as notification_service
from app.services import workflow as workflow_service
from app.services.access import load_case
from app.services.case_types import CASE_TYPE_SPECS, spec_for
from app.storage import ObjectStorage
from app.storage.scanner import MalwareScanner

PUBLIC_STATUS_MESSAGES = {
    "en": {
        PublicReviewStatus.PENDING_VERIFICATION: "Your report is pending staff verification.",
        PublicReviewStatus.VERIFIED: "Your report has been accepted by staff.",
        PublicReviewStatus.REJECTED: "Your report could not be verified. Please visit a help desk.",
        CaseStatus.OPEN: "Your report is open and being handled by staff.",
        CaseStatus.UNDER_REVIEW: "Your report is under review.",
        CaseStatus.ASSIGNED: "An officer has been assigned to your report.",
        CaseStatus.MATCH_FOUND: "Staff are reviewing a potential update. Please visit the help desk.",
        CaseStatus.RESOLVED: "Your report has been marked reunited/resolved.",
        CaseStatus.CLOSED: "Your report is closed.",
        CaseStatus.REOPENED: "Your report has been reopened for further assistance.",
    },
    "hi": {
        PublicReviewStatus.PENDING_VERIFICATION: "आपकी रिपोर्ट कर्मचारी सत्यापन की प्रतीक्षा में है।",
        PublicReviewStatus.VERIFIED: "आपकी रिपोर्ट कर्मचारियों द्वारा स्वीकार कर ली गई है।",
        PublicReviewStatus.REJECTED: "आपकी रिपोर्ट सत्यापित नहीं हो सकी। कृपया हेल्प डेस्क पर आएं।",
        CaseStatus.OPEN: "आपकी रिपोर्ट खुली है और कर्मचारी इसे देख रहे हैं।",
        CaseStatus.UNDER_REVIEW: "आपकी रिपोर्ट समीक्षाधीन है।",
        CaseStatus.ASSIGNED: "आपकी रिपोर्ट को एक अधिकारी सौंपा गया है।",
        CaseStatus.MATCH_FOUND: "कर्मचारी संभावित अपडेट की समीक्षा कर रहे हैं। कृपया हेल्प डेस्क आएं।",
        CaseStatus.RESOLVED: "आपकी रिपोर्ट को पुनर्मिलन/निराकरण के रूप में चिह्नित किया गया है।",
        CaseStatus.CLOSED: "आपकी रिपोर्ट बंद है।",
        CaseStatus.REOPENED: "आगे सहायता के लिए आपकी रिपोर्ट फिर से खोली गई है।",
    },
}


def _intake_actor(db: Session) -> User:
    settings = get_settings()
    if settings.public_intake_user_id is None or settings.public_intake_station_id is None:
        raise ServiceUnavailable(
            "Public intake is not configured",
            code="public_intake_unconfigured",
        )
    user = db.get(User, settings.public_intake_user_id)
    station = db.get(Station, settings.public_intake_station_id)
    if (
        user is None
        or not user.is_active
        or user.role != Role.HELP_DESK_OPERATOR
        or station is None
        or not station.is_active
        or user.station_id != station.id
    ):
        raise ServiceUnavailable(
            "Public intake actor or station is invalid",
            code="public_intake_unconfigured",
        )
    return user


def _require_verified_mobile(token: str, *, purpose: OtpPurpose, expected_mobile: str) -> str:
    claims = decode_public_token(token, expected_typ="verified_mobile")
    if claims.get("purpose") != purpose.value:
        raise Unauthenticated("Verified mobile token purpose mismatch", code="invalid_token")
    phone = normalise_phone(expected_mobile, get_settings().notification_default_country_code)
    if phone is None or claims.get("mobile") != phone:
        raise Unauthenticated("Verified mobile token does not match reporter number", code="invalid_token")
    return phone


def submit_missing_person(
    db: Session,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    *,
    payload: PublicMissingPersonCreate,
    verified_mobile_token: str,
    idempotency_key: str | None,
    photo: BinaryIO | None = None,
    photo_filename: str | None = None,
    photo_content_type: str | None = None,
) -> tuple[dict, bool]:
    if not idempotency_key:
        raise ValidationFailed("Idempotency-Key header is required", code="idempotency_key_required")
    phone = _require_verified_mobile(
        verified_mobile_token, purpose=OtpPurpose.REPORT_MISSING, expected_mobile=payload.reporter_mobile_number
    )
    actor = _intake_actor(db)
    settings = get_settings()
    email = str(payload.reporter_email) if payload.reporter_email else None
    consent_at = utcnow() if payload.notification_consent else None
    consent_statement = payload.notification_consent_statement if payload.notification_consent else None

    create_payload = MissingPersonCreate(
        person_name=payload.person_name,
        approximate_age=payload.approximate_age,
        age_group=payload.age_group,
        gender=payload.gender,
        clothing_description=payload.clothing_description,
        last_seen_location=payload.last_seen_location,
        last_seen_at=payload.last_seen_at,
        additional_description=payload.additional_description,
        station_id=settings.public_intake_station_id,
    )
    public_contact = {
        "mobile": phone,
        "email": email,
        "reporter_name": payload.reporter_name,
        "preferred_locale": payload.preferred_locale,
        "notification_consent_statement": consent_statement,
        "notification_consent_at": consent_at,
        "mobile_verified_at": utcnow(),
        "email_verified_at": None,
    }
    # Namespace idempotency by verified mobile so citizens cannot collide on the intake user key.
    namespaced_key = f"pub:{keyed_hash(phone)[:24]}:{idempotency_key}"

    case, replayed = case_service.create_case(
        db,
        actor,
        CASE_TYPE_SPECS[CaseType.MISSING_PERSON],
        create_payload,
        idempotency_key=namespaced_key,
        source=CaseSource.PUBLIC,
        public_review_status=PublicReviewStatus.PENDING_VERIFICATION,
        public_contact=public_contact,
        skip_permission_check=True,
    )
    if not replayed:
        record_audit(
            db,
            action=AuditAction.PUBLIC_CASE_SUBMITTED,
            entity_type="case",
            entity_id=case.id,
            actor_user_id=actor.id,
            case=case,
            after={"public_review_status": PublicReviewStatus.PENDING_VERIFICATION},
        )
        channels = [NotificationChannel.SMS]
        if email and consent_statement:
            channels.append(NotificationChannel.EMAIL)
        notification_service.enqueue_event(
            db,
            case,
            NotificationTemplate.CASE_SUBMITTED,
            channels=channels,
            locale=payload.preferred_locale,
            actor_user_id=actor.id,
            idempotency_suffix="public_submit",
        )
        db.commit()
        if photo is not None:
            attachment_service.upload_attachment(
                db,
                storage,
                actor,
                case.id,
                media_type=MediaType.PHOTO,
                stream=photo,
                filename=photo_filename,
                declared_content_type=photo_content_type,
                scanner=scanner,
                idempotency_key=f"{namespaced_key}:photo",
            )
    return {
        "case_id": case.case_number,
        "status": case.public_review_status,
        "message": "Report submitted successfully",
    }, replayed


def review_public_case(db: Session, actor: User, case_id: uuid.UUID, req: PublicReviewRequest) -> Case:
    require_permission(actor, Permission.CASE_REVIEW)
    case = load_case(db, actor, case_id, for_update=True)
    if case.source != CaseSource.PUBLIC:
        raise Conflict("Only public submissions can be reviewed here", code="not_public_case")
    if case.public_review_status != PublicReviewStatus.PENDING_VERIFICATION:
        raise Conflict("Public submission is not awaiting verification", code="public_review_state")
    case.public_review_status = PublicReviewStatus(req.decision)
    case.updated_at = utcnow()
    record_audit(
        db,
        action=AuditAction.PUBLIC_CASE_REVIEWED,
        entity_type="case",
        entity_id=case.id,
        actor_user_id=actor.id,
        case=case,
        after={"public_review_status": req.decision},
        reason=req.reason,
    )
    if req.decision == PublicReviewStatus.VERIFIED.value:
        notification_service.enqueue_event(
            db,
            case,
            NotificationTemplate.CASE_APPROVED,
            channels=[NotificationChannel.SMS, NotificationChannel.EMAIL],
            actor_user_id=actor.id,
            idempotency_suffix=f"approved:{case.version}",
        )
        db.commit()
        if case.status == CaseStatus.OPEN:
            workflow_service.change_status(
                db,
                actor,
                spec_for(case.case_type),
                case.id,
                StatusChangeRequest(status=CaseStatus.UNDER_REVIEW, reason=req.reason or "Public report verified"),
            )
    else:
        db.commit()
    return load_case(db, actor, case_id)


def public_status_for_token(db: Session, *, case_number: str, authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthenticated("Case status authorization required")
    token = authorization.split(" ", 1)[1].strip()
    claims = decode_public_token(token, expected_typ="case_status")
    if claims.get("scope") != "status:read" or claims.get("case_number") != case_number:
        raise Unauthenticated("Invalid case status token", code="invalid_token")
    case = db.scalar(select(Case).where(Case.case_number == case_number))
    if case is None or str(case.id) != claims.get("case_id"):
        raise NotFound("Case not found", code="case_not_found")
    contact = db.get(CaseContact, case.id)
    mobile = claims.get("mobile")
    if contact is None or contact.mobile_lookup_hash != keyed_hash(mobile or ""):
        raise NotFound("Case not found", code="case_not_found")
    locale = contact.preferred_locale if contact.preferred_locale in ("en", "hi") else "hi"
    if case.public_review_status == PublicReviewStatus.PENDING_VERIFICATION:
        public_status = PublicReviewStatus.PENDING_VERIFICATION.value
        message = PUBLIC_STATUS_MESSAGES[locale][PublicReviewStatus.PENDING_VERIFICATION]
    elif case.public_review_status == PublicReviewStatus.REJECTED:
        public_status = PublicReviewStatus.REJECTED.value
        message = PUBLIC_STATUS_MESSAGES[locale][PublicReviewStatus.REJECTED]
    else:
        public_status = case.status
        message = PUBLIC_STATUS_MESSAGES[locale].get(CaseStatus(case.status), "Status updated.")
    return {
        "case_id": case.case_number,
        "status": public_status,
        "updated_at": case.updated_at,
        "message": message,
    }


def parse_public_report(raw: str) -> PublicMissingPersonCreate:
    from pydantic import ValidationError

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("report must be valid JSON", code="invalid_report_json") from exc
    try:
        return PublicMissingPersonCreate.model_validate(data)
    except ValidationError as exc:
        raise ValidationFailed("Invalid public report", code="validation_failed", details=exc.errors()) from exc
