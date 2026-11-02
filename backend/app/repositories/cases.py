import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import ColumnElement, Select, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.models import (
    Case,
    CaseContact,
    CaseNumberCounter,
    FoundItemDetails,
    FoundPersonDetails,
    LostItemDetails,
    MissingPersonDetails,
)
from app.models.enums import AgeGroup, CaseStatus, CaseType, Gender, UrgencyLevel

_MP, _FP, _LI, _FI = MissingPersonDetails, FoundPersonDetails, LostItemDetails, FoundItemDetails

_SORTS = {
    "-created_at": (Case.created_at.desc(), Case.id.desc()),
    "created_at": (Case.created_at.asc(), Case.id.asc()),
    "-updated_at": (Case.updated_at.desc(), Case.id.desc()),
    "updated_at": (Case.updated_at.asc(), Case.id.asc()),
}


@dataclass
class CaseFilters:
    case_type: CaseType | None = None
    q: str | None = None
    case_number: str | None = None
    statuses: list[CaseStatus] = field(default_factory=list)
    urgency_level: UrgencyLevel | None = None
    station_id: uuid.UUID | None = None
    assigned_to_user_id: uuid.UUID | None = None
    unassigned: bool | None = None
    location: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    event_from: datetime | None = None
    event_to: datetime | None = None
    item_category: str | None = None
    age_group: AgeGroup | None = None
    gender: Gender | None = None
    language: str | None = None
    include_archived: bool = False
    sort: str = "-created_at"


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _eager_details(stmt: Select) -> Select:
    return stmt.options(
        joinedload(Case.missing_person),
        joinedload(Case.found_person),
        joinedload(Case.lost_item),
        joinedload(Case.found_item),
    )


def get_case(
    db: Session, case_id: uuid.UUID, visibility: ColumnElement[bool], *, for_update: bool = False
) -> Case | None:
    stmt = _eager_details(select(Case).where(Case.id == case_id, visibility))
    if for_update:
        stmt = stmt.with_for_update(of=Case)
    return db.scalars(stmt).unique().first()


def case_exists_in_scope(db: Session, case_id: uuid.UUID, visibility: ColumnElement[bool]) -> bool:
    return db.scalar(select(Case.id).where(Case.id == case_id, visibility)) is not None


def has_contact(db: Session, case_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not case_ids:
        return set()
    return set(db.scalars(select(CaseContact.case_id).where(CaseContact.case_id.in_(case_ids))))


def allocate_case_number_value(db: Session, case_type: CaseType, year: int) -> int:
    """Atomic upsert; the row lock serialises concurrent allocations until the transaction ends."""
    stmt = (
        insert(CaseNumberCounter)
        .values(case_type=case_type, year=year, last_value=1)
        .on_conflict_do_update(
            index_elements=[CaseNumberCounter.case_type, CaseNumberCounter.year],
            set_={"last_value": CaseNumberCounter.last_value + 1},
        )
        .returning(CaseNumberCounter.last_value)
    )
    return db.execute(stmt).scalar_one()


def search_cases(
    db: Session,
    filters: CaseFilters,
    visibility: ColumnElement[bool],
    *,
    limit: int,
    offset: int,
) -> tuple[list[Case], int]:
    location = func.coalesce(
        _MP.last_seen_location, _FP.found_location, _LI.last_seen_location, _FI.found_location
    )
    event_at = func.coalesce(_MP.last_seen_at, _FP.found_at, _LI.last_seen_at, _FI.found_at)

    conditions: list[ColumnElement[bool]] = [visibility]
    if not filters.include_archived:
        conditions.append(Case.archived_at.is_(None))
    if filters.case_type:
        conditions.append(Case.case_type == filters.case_type)
    if filters.q:
        tsquery = func.websearch_to_tsquery(literal_column("'simple'::regconfig"), filters.q)
        conditions.append(
            or_(Case.search_vector.op("@@")(tsquery), Case.case_number.ilike(_like(filters.q)))
        )
    if filters.case_number:
        conditions.append(Case.case_number.ilike(_like(filters.case_number)))
    if filters.statuses:
        conditions.append(Case.status.in_(filters.statuses))
    if filters.urgency_level:
        conditions.append(Case.urgency_level == filters.urgency_level)
    if filters.station_id:
        conditions.append(Case.station_id == filters.station_id)
    if filters.assigned_to_user_id:
        conditions.append(Case.assigned_to_user_id == filters.assigned_to_user_id)
    if filters.unassigned is True:
        conditions.append(Case.assigned_to_user_id.is_(None))
    elif filters.unassigned is False:
        conditions.append(Case.assigned_to_user_id.is_not(None))
    if filters.location:
        conditions.append(location.ilike(_like(filters.location)))
    if filters.created_from:
        conditions.append(Case.created_at >= filters.created_from)
    if filters.created_to:
        conditions.append(Case.created_at < filters.created_to)
    if filters.event_from:
        conditions.append(event_at >= filters.event_from)
    if filters.event_to:
        conditions.append(event_at < filters.event_to)
    if filters.item_category:
        cat = filters.item_category.lower()
        conditions.append(or_(func.lower(_LI.item_category) == cat, func.lower(_FI.item_category) == cat))
    if filters.age_group:
        conditions.append(or_(_MP.age_group == filters.age_group, _FP.age_group == filters.age_group))
    if filters.gender:
        conditions.append(or_(_MP.gender == filters.gender, _FP.estimated_gender == filters.gender))
    if filters.language:
        lang = _like(filters.language)
        conditions.append(or_(_MP.language.ilike(lang), _FP.language.ilike(lang)))

    def joined(stmt: Select) -> Select:
        return (
            stmt.outerjoin(_MP, _MP.case_id == Case.id)
            .outerjoin(_FP, _FP.case_id == Case.id)
            .outerjoin(_LI, _LI.case_id == Case.id)
            .outerjoin(_FI, _FI.case_id == Case.id)
            .where(*conditions)
        )

    total = db.scalar(joined(select(func.count(Case.id)).select_from(Case))) or 0
    stmt = (
        joined(select(Case))
        .options(
            contains_eager(Case.missing_person),
            contains_eager(Case.found_person),
            contains_eager(Case.lost_item),
            contains_eager(Case.found_item),
        )
        .order_by(*_SORTS.get(filters.sort, _SORTS["-created_at"]))
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt).unique().all()), total
