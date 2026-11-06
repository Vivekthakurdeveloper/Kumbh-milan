import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from app.models.enums import (
    CaseStatus,
    ConfidenceBand,
    CustodyStatus,
    FaceStatus,
    MatchDomain,
    MatchReviewStage,
    MatchSource,
    MatchStatus,
    MediaType,
    NoteType,
    ScanStatus,
)
from app.schemas.cases import CaseSummary
from app.schemas.common import LocationText, PastDatetime, ReadModel, Reason, StrictModel

Evidence = Field(None, min_length=10, max_length=5000)
ExpectedVersion = Field(
    None, ge=1, description="Case version the client last saw; a mismatch returns 409 case_changed instead of applying"
)


class AssignRequest(StrictModel):
    assignee_user_id: uuid.UUID
    reason: Reason | None = None
    expected_version: int | None = ExpectedVersion


class StatusChangeRequest(StrictModel):
    status: CaseStatus
    reason: Reason
    match_id: uuid.UUID | None = Field(None, description="Confirmed match supporting MATCH_FOUND or RESOLVED")
    verification_evidence: str | None = Field(
        None,
        min_length=10,
        max_length=5000,
        description="How identity or ownership was verified (required to resolve without a confirmed match)",
    )
    expected_version: int | None = ExpectedVersion


class CustodyChangeRequest(StrictModel):
    custody_status: CustodyStatus
    reason: Reason
    storage_location: LocationText | None = None
    recipient_acknowledgement: str | None = Field(
        None, min_length=10, max_length=500,
        description="Required to release an item. Record who acknowledged the handover; do not put contact numbers here.",
    )


class NoteCreate(StrictModel):
    note_text: str = Field(min_length=1, max_length=10000)
    note_type: NoteType = NoteType.OPERATIONAL
    is_sensitive: bool = False
    corrects_note_id: uuid.UUID | None = Field(None, description="Required when note_type is CORRECTION")
    client_id: Annotated[str | None, StringConstraints(min_length=1, max_length=128)] = None
    device_id: Annotated[str | None, StringConstraints(min_length=1, max_length=128)] = None
    client_record_id: Annotated[str | None, StringConstraints(min_length=1, max_length=128)] = None
    client_created_at: PastDatetime | None = None

    @model_validator(mode="after")
    def _sync_fields_consistent(self):
        if self.client_record_id is not None and self.device_id is None:
            raise ValueError("device_id is required when client_record_id is sent")
        return self


class NoteRead(ReadModel):
    id: uuid.UUID
    case_id: uuid.UUID
    author_user_id: uuid.UUID
    note_text: str
    note_type: NoteType
    is_sensitive: bool
    corrects_note_id: uuid.UUID | None
    created_at: datetime


class AssignmentRead(ReadModel):
    id: uuid.UUID
    assigned_to_user_id: uuid.UUID
    assigned_by_user_id: uuid.UUID
    assigned_at: datetime
    unassigned_at: datetime | None
    reason: str | None
    is_active: bool


class StatusHistoryRead(ReadModel):
    id: uuid.UUID
    from_status: CaseStatus | None
    to_status: CaseStatus
    changed_by_user_id: uuid.UUID
    reason: str | None
    verification_evidence: str | None
    match_id: uuid.UUID | None
    created_at: datetime


class CaseHistory(StrictModel):
    status_history: list[StatusHistoryRead]
    assignments: list[AssignmentRead]


class MatchProposeRequest(StrictModel):
    other_case_id: uuid.UUID
    rationale: str = Field(min_length=10, max_length=5000)


class MatchReviewRequest(StrictModel):
    decision: Literal[MatchStatus.CONFIRMED, MatchStatus.REJECTED]
    evidence: str = Field(min_length=10, max_length=5000, description="What was checked, and with whom")
    left_case_version: int | None = Field(
        None, ge=1, description="Version of the missing/lost case when review began; stale versions return 409"
    )
    right_case_version: int | None = Field(
        None, ge=1, description="Version of the found case when review began; stale versions return 409"
    )


class MatchRevokeRequest(StrictModel):
    reason: str = Field(min_length=10, max_length=5000, description="Why the confirmed match was wrong")


class MatchReason(StrictModel):
    feature: str
    weight: float
    similarity: float
    contribution: float
    detail: str


class MatchRead(StrictModel):
    id: uuid.UUID
    match_domain: MatchDomain
    status: MatchStatus
    source: MatchSource
    review_stage: MatchReviewStage
    score: float | None = Field(description="Automatic / face suggestions: 0-1 ranking score. Not proof of identity.")
    confidence_band: ConfidenceBand | None
    reasons: list[MatchReason] | None = Field(description="Per-feature explanation of an automatic score")
    engine_version: str | None
    left_case_id: uuid.UUID
    right_case_id: uuid.UUID
    left_case_version: int
    right_case_version: int
    counterpart: CaseSummary
    left_attachment_id: uuid.UUID | None = None
    right_attachment_id: uuid.UUID | None = None
    face_similarity: float | None = Field(
        None, description="Actual SFace cosine similarity when a face candidate exists. Not proof of identity."
    )
    face_model: str | None = None
    proposed_by_user_id: uuid.UUID | None
    rationale: str | None
    first_approved_by_user_id: uuid.UUID | None = None
    first_approved_at: datetime | None = None
    first_approval_evidence: str | None = None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_evidence: str | None
    revoked_by_user_id: uuid.UUID | None
    revoked_at: datetime | None
    revocation_reason: str | None
    created_at: datetime


class MatchQueueItem(StrictModel):
    """Cross-case review queue row. Both sides are summarised (and redacted) for the caller."""

    id: uuid.UUID
    match_domain: MatchDomain
    status: MatchStatus
    source: MatchSource
    review_stage: MatchReviewStage
    score: float | None = Field(description="Automatic / face suggestions: 0-1 ranking score. Not proof of identity.")
    confidence_band: ConfidenceBand | None
    reasons: list[MatchReason] | None = Field(description="Per-feature explanation of an automatic score")
    engine_version: str | None
    left_case_id: uuid.UUID
    right_case_id: uuid.UUID
    left_case_version: int
    right_case_version: int
    left_case: CaseSummary
    right_case: CaseSummary
    left_attachment_id: uuid.UUID | None = None
    right_attachment_id: uuid.UUID | None = None
    face_similarity: float | None = None
    face_model: str | None = None
    proposed_by_user_id: uuid.UUID | None
    rationale: str | None
    first_approved_by_user_id: uuid.UUID | None = None
    first_approved_at: datetime | None = None
    first_approval_evidence: str | None = None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_evidence: str | None
    revoked_by_user_id: uuid.UUID | None
    revoked_at: datetime | None
    revocation_reason: str | None
    created_at: datetime


class ContactRevealRequest(StrictModel):
    reason_code: Literal[
        "REUNIFICATION_REVIEW", "FAMILY_ENQUIRY", "INVESTIGATION", "CARE_HANDOVER",
    ]
    note: str | None = Field(
        None, max_length=200,
        description="Optional operational note. It is not stored; the audit record keeps only reason_code.",
    )


class ContactRead(StrictModel):
    case_id: uuid.UUID
    contact_details: str


class AttachmentRead(ReadModel):
    id: uuid.UUID
    case_id: uuid.UUID
    uploaded_by_user_id: uuid.UUID
    media_type: MediaType
    original_filename: str | None
    content_type: str
    file_size_bytes: int
    checksum_sha256: str
    scan_status: ScanStatus
    scanned_at: datetime | None = None
    downloadable: bool = Field(False, description="True once the file passed malware scanning")
    face_status: FaceStatus | None = Field(
        None, description="Face analysis outcome for person photographs; embeddings are never exposed"
    )
    created_at: datetime


class AttachmentDownload(StrictModel):
    url: str
    expires_in: int
    content_type: str


class AttachmentDeleteRequest(StrictModel):
    reason: Reason


class AuditEventRead(ReadModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    case_id: uuid.UUID | None
    case_number: str | None
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    reason: str | None
    request_id: str | None
    client_ip: str | None
    created_at: datetime


class MatchGenerateResult(ReadModel):
    created: int = Field(description="New suggestions stored by this run")
    suggestions: list[MatchRead]
