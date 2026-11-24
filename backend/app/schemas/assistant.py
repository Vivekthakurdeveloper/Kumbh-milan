from typing import Literal

from pydantic import Field

from app.schemas.common import StrictModel

AssistantLocale = Literal["en", "hi"]


class AssistantAskRequest(StrictModel):
    question: str = Field(
        min_length=1, max_length=1000, description="Trimmed first; empty or whitespace-only is rejected (422)",
        examples=["How do I register a missing person?"],
    )
    locale: AssistantLocale = Field("en", description="Language of the answer")


class AssistantAnswerResponse(StrictModel):
    answer: str
    locale: AssistantLocale = Field(description="Language the answer is written in")
    sources: list[str] = Field(
        default_factory=list, description="Titles of the help articles the answer is based on; [] when none"
    )
