"""Unauthenticated citizen portal endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile, status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.models.enums import OtpPurpose
from app.schemas.common import Page, PageParams
from app.schemas.public import (
    CaseStatusTokenResponse,
    OtpRequest,
    OtpRequestResponse,
    OtpVerify,
    PublicCaseStatusRead,
    PublicInformationCreate,
    PublicInformationList,
    PublicInformationRead,
    PublicInformationUpdate,
    PublicMissingPersonCreate,
    PublicReviewRequest,
    PublicSightingAck,
    PublicSightingCreate,
    PublicSubmissionAck,
    SightingRead,
    StatusOtpRequest,
    StatusOtpVerify,
    VerifiedMobileResponse,
)
from app.schemas.workflow import AttachmentDownload
from app.services import otp as otp_service
from app.services import public_cases as public_case_service
from app.services import public_information as public_information_service
from app.services import public_sightings as sighting_service
from app.storage import ObjectStorage, get_storage
from app.storage.scanner import MalwareScanner, get_scanner

router = APIRouter(prefix="/public", tags=["Public portal"])
admin_router = APIRouter(prefix="/admin/public-information", tags=["Public information admin"])
sightings_router = APIRouter(prefix="/sightings", tags=["Sightings"])

StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
ScannerDep = Annotated[MalwareScanner, Depends(get_scanner)]
VerifiedMobileToken = Annotated[str | None, Header(alias="X-Verified-Mobile-Token")]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]


@router.post("/otp/request", response_model=OtpRequestResponse, responses=errors(422, 429))
def request_otp(payload: OtpRequest, db: DbSession):
    from app.common.errors import ValidationFailed

    if payload.purpose is OtpPurpose.CASE_STATUS:
        raise ValidationFailed("Use /public/status/request-otp for case status access", code="wrong_endpoint")
    return otp_service.request_otp(
        db, purpose=payload.purpose, mobile_number=payload.mobile_number, locale=payload.locale
    )


@router.post("/otp/verify", response_model=VerifiedMobileResponse, responses=errors(401, 422, 429))
def verify_otp(payload: OtpVerify, db: DbSession):
    if payload.purpose is OtpPurpose.CASE_STATUS:
        from app.common.errors import ValidationFailed

        raise ValidationFailed("Use /public/status/verify-otp for case status access", code="wrong_endpoint")
    return otp_service.verify_otp(
        db, purpose=payload.purpose, mobile_number=payload.mobile_number, code=payload.code
    )


@router.post("/status/request-otp", response_model=OtpRequestResponse, responses=errors(422, 429))
def status_request_otp(payload: StatusOtpRequest, db: DbSession):
    return otp_service.request_otp(
        db,
        purpose=OtpPurpose.CASE_STATUS,
        mobile_number=payload.mobile_number,
        locale=payload.locale,
        case_number=payload.case_id,
    )


@router.post("/status/verify-otp", response_model=CaseStatusTokenResponse, responses=errors(401, 422, 429))
def status_verify_otp(payload: StatusOtpVerify, db: DbSession):
    return otp_service.verify_otp(
        db,
        purpose=OtpPurpose.CASE_STATUS,
        mobile_number=payload.mobile_number,
        code=payload.code,
        case_number=payload.case_id,
    )


@router.get(
    "/cases/{case_id}/status",
    response_model=PublicCaseStatusRead,
    responses=errors(401, 404),
)
def case_status(case_id: str, db: DbSession, authorization: AuthorizationHeader = None):
    return public_case_service.public_status_for_token(db, case_number=case_id, authorization=authorization)


@router.post(
    "/missing-persons",
    response_model=PublicSubmissionAck,
    status_code=status.HTTP_201_CREATED,
    responses=errors(401, 409, 413, 415, 422, 429, 503),
)
def submit_missing_person(
    db: DbSession,
    storage: StorageDep,
    scanner: ScannerDep,
    response: Response,
    report: Annotated[str, Form(description="JSON body matching PublicMissingPersonCreate")],
    photo: Annotated[UploadFile | None, File()] = None,
    verified_mobile_token: VerifiedMobileToken = None,
    idempotency_key: IdempotencyKey = None,
):
    if not verified_mobile_token:
        from app.common.errors import Unauthenticated

        raise Unauthenticated("X-Verified-Mobile-Token is required")
    payload = public_case_service.parse_public_report(report)
    ack, replayed = public_case_service.submit_missing_person(
        db,
        storage,
        scanner,
        payload=payload,
        verified_mobile_token=verified_mobile_token,
        idempotency_key=idempotency_key,
        photo=photo.file if photo else None,
        photo_filename=photo.filename if photo else None,
        photo_content_type=photo.content_type if photo else None,
    )
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
        response.status_code = status.HTTP_200_OK
    return ack


@router.post(
    "/sightings",
    response_model=PublicSightingAck,
    status_code=status.HTTP_201_CREATED,
    responses=errors(401, 413, 415, 422, 429, 503),
)
async def submit_sighting(
    db: DbSession,
    storage: StorageDep,
    scanner: ScannerDep,
    report: Annotated[str, Form()],
    photo: Annotated[UploadFile | None, File()] = None,
    verified_mobile_token: VerifiedMobileToken = None,
):
    import json

    from app.common.errors import Unauthenticated, ValidationFailed

    if not verified_mobile_token:
        raise Unauthenticated("X-Verified-Mobile-Token is required")
    try:
        payload = PublicSightingCreate.model_validate(json.loads(report))
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed("report must be valid JSON", code="invalid_report_json") from exc
    sighting = sighting_service.submit_sighting(
        db,
        storage,
        scanner,
        payload=payload,
        verified_mobile_token=verified_mobile_token,
        photo=photo.file if photo else None,
        photo_filename=photo.filename if photo else None,
        photo_content_type=photo.content_type if photo else None,
    )
    return PublicSightingAck(
        sighting_id=sighting.id, status=sighting.status, message="Sighting submitted for staff review"
    )


@router.get("/information", response_model=PublicInformationList)
def public_information(db: DbSession):
    items = public_information_service.list_published(db)
    return PublicInformationList(items=[PublicInformationRead.model_validate(i) for i in items])


@admin_router.get("", response_model=list[PublicInformationRead], responses=AUTHED)
def admin_list_information(principal: CurrentPrincipal, db: DbSession):
    return [PublicInformationRead.model_validate(i) for i in public_information_service.list_all(db, principal.user)]


@admin_router.post(
    "", response_model=PublicInformationRead, status_code=status.HTTP_201_CREATED, responses=AUTHED
)
def admin_create_information(payload: PublicInformationCreate, principal: CurrentPrincipal, db: DbSession):
    return public_information_service.create_item(db, principal.user, payload)


@admin_router.patch("/{item_id}", response_model=PublicInformationRead, responses=AUTHED)
def admin_update_information(
    item_id: uuid.UUID, payload: PublicInformationUpdate, principal: CurrentPrincipal, db: DbSession
):
    return public_information_service.update_item(db, principal.user, item_id, payload)


@admin_router.post("/{item_id}/publish", response_model=PublicInformationRead, responses=AUTHED)
def admin_publish_information(item_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return public_information_service.publish_item(db, principal.user, item_id, publish=True)


@admin_router.post("/{item_id}/unpublish", response_model=PublicInformationRead, responses=AUTHED)
def admin_unpublish_information(item_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return public_information_service.publish_item(db, principal.user, item_id, publish=False)


@sightings_router.get("", response_model=Page[SightingRead], responses=AUTHED)
def list_sightings(
    principal: CurrentPrincipal,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    case_id: uuid.UUID | None = None,
):
    rows, total = sighting_service.list_sightings(
        db, principal.user, case_id=case_id, limit=page.limit, offset=page.offset
    )
    items = [
        SightingRead.model_validate(r).model_copy(
            update={"has_photo": sighting_service.sighting_has_photo(db, r.id)}
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@sightings_router.get("/{sighting_id}", response_model=SightingRead, responses=AUTHED)
def get_sighting(sighting_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    row = sighting_service.get_sighting(db, principal.user, sighting_id)
    return SightingRead.model_validate(row).model_copy(
        update={"has_photo": sighting_service.sighting_has_photo(db, row.id)}
    )


@sightings_router.get("/{sighting_id}/photo", response_model=AttachmentDownload, responses=AUTHED)
def get_sighting_photo(
    sighting_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, storage: StorageDep
):
    url, ttl, content_type = sighting_service.sighting_photo_url(db, storage, principal.user, sighting_id)
    return AttachmentDownload(url=url, expires_in=ttl, content_type=content_type)
