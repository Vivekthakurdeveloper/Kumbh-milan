import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.enums import (
    HandoverKind,
    HandoverStatus,
    IdentityDocumentType,
    IdentityVerificationMethod,
)
from app.schemas.common import LocationText, PastDatetime, ShortText, StrictModel


class HandoverCreateRequest(StrictModel):
    match_id: uuid.UUID | None = Field(
        None,
        description="Confirmed person/item match. Required for person reunification.",
    )
    claimant_full_name: ShortText
    claimant_relationship: str = Field(min_length=2, max_length=100)
    claimant_mobile_number: str | None = Field(None, min_length=8, max_length=20)
    claimant_address: str | None = Field(None, min_length=3, max_length=500)
    identity_document_type: IdentityDocumentType | None = None
    identity_document_reference: str | None = Field(None, min_length=2, max_length=200)
    witness_full_name: ShortText | None = None
    witness_mobile_number: str | None = Field(None, min_length=8, max_length=20)
    witness_relationship: str | None = Field(None, min_length=2, max_length=100)
    witness_statement: str | None = Field(None, min_length=3, max_length=2000)
    evidence_attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    expected_case_version: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _document_and_witness_fields_are_consistent(self):
        if (self.identity_document_type is None) != (self.identity_document_reference is None):
            raise ValueError("identity_document_type and identity_document_reference must be sent together")
        witness_values = (
            self.witness_mobile_number,
            self.witness_relationship,
            self.witness_statement,
        )
        if any(value is not None for value in witness_values) and self.witness_full_name is None:
            raise ValueError("witness_full_name is required when witness details are sent")
        return self


class HandoverVerifyRequest(StrictModel):
    decision: Literal["VERIFIED", "REJECTED"]
    verification_method: IdentityVerificationMethod | None = None
    evidence: str = Field(min_length=10, max_length=5000)
    expected_version: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _verified_requires_method(self):
        if self.decision == "VERIFIED" and self.verification_method is None:
            raise ValueError("verification_method is required when decision is VERIFIED")
        return self


class HandoverCompleteRequest(StrictModel):
    handover_location: LocationText
    handover_at: PastDatetime
    acknowledgement: str = Field(min_length=10, max_length=2000)
    expected_version: int | None = Field(None, ge=1)


class HandoverRead(StrictModel):
    id: uuid.UUID
    primary_case_id: uuid.UUID
    counterpart_case_id: uuid.UUID | None
    match_id: uuid.UUID | None
    kind: HandoverKind
    status: HandoverStatus
    claimant_full_name: str | None
    claimant_relationship: str | None
    claimant_mobile_number: str | None
    claimant_address: str | None
    identity_document_type: IdentityDocumentType | None
    identity_document_reference: str | None
    witness_full_name: str | None
    witness_mobile_number: str | None
    witness_relationship: str | None
    witness_statement: str | None
    evidence_attachment_ids: list[uuid.UUID]
    verification_method: IdentityVerificationMethod | None
    verification_evidence: str | None
    verified_by_user_id: uuid.UUID | None
    verified_at: datetime | None
    handover_location: str | None
    handover_at: datetime | None
    acknowledgement: str | None
    completed_by_user_id: uuid.UUID | None
    completed_at: datetime | None
    rejection_reason: str | None
    rejected_by_user_id: uuid.UUID | None
    rejected_at: datetime | None
    created_by_user_id: uuid.UUID
    anonymized_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class HandoverHistoryEventRead(StrictModel):
    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None
    status: HandoverStatus | None
    created_at: datetime
