"""One spec per case type. Routers and services are generated from these, so workflow code exists once."""

from dataclasses import dataclass

from pydantic import BaseModel

from app.database.base import Base
from app.models import FoundItemDetails, FoundPersonDetails, LostItemDetails, MissingPersonDetails
from app.models.enums import CaseType
from app.schemas import cases as s


@dataclass(frozen=True)
class CaseTypeSpec:
    case_type: CaseType
    code: str
    slug: str
    label: str
    relationship: str
    detail_model: type[Base]
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    read_schema: type[s.CaseRead]
    details_read_schema: type[BaseModel]
    search_fields: tuple[str, ...]
    title_field: str | None
    location_field: str
    event_time_field: str

    @property
    def is_person(self) -> bool:
        return self.case_type in (CaseType.MISSING_PERSON, CaseType.FOUND_PERSON)


MISSING_PERSON = CaseTypeSpec(
    case_type=CaseType.MISSING_PERSON,
    code="MP",
    slug="missing-persons",
    label="Missing person",
    relationship="missing_person",
    detail_model=MissingPersonDetails,
    create_schema=s.MissingPersonCreate,
    update_schema=s.MissingPersonUpdate,
    read_schema=s.MissingPersonCaseRead,
    details_read_schema=s.MissingPersonDetailsRead,
    search_fields=(
        "person_name",
        "height_description",
        "clothing_description",
        "identifying_marks",
        "language",
        "last_seen_location",
        "medical_needs",
        "additional_description",
    ),
    title_field="person_name",
    location_field="last_seen_location",
    event_time_field="last_seen_at",
)

FOUND_PERSON = CaseTypeSpec(
    case_type=CaseType.FOUND_PERSON,
    code="FP",
    slug="found-persons",
    label="Found person",
    relationship="found_person",
    detail_model=FoundPersonDetails,
    create_schema=s.FoundPersonCreate,
    update_schema=s.FoundPersonUpdate,
    read_schema=s.FoundPersonCaseRead,
    details_read_schema=s.FoundPersonDetailsRead,
    search_fields=(
        "physical_description",
        "clothing_description",
        "identifying_marks",
        "language",
        "found_location",
        "care_or_assistance_required",
        "current_care_location",
    ),
    title_field=None,
    location_field="found_location",
    event_time_field="found_at",
)

LOST_ITEM = CaseTypeSpec(
    case_type=CaseType.LOST_ITEM,
    code="LI",
    slug="lost-items",
    label="Lost item",
    relationship="lost_item",
    detail_model=LostItemDetails,
    create_schema=s.LostItemCreate,
    update_schema=s.LostItemUpdate,
    read_schema=s.LostItemCaseRead,
    details_read_schema=s.LostItemDetailsRead,
    search_fields=(
        "item_category",
        "item_name",
        "item_description",
        "brand_or_color",
        "identifying_features",
        "last_seen_location",
    ),
    title_field="item_name",
    location_field="last_seen_location",
    event_time_field="last_seen_at",
)

FOUND_ITEM = CaseTypeSpec(
    case_type=CaseType.FOUND_ITEM,
    code="FI",
    slug="found-items",
    label="Found item",
    relationship="found_item",
    detail_model=FoundItemDetails,
    create_schema=s.FoundItemCreate,
    update_schema=s.FoundItemUpdate,
    read_schema=s.FoundItemCaseRead,
    details_read_schema=s.FoundItemDetailsRead,
    search_fields=(
        "item_category",
        "item_name",
        "item_description",
        "item_condition",
        "found_location",
        "storage_location",
    ),
    title_field="item_name",
    location_field="found_location",
    event_time_field="found_at",
)

CASE_TYPE_SPECS: dict[CaseType, CaseTypeSpec] = {
    spec.case_type: spec for spec in (MISSING_PERSON, FOUND_PERSON, LOST_ITEM, FOUND_ITEM)
}


def spec_for(case_type: CaseType | str) -> CaseTypeSpec:
    return CASE_TYPE_SPECS[CaseType(case_type)]
