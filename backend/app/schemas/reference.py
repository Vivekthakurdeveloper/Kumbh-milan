import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import CaseStatus
from app.schemas.common import ReadModel, StrictModel


class ItemCategoryRead(ReadModel):
    code: str
    label_en: str
    label_hi: str
    sort_order: int
    is_active: bool


class ItemCategoryUpsert(StrictModel):
    label_en: str = Field(min_length=1, max_length=100)
    label_hi: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(0, ge=0, le=10_000)
    is_active: bool = True


class EnumLabel(BaseModel):
    code: str
    label_en: str
    label_hi: str


class DuplicateCandidate(BaseModel):
    case_id: uuid.UUID
    case_number: str
    status: CaseStatus
    station_id: uuid.UUID
    created_at: datetime
    reasons: list[str] = Field(description="Why this case looks like a duplicate; a person must decide")
    score: int
