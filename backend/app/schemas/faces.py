import uuid
from datetime import datetime

from pydantic import Field

from app.models.enums import (
    MatchDomain,
    MatchReviewStage,
    MatchSource,
    MatchStatus,
    StaffNotificationKind,
    StaffNotificationStatus,
)
from app.schemas.cases import CaseSummary
from app.schemas.common import ReadModel
from app.schemas.workflow import MatchReason


class MatchDetail(ReadModel):
    """Side-by-side review payload for an authorised officer. Embeddings are never included."""

    id: uuid.UUID
    match_domain: MatchDomain
    status: MatchStatus
    source: MatchSource
    review_stage: MatchReviewStage
    score: float | None = None
    confidence_band: str | None = None
    reasons: list[MatchReason] | None = None
    engine_version: str | None = None
    left_case_id: uuid.UUID
    right_case_id: uuid.UUID
    left_case_version: int
    right_case_version: int
    left_case: CaseSummary
    right_case: CaseSummary
    left_attachment_id: uuid.UUID | None = None
    right_attachment_id: uuid.UUID | None = None
    face_similarity: float | None = Field(
        None, description="Actual model cosine similarity. Not proof of identity."
    )
    face_similarity_threshold: float | None = None
    face_model: str | None = None
    proposed_by_user_id: uuid.UUID | None = None
    rationale: str | None = None
    first_approved_by_user_id: uuid.UUID | None = None
    first_approved_at: datetime | None = None
    first_approval_evidence: str | None = None
    reviewed_by_user_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    review_evidence: str | None = None
    revoked_by_user_id: uuid.UUID | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    created_at: datetime


class MatchPhotoUrl(ReadModel):
    side: str
    attachment_id: uuid.UUID
    url: str
    expires_in: int
    content_type: str


class MatchHistoryEvent(ReadModel):
    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None
    after_state: dict | None
    reason: str | None
    created_at: datetime


class StaffNotificationRead(ReadModel):
    id: uuid.UUID
    kind: StaffNotificationKind
    status: StaffNotificationStatus
    match_id: uuid.UUID
    link: str = Field(description="Relative path to the match review screen")
    created_at: datetime
    read_at: datetime | None = None
