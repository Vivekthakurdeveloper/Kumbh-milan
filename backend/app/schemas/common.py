from datetime import timedelta
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.common.time import utcnow

MAX_PAGE_SIZE = 100


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _not_in_future(value: AwareDatetime) -> AwareDatetime:
    if value > utcnow() + timedelta(minutes=5):
        raise ValueError("must not be in the future")
    return value


PastDatetime = Annotated[AwareDatetime, AfterValidator(_not_in_future)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
LocationText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=5000)]
Reason = Annotated[str, StringConstraints(min_length=3, max_length=2000)]


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(description="Total rows matching the filters")
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class PageParams(BaseModel):
    limit: int = Field(25, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(0, ge=0, le=100_000)
