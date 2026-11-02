"""
Automatic match suggestions. Suggestions are PROPOSED, AUTOMATIC matches that a police officer must review;
nothing here changes a case's status, confirms identity, or reveals contact details.

Pairs that already exist in any status (including REJECTED and REVOKED) are never suggested again.
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.audit import AuditAction, record_audit
from app.auth.permissions import Permission, require_permission
from app.common.errors import Conflict
from app.config import get_settings
from app.matching import DEFAULT_RULES, ENGINE_VERSION, ItemProfile, MatchingRules, PersonProfile, Place
from app.matching.engine import Assessment, score_items, score_persons
from app.models import Case, CaseMatch, Location, User
from app.models.enums import CaseType, ConfidenceBand, Gender, MatchDomain, MatchSource, MatchStatus
from app.observability.metrics import MATCH_SUGGESTIONS, MATCHING_FAILURES
from app.services.access import load_case
from app.services.workflow import MATCH_CONFIRMATION_SOURCES

logger = logging.getLogger("kumbh_setu.matching")

COUNTERPART = {
    CaseType.MISSING_PERSON: CaseType.FOUND_PERSON,
    CaseType.FOUND_PERSON: CaseType.MISSING_PERSON,
    CaseType.LOST_ITEM: CaseType.FOUND_ITEM,
    CaseType.FOUND_ITEM: CaseType.LOST_ITEM,
}
LEFT_TYPES = (CaseType.MISSING_PERSON, CaseType.LOST_ITEM)
_DETAILS = (
    joinedload(Case.missing_person), joinedload(Case.found_person),
    joinedload(Case.lost_item), joinedload(Case.found_item),
)


def _places(db: Session, cases: list[Case]) -> dict[uuid.UUID, Location]:
    ids = {c.location_id for c in cases if c.location_id}
    return {loc.id: loc for loc in db.scalars(select(Location).where(Location.id.in_(ids)))} if ids else {}


def _place(case: Case, text: str | None, locations: dict[uuid.UUID, Location]) -> Place:
    location = locations.get(case.location_id) if case.location_id else None
    sector = (location.id if location and location.kind == "SECTOR" else location.sector_id) if location else None
    if case.latitude is not None:
        return Place(case.latitude, case.longitude, case.location_id, sector, text)
    if location is not None:
        return Place(location.latitude, location.longitude, location.id, sector, text)
    return Place(text=text)


def profile(case: Case, locations: dict[uuid.UUID, Location]) -> PersonProfile | ItemProfile:
    t = CaseType(case.case_type)
    if t is CaseType.MISSING_PERSON:
        d = case.missing_person
        return PersonProfile(
            case.id, d.gender, d.approximate_age, d.age_group, d.language, d.clothing_description,
            d.identifying_marks, _place(case, d.last_seen_location, locations), d.last_seen_at, d.has_disability,
        )
    if t is CaseType.FOUND_PERSON:
        d = case.found_person
        return PersonProfile(
            case.id, d.estimated_gender, d.approximate_age, d.age_group, d.language, d.clothing_description,
            d.identifying_marks, _place(case, d.found_location, locations), d.found_at, d.has_disability,
        )
    if t is CaseType.LOST_ITEM:
        d = case.lost_item
        description = " ".join(filter(None, (d.item_description, d.brand_or_color, d.identifying_features)))
        return ItemProfile(case.id, d.item_category, d.item_name, description or None,
                           _place(case, d.last_seen_location, locations), d.last_seen_at)
    d = case.found_item
    description = " ".join(filter(None, (d.item_description, d.item_condition)))
    return ItemProfile(case.id, d.item_category, d.item_name, description or None,
                       _place(case, d.found_location, locations), d.found_at)


def assess(left: PersonProfile | ItemProfile, right: PersonProfile | ItemProfile, rules: MatchingRules) -> Assessment:
    if isinstance(left, PersonProfile):
        return score_persons(left, right, rules)
    return score_items(left, right, rules)


def _candidates(db: Session, source: Case, rules: MatchingRules) -> list[Case]:
    from app.models import FoundItemDetails, FoundPersonDetails, LostItemDetails, MissingPersonDetails

    target = COUNTERPART[CaseType(source.case_type)]
    window = timedelta(days=rules.candidate_window_days)
    already_paired = exists().where(
        or_(
            and_(CaseMatch.left_case_id == source.id, CaseMatch.right_case_id == Case.id),
            and_(CaseMatch.right_case_id == source.id, CaseMatch.left_case_id == Case.id),
        )
    )
    stmt = select(Case).where(
        Case.case_type == target,
        Case.status.in_(MATCH_CONFIRMATION_SOURCES),
        Case.created_at.between(source.created_at - window, source.created_at + window),
        ~already_paired,
    )
    # Cheap pre-filters for hard contradictions; the engine re-checks everything.
    if target in (CaseType.MISSING_PERSON, CaseType.FOUND_PERSON):
        detail = source.missing_person or source.found_person
        gender = getattr(detail, "gender", None) or getattr(detail, "estimated_gender", None)
        if gender in (Gender.MALE, Gender.FEMALE):
            model, column = (
                (MissingPersonDetails, MissingPersonDetails.gender) if target is CaseType.MISSING_PERSON
                else (FoundPersonDetails, FoundPersonDetails.estimated_gender)
            )
            stmt = stmt.join(model, model.case_id == Case.id).where(
                or_(column.is_(None), column.in_((gender, Gender.OTHER, Gender.UNKNOWN)))
            )
    else:
        detail = source.lost_item or source.found_item
        model = FoundItemDetails if target is CaseType.FOUND_ITEM else LostItemDetails
        stmt = stmt.join(model, model.case_id == Case.id).where(
            func.lower(model.item_category) == detail.item_category.lower()
        )
    stmt = stmt.options(*_DETAILS).order_by(Case.created_at.desc(), Case.id).limit(rules.candidate_limit)
    return list(db.scalars(stmt).unique())


def generate_for_case(
    db: Session, case_id: uuid.UUID, *, actor: User | None = None, rules: MatchingRules = DEFAULT_RULES
) -> list[uuid.UUID]:
    """Scores one case against its candidates and stores new suggestions. Returns ids of created matches."""
    source = db.scalars(select(Case).where(Case.id == case_id).options(*_DETAILS)).unique().first()
    if source is None or source.status not in MATCH_CONFIRMATION_SOURCES:
        return []
    candidates = _candidates(db, source, rules)
    _refresh_existing(db, source, rules)
    if not candidates:
        db.commit()
        return []
    locations = _places(db, [source, *candidates])
    source_profile = profile(source, locations)
    source_is_left = source.case_type in LEFT_TYPES

    scored = []
    for candidate in candidates:
        candidate_profile = profile(candidate, locations)
        left, right = (source_profile, candidate_profile) if source_is_left else (candidate_profile, source_profile)
        result = assess(left, right, rules)
        if result.suggest:
            scored.append((result, candidate))
    scored.sort(key=lambda pair: (-pair[0].score, str(pair[1].id)))

    created = []
    for result, candidate in scored[: rules.max_suggestions_per_case]:
        left, right = (source, candidate) if source_is_left else (candidate, source)
        domain = MatchDomain.PERSON if left.case_type == CaseType.MISSING_PERSON else MatchDomain.ITEM
        match_id = db.execute(
            insert(CaseMatch)
            .values(
                id=uuid.uuid4(), match_domain=domain, left_case_id=left.id, left_case_type=left.case_type,
                right_case_id=right.id, right_case_type=right.case_type, status=MatchStatus.PROPOSED,
                source=MatchSource.AUTOMATIC, score=Decimal(str(result.score)), confidence_band=result.band,
                reasons=result.reasons, engine_version=ENGINE_VERSION,
            )
            .on_conflict_do_nothing(index_elements=["left_case_id", "right_case_id"])
            .returning(CaseMatch.id)
        ).scalar()
        if match_id is None:
            continue
        for side, counterpart in ((left, right), (right, left)):
            record_audit(
                db, action=AuditAction.MATCH_SUGGESTED, entity_type="case_match", entity_id=match_id,
                actor_user_id=actor.id if actor else None, case=side,
                after={"counterpart_case_number": counterpart.case_number, "score": result.score,
                       "confidence_band": result.band, "engine_version": ENGINE_VERSION},
            )
        MATCH_SUGGESTIONS.labels(domain.value, result.band.value).inc()
        created.append(match_id)
    db.commit()
    return created


def _refresh_existing(db: Session, source: Case, rules: MatchingRules) -> None:
    """Re-scores this case's open automatic suggestions so edits are reflected; reviewers still decide."""
    rows = list(db.scalars(
        select(CaseMatch).where(
            or_(CaseMatch.left_case_id == source.id, CaseMatch.right_case_id == source.id),
            CaseMatch.status == MatchStatus.PROPOSED, CaseMatch.source == MatchSource.AUTOMATIC,
        ).with_for_update(skip_locked=True)
    ))
    if not rows:
        return
    others = {m.right_case_id if m.left_case_id == source.id else m.left_case_id for m in rows}
    cases = {c.id: c for c in db.scalars(select(Case).where(Case.id.in_(others)).options(*_DETAILS)).unique()}
    locations = _places(db, [source, *cases.values()])
    for match in rows:
        left = source if match.left_case_id == source.id else cases[match.left_case_id]
        right = source if match.right_case_id == source.id else cases[match.right_case_id]
        result = assess(profile(left, locations), profile(right, locations), rules)
        match.score = Decimal(str(result.score))
        match.confidence_band = result.band or ConfidenceBand.LOW
        match.reasons = result.reasons
        match.engine_version = ENGINE_VERSION


def generate_on_request(db: Session, actor: User, case_id: uuid.UUID) -> list[uuid.UUID]:
    require_permission(actor, Permission.MATCH_PROPOSE)
    case = load_case(db, actor, case_id)
    if case.status not in MATCH_CONFIRMATION_SOURCES:
        raise Conflict(f"{case.status.title()} cases are not matched automatically", code="case_not_matchable")
    return generate_for_case(db, case.id, actor=actor)


def run_after_change(case_id: uuid.UUID) -> None:
    """Best effort, in its own session: a matching failure never fails the registration or edit."""
    if not get_settings().matching_auto_enabled:
        return
    from app.database import SessionLocal

    try:
        with SessionLocal() as db:
            generate_for_case(db, case_id)
    except Exception:  # noqa: BLE001
        MATCHING_FAILURES.inc()
        logger.exception("Automatic matching failed", extra={"case_id": str(case_id)})


def rematch(db: Session, *, since_days: int, limit: int) -> tuple[int, int]:
    cutoff = func.now() - timedelta(days=since_days)
    ids = list(db.scalars(
        select(Case.id)
        .where(Case.status.in_(MATCH_CONFIRMATION_SOURCES), Case.created_at >= cutoff, Case.case_type.in_(LEFT_TYPES))
        .order_by(Case.created_at.desc())
        .limit(limit)
    ))
    created = 0
    for case_id in ids:
        created += len(generate_for_case(db, case_id))
    return len(ids), created
