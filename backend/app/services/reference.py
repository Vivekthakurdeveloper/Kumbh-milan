"""Reference data (item taxonomy, localized enum labels) and explainable duplicate candidates (BE-105)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.auth.scope import case_visibility_clause
from app.common.errors import NotFound
from app.models import (
    Case,
    CaseContact,
    FoundItemDetails,
    FoundPersonDetails,
    ItemCategory,
    LostItemDetails,
    MissingPersonDetails,
    User,
)
from app.models.enums import (
    AgeGroup,
    CaseStatus,
    CaseType,
    CustodyStatus,
    Gender,
    GrievanceCategory,
    GrievanceStatus,
    UrgencyLevel,
)
from app.repositories import cases as case_repo
from app.services.access import load_case

DUPLICATE_TIME_WINDOW = timedelta(hours=6)
DUPLICATE_LIMIT = 10
INACTIVE = (CaseStatus.RESOLVED, CaseStatus.CLOSED)

ENUM_LABELS: dict[str, dict[str, tuple[str, str]]] = {
    "case_type": {
        CaseType.MISSING_PERSON: ("Missing person", "गुमशुदा व्यक्ति"),
        CaseType.FOUND_PERSON: ("Found person", "मिला व्यक्ति"),
        CaseType.LOST_ITEM: ("Lost item", "खोई वस्तु"),
        CaseType.FOUND_ITEM: ("Found item", "मिली वस्तु"),
    },
    "case_status": {
        CaseStatus.OPEN: ("Open", "खुला"),
        CaseStatus.UNDER_REVIEW: ("Under review", "समीक्षाधीन"),
        CaseStatus.ASSIGNED: ("Assigned", "सौंपा गया"),
        CaseStatus.MATCH_FOUND: ("Match found", "मिलान मिला"),
        CaseStatus.RESOLVED: ("Resolved", "सुलझा"),
        CaseStatus.CLOSED: ("Closed", "बंद"),
        CaseStatus.REOPENED: ("Reopened", "पुनः खोला गया"),
    },
    "urgency_level": {
        UrgencyLevel.LOW: ("Low", "कम"),
        UrgencyLevel.MEDIUM: ("Medium", "मध्यम"),
        UrgencyLevel.HIGH: ("High", "उच्च"),
        UrgencyLevel.CRITICAL: ("Critical", "अति गंभीर"),
    },
    "gender": {
        Gender.MALE: ("Male", "पुरुष"),
        Gender.FEMALE: ("Female", "महिला"),
        Gender.OTHER: ("Other", "अन्य"),
        Gender.UNKNOWN: ("Unknown", "अज्ञात"),
    },
    "age_group": {
        AgeGroup.INFANT: ("Infant", "शिशु"),
        AgeGroup.CHILD: ("Child", "बच्चा"),
        AgeGroup.ADULT: ("Adult", "वयस्क"),
        AgeGroup.ELDERLY: ("Elderly", "बुज़ुर्ग"),
        AgeGroup.UNKNOWN: ("Unknown", "अज्ञात"),
    },
    "custody_status": {
        CustodyStatus.IN_CUSTODY: ("In custody", "हिरासत में"),
        CustodyStatus.TRANSFERRED: ("Transferred", "स्थानांतरित"),
        CustodyStatus.RELEASED: ("Released", "सौंप दिया गया"),
    },
    "grievance_category": {
        GrievanceCategory.SERVICE: ("Service", "सेवा"),
        GrievanceCategory.STAFF_CONDUCT: ("Staff conduct", "कर्मचारी आचरण"),
        GrievanceCategory.DELAY: ("Delay", "देरी"),
        GrievanceCategory.OTHER: ("Other", "अन्य"),
    },
    "grievance_status": {
        GrievanceStatus.OPEN: ("Open", "खुली"),
        GrievanceStatus.ASSIGNED: ("Assigned", "सौंपी गई"),
        GrievanceStatus.IN_PROGRESS: ("In progress", "प्रगति पर"),
        GrievanceStatus.RESOLVED: ("Resolved", "हल हुई"),
        GrievanceStatus.CLOSED: ("Closed", "बंद"),
        GrievanceStatus.REOPENED: ("Reopened", "पुनः खोली गई"),
    },
}


def enum_labels() -> dict[str, list[dict[str, str]]]:
    return {
        name: [{"code": str(code), "label_en": en, "label_hi": hi} for code, (en, hi) in values.items()]
        for name, values in ENUM_LABELS.items()
    }


def list_item_categories(db: Session, *, include_inactive: bool = False) -> list[ItemCategory]:
    stmt = select(ItemCategory).order_by(ItemCategory.sort_order, ItemCategory.code)
    if not include_inactive:
        stmt = stmt.where(ItemCategory.is_active.is_(True))
    return list(db.scalars(stmt))


def upsert_item_category(
    db: Session, actor: User, code: str, *, label_en: str, label_hi: str, sort_order: int, is_active: bool
) -> ItemCategory:
    require_permission(actor, Permission.REFERENCE_MANAGE)
    row = db.get(ItemCategory, code)
    before = None
    if row is None:
        row = ItemCategory(code=code)
        db.add(row)
    else:
        before = {"label_en": row.label_en, "label_hi": row.label_hi, "is_active": row.is_active}
    row.label_en, row.label_hi, row.sort_order, row.is_active = label_en, label_hi, sort_order, is_active
    record_audit(
        db, action=AuditAction.REFERENCE_UPDATED, entity_type="item_category", actor_user_id=actor.id,
        before=before, after={"code": code, "label_en": label_en, "label_hi": label_hi, "is_active": is_active},
    )
    db.commit()
    db.refresh(row)
    return row


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.lower().split())
    return text or None


def _profile(db: Session, case: Case) -> dict:
    details = case.missing_person or case.found_person or case.lost_item or case.found_item
    contact = db.get(CaseContact, case.id)
    event_at = getattr(details, "last_seen_at", None) or getattr(details, "found_at", None)
    location = getattr(details, "last_seen_location", None) or getattr(details, "found_location", None)
    return {
        "mobile_hash": contact.mobile_lookup_hash if contact else None,
        "name": _norm(getattr(details, "person_name", None)),
        "item_category": _norm(getattr(details, "item_category", None)),
        "item_name": _norm(getattr(details, "item_name", None)),
        "event_at": event_at,
        "location": _norm(location),
        "location_id": case.location_id,
        "age_group": getattr(details, "age_group", None),
        "gender": getattr(details, "gender", None) or getattr(details, "estimated_gender", None),
    }


def find_duplicates(db: Session, actor: User, case_id: uuid.UUID) -> list[dict]:
    """Likely same-type duplicates with reasons. Never merges; a person decides what to do."""
    require_permission(actor, Permission.CASE_READ)
    case = load_case(db, actor, case_id)
    me = _profile(db, case)
    details_model = {
        CaseType.MISSING_PERSON: MissingPersonDetails,
        CaseType.FOUND_PERSON: FoundPersonDetails,
        CaseType.LOST_ITEM: LostItemDetails,
        CaseType.FOUND_ITEM: FoundItemDetails,
    }[CaseType(case.case_type)]

    narrowing = []
    if me["mobile_hash"]:
        narrowing.append(Case.id.in_(select(CaseContact.case_id).where(CaseContact.mobile_lookup_hash == me["mobile_hash"])))
    if me["name"] and hasattr(details_model, "person_name"):
        narrowing.append(func.lower(func.btrim(details_model.person_name)) == me["name"])
    if me["item_name"] and hasattr(details_model, "item_name"):
        narrowing.append(func.lower(func.btrim(details_model.item_name)) == me["item_name"])
    if me["event_at"] is not None:
        event_col = getattr(details_model, "last_seen_at", None) or getattr(details_model, "found_at")
        narrowing.append(event_col.between(me["event_at"] - DUPLICATE_TIME_WINDOW, me["event_at"] + DUPLICATE_TIME_WINDOW))
    if case.location_id is not None:
        narrowing.append(Case.location_id == case.location_id)
    if not narrowing:
        return []

    candidates = list(
        db.scalars(
            case_repo._eager_details(select(Case))
            .join(details_model, details_model.case_id == Case.id)
            .where(
                case_visibility_clause(actor),
                Case.case_type == case.case_type,
                Case.id != case.id,
                Case.archived_at.is_(None),
                Case.status.not_in(INACTIVE),
                or_(*narrowing),
            )
            .order_by(Case.created_at.desc())
            .limit(200)
        ).unique()
    )
    results: list[dict] = []
    for other in candidates:
        them = _profile(db, other)
        reasons: list[str] = []
        if me["mobile_hash"] and me["mobile_hash"] == them["mobile_hash"]:
            reasons.append("SAME_REPORTER_MOBILE")
        if me["name"] and me["name"] == them["name"]:
            reasons.append("SAME_PERSON_NAME")
        if me["item_name"] and me["item_name"] == them["item_name"] and me["item_category"] == them["item_category"]:
            reasons.append("SAME_ITEM_NAME_AND_CATEGORY")
        if me["event_at"] and them["event_at"] and abs(me["event_at"] - them["event_at"]) <= DUPLICATE_TIME_WINDOW:
            reasons.append("EVENT_TIME_WITHIN_6_HOURS")
        if (me["location_id"] and me["location_id"] == them["location_id"]) or (
            me["location"] and me["location"] == them["location"]
        ):
            reasons.append("SAME_LOCATION")
        if me["age_group"] and me["gender"] and (me["age_group"], me["gender"]) == (them["age_group"], them["gender"]):
            reasons.append("SAME_AGE_GROUP_AND_GENDER")
        strong = {"SAME_REPORTER_MOBILE", "SAME_PERSON_NAME", "SAME_ITEM_NAME_AND_CATEGORY"} & set(reasons)
        if not strong and len(reasons) < 3:
            continue
        results.append(
            {
                "case_id": other.id,
                "case_number": other.case_number,
                "status": other.status,
                "station_id": other.station_id,
                "created_at": other.created_at,
                "reasons": reasons,
                "score": len(reasons) + 2 * len(strong),
            }
        )
    results.sort(key=lambda r: -r["score"])
    return results[:DUPLICATE_LIMIT]


def get_item_category(db: Session, code: str) -> ItemCategory:
    row = db.get(ItemCategory, code)
    if row is None:
        raise NotFound("Item category not found", code="item_category_not_found")
    return row
