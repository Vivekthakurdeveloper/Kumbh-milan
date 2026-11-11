import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.models.enums import AgeGroup, CaseStatus, CaseType, Gender, MediaType, ScanStatus, UrgencyLevel
from app.repositories.cases import CaseFilters
from app.schemas.cases import (
    CaseSummary,
    FoundItemCaseRead,
    FoundPersonCaseRead,
    LostItemCaseRead,
    MissingPersonCaseRead,
    SortOrder,
)
from app.schemas.common import Page, PageParams
from app.schemas.geo import CaseMap, NearbyCase
from app.schemas.notifications import NotificationCreate, NotificationRead
from app.schemas.public import PublicReviewRequest
from app.schemas.workflow import (
    MatchGenerateResult,
    AssignRequest,
    AttachmentDeleteRequest,
    AttachmentDownload,
    AttachmentRead,
    CaseHistory,
    ContactRead,
    ContactRevealRequest,
    CustodyChangeRequest,
    MatchProposeRequest,
    MatchRead,
    MatchReviewRequest,
    MatchRevokeRequest,
    NoteCreate,
    NoteRead,
    StatusChangeRequest,
)
from app.services import attachments as attachment_service
from app.services import cases as case_service
from app.services import geo as geo_service
from app.services import matching as matching_service
from app.services import notifications as notification_service
from app.services import public_cases as public_case_service
from app.services import workflow as workflow_service
from app.services.case_types import CASE_TYPE_SPECS, CaseTypeSpec
from app.storage import ObjectStorage, get_storage
from app.storage.scanner import MalwareScanner, get_scanner

StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
ScannerDep = Annotated[MalwareScanner, Depends(get_scanner)]
PageDep = Annotated[PageParams, Depends()]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", description="Client-generated key (8-128 chars) that makes retries safe"),
]
NOT_FOUND = {**AUTHED, **errors(404)}


def attachment_read(attachment, db=None) -> AttachmentRead:
    face_status = None
    if db is not None and attachment.media_type == MediaType.PHOTO:
        from app.services.faces import face_status_for_attachment

        face_status = face_status_for_attachment(db, attachment.id)
    return AttachmentRead.model_validate(attachment).model_copy(
        update={
            "downloadable": attachment_service.is_downloadable(attachment),
            "face_status": face_status,
        }
    )


def _mark_replay(response: Response, replayed: bool) -> None:
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"


STATE = {**AUTHED, **errors(404, 409)}


def case_filters(
    q: Annotated[str | None, Query(max_length=200, description="Full-text search over descriptions and case number")] = None,
    case_number: Annotated[str | None, Query(max_length=32)] = None,
    status_: Annotated[list[CaseStatus] | None, Query(alias="status")] = None,
    urgency_level: UrgencyLevel | None = None,
    station_id: uuid.UUID | None = None,
    assigned_to_user_id: uuid.UUID | None = None,
    unassigned: bool | None = None,
    location: Annotated[str | None, Query(max_length=200)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    event_from: Annotated[datetime | None, Query(description="Last-seen or found time, inclusive")] = None,
    event_to: datetime | None = None,
    item_category: Annotated[str | None, Query(max_length=64)] = None,
    age_group: AgeGroup | None = None,
    gender: Gender | None = None,
    language: Annotated[str | None, Query(max_length=100)] = None,
    include_archived: Annotated[bool, Query(description="Include archived cases (police and admins)")] = False,
    sort: SortOrder = "-created_at",
) -> CaseFilters:
    return CaseFilters(
        q=q, case_number=case_number, statuses=status_ or [], urgency_level=urgency_level, station_id=station_id,
        assigned_to_user_id=assigned_to_user_id, unassigned=unassigned, location=location,
        created_from=created_from, created_to=created_to, event_from=event_from, event_to=event_to,
        item_category=item_category, age_group=age_group, gender=gender, language=language,
        include_archived=include_archived, sort=sort,
    )


FiltersDep = Annotated[CaseFilters, Depends(case_filters)]


def build_case_type_router(spec: CaseTypeSpec) -> APIRouter:
    router = APIRouter(prefix=f"/cases/{spec.slug}", tags=[f"{spec.label} cases"])
    Read = spec.read_schema
    Create = spec.create_schema
    Update = spec.update_schema

    @router.post(
        "", response_model=Read, status_code=status.HTTP_201_CREATED, responses={**AUTHED, **errors(409)},
        name=f"create_{spec.relationship}", summary=f"Register a {spec.label.lower()} case",
    )
    def create(
        payload: Create, principal: CurrentPrincipal, db: DbSession, response: Response, idempotency_key: IdempotencyKey = None
    ):
        case, replayed = case_service.create_case(db, principal.user, spec, payload, idempotency_key=idempotency_key)
        if replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return case_service.to_read(db, case, spec, actor=principal.user)

    @router.get(
        "", response_model=Page[Read], responses=AUTHED, name=f"list_{spec.relationship}",
        summary=f"List {spec.label.lower()} cases in your scope",
    )
    def list_(principal: CurrentPrincipal, db: DbSession, filters: FiltersDep, page: PageDep):
        items, total = case_service.list_cases(db, principal.user, spec, filters, limit=page.limit, offset=page.offset)
        return Page(items=items, total=total, limit=page.limit, offset=page.offset)

    @router.get("/{case_id}", response_model=Read, responses=NOT_FOUND, name=f"get_{spec.relationship}")
    def detail(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
        return case_service.to_read(
            db, case_service.get_case(db, principal.user, case_id, spec), spec, actor=principal.user
        )

    @router.patch("/{case_id}", response_model=Read, responses=STATE, name=f"update_{spec.relationship}")
    def update(
        case_id: uuid.UUID,
        payload: Update,
        principal: CurrentPrincipal,
        db: DbSession,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ):
        case, replayed = case_service.update_case(
            db, principal.user, spec, case_id, payload, idempotency_key=idempotency_key
        )
        _mark_replay(response, replayed)
        return case_service.to_read(db, case, spec, actor=principal.user)

    @router.post(
        "/{case_id}/assign", response_model=Read, responses=STATE, name=f"assign_{spec.relationship}",
        summary="Assign or reassign to a police officer (history is preserved)",
    )
    def assign(
        case_id: uuid.UUID, payload: AssignRequest, principal: CurrentPrincipal, db: DbSession, response: Response,
        idempotency_key: IdempotencyKey = None,
    ):
        replayed = workflow_service.assign_case(db, principal.user, spec, case_id, payload, idempotency_key=idempotency_key)
        _mark_replay(response, replayed)
        db.expire_all()
        return case_service.to_read(
            db, case_service.get_case(db, principal.user, case_id, spec), spec, actor=principal.user
        )

    @router.post(
        "/{case_id}/status", response_model=Read, responses=STATE, name=f"status_{spec.relationship}",
        summary="Apply a validated status transition",
    )
    def change_status(
        case_id: uuid.UUID, payload: StatusChangeRequest, principal: CurrentPrincipal, db: DbSession, response: Response,
        idempotency_key: IdempotencyKey = None,
    ):
        replayed = workflow_service.change_status(
            db, principal.user, spec, case_id, payload, idempotency_key=idempotency_key
        )
        _mark_replay(response, replayed)
        db.expire_all()
        return case_service.to_read(
            db, case_service.get_case(db, principal.user, case_id, spec), spec, actor=principal.user
        )

    if spec.case_type is CaseType.FOUND_ITEM:

        @router.post(
            "/{case_id}/custody", response_model=Read, responses=STATE, name="custody_found_item",
            summary="Change physical custody (separate from case status)",
        )
        def change_custody(
            case_id: uuid.UUID, payload: CustodyChangeRequest, principal: CurrentPrincipal, db: DbSession,
            response: Response, idempotency_key: IdempotencyKey = None,
        ):
            replayed = workflow_service.change_custody(
                db, principal.user, spec, case_id, payload, idempotency_key=idempotency_key
            )
            _mark_replay(response, replayed)
            db.expire_all()
            return case_service.to_read(
                db, case_service.get_case(db, principal.user, case_id, spec), spec, actor=principal.user
            )

    return router


type_routers = [build_case_type_router(spec) for spec in CASE_TYPE_SPECS.values()]

shared = APIRouter(prefix="/cases", tags=["Case operations"])
AnyCaseRead = MissingPersonCaseRead | FoundPersonCaseRead | LostItemCaseRead | FoundItemCaseRead


@shared.get("", response_model=Page[CaseSummary], responses=AUTHED, summary="Search cases across all types")
def search_cases(
    principal: CurrentPrincipal,
    db: DbSession,
    filters: FiltersDep,
    page: PageDep,
    case_type: CaseType | None = None,
    all_stations: Annotated[
        bool, Query(description="Police and admins: include summaries from other stations for matching")
    ] = False,
):
    filters.case_type = case_type
    items, total = case_service.search_all(
        db, principal.user, filters, all_stations=all_stations, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


AllStations = Annotated[bool, Query(description="Police and admins: include cases from other stations")]
StatusFilter = Annotated[list[CaseStatus] | None, Query(alias="status", description="Default: active statuses only")]


@shared.get(
    "/nearby", response_model=list[NearbyCase], responses=AUTHED,
    summary="Cases within a radius of a point, nearest first",
)
def nearby_cases(
    principal: CurrentPrincipal,
    db: DbSession,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    radius_m: Annotated[int, Query(ge=10, description="Metres; capped by GEO_MAX_RADIUS_M (default 10 km)")] = 1000,
    case_type: CaseType | None = None,
    status_filter: StatusFilter = None,
    all_stations: AllStations = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """
    Uses each case's own coordinates (device GPS or map pin) when recorded, otherwise its referenced
    location. Cases with neither are not returned: positions are never guessed from free text.
    """
    return geo_service.nearby_cases(
        db, principal.user, latitude=lat, longitude=lng, radius_m=radius_m, case_type=case_type,
        statuses=status_filter, all_stations=all_stations, limit=limit,
    )


@shared.get("/map", response_model=CaseMap, responses=AUTHED, summary="Case markers inside a bounding box")
def map_cases(
    principal: CurrentPrincipal,
    db: DbSession,
    min_lat: Annotated[float, Query(ge=-90, le=90)],
    min_lng: Annotated[float, Query(ge=-180, le=180)],
    max_lat: Annotated[float, Query(ge=-90, le=90)],
    max_lng: Annotated[float, Query(ge=-180, le=180)],
    case_type: CaseType | None = None,
    status_filter: StatusFilter = None,
    all_stations: AllStations = False,
):
    """Markers carry no names or descriptions. Open a case with GET /cases/{id} for details."""
    return geo_service.map_cases(
        db, principal.user, min_lat=min_lat, min_lng=min_lng, max_lat=max_lat, max_lng=max_lng,
        case_type=case_type, statuses=status_filter, all_stations=all_stations,
    )


@shared.get("/{case_id}", response_model=AnyCaseRead, responses=NOT_FOUND, summary="Get any case by id")
def get_any_case(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return case_service.to_read(db, case_service.get_case(db, principal.user, case_id), actor=principal.user)


@shared.post(
    "/{case_id}/contact/reveal", response_model=ContactRead, responses=NOT_FOUND,
    summary="Reveal owner/family contact details (police and admins; audited with your reason)",
)
def reveal_contact(case_id: uuid.UUID, payload: ContactRevealRequest, principal: CurrentPrincipal, db: DbSession):
    contact = case_service.reveal_contact(db, principal.user, case_id, payload.reason_code)
    return ContactRead(case_id=case_id, contact_details=contact)


@shared.post(
    "/{case_id}/public-review",
    response_model=AnyCaseRead,
    responses=STATE,
    summary="Accept or reject a public citizen submission awaiting staff verification",
)
def public_review(case_id: uuid.UUID, payload: PublicReviewRequest, principal: CurrentPrincipal, db: DbSession):
    case = public_case_service.review_public_case(db, principal.user, case_id, payload)
    return case_service.to_read(db, case, actor=principal.user)


@shared.get("/{case_id}/notes", response_model=Page[NoteRead], responses=NOT_FOUND)
def list_notes(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, page: PageDep):
    """Sensitive notes are only returned to police officers and admins."""
    rows, total = workflow_service.list_notes(db, principal.user, case_id, limit=page.limit, offset=page.offset)
    return Page(items=[NoteRead.model_validate(n) for n in rows], total=total, limit=page.limit, offset=page.offset)


@shared.post("/{case_id}/notes", response_model=NoteRead, status_code=status.HTTP_201_CREATED, responses=NOT_FOUND)
def add_note(
    case_id: uuid.UUID, payload: NoteCreate, principal: CurrentPrincipal, db: DbSession, response: Response,
    idempotency_key: IdempotencyKey = None,
):
    """Notes are append-only. To fix a note, add a CORRECTION that references it."""
    note, replayed = workflow_service.add_note(db, principal.user, case_id, payload, idempotency_key=idempotency_key)
    _mark_replay(response, replayed)
    return note


@shared.get("/{case_id}/attachments", response_model=Page[AttachmentRead], responses=NOT_FOUND)
def list_attachments(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, page: PageDep):
    rows, total = attachment_service.list_attachments(db, principal.user, case_id, limit=page.limit, offset=page.offset)
    return Page(items=[attachment_read(a, db) for a in rows], total=total, limit=page.limit, offset=page.offset)


@shared.post(
    "/{case_id}/attachments", response_model=AttachmentRead, status_code=status.HTTP_201_CREATED,
    responses={**STATE, **errors(413, 415, 422), 202: {"model": AttachmentRead, "description": "Stored in quarantine; scan pending"}},
    summary="Upload a photo (JPEG/PNG/WEBP) or voice recording (WAV/MP3/M4A/OGG/WEBM)",
)
def upload_attachment(
    case_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
    scanner: ScannerDep,
    response: Response,
    media_type: Annotated[MediaType, Form()],
    file: Annotated[UploadFile, File()],
    idempotency_key: IdempotencyKey = None,
    client_id: Annotated[str | None, Form()] = None,
    device_id: Annotated[str | None, Form()] = None,
    client_record_id: Annotated[str | None, Form()] = None,
    client_created_at: Annotated[datetime | None, Form()] = None,
):
    """
    Files are quarantined and scanned before they can be downloaded. 201: scanned (CLEAN, or SKIPPED_DEV in
    development). 202: the scanner was unavailable; the file stays QUARANTINED and is retried automatically.
    422 malware_detected: the file was infected and has been deleted.
    Retries are safe with Idempotency-Key or a stable client_record_id (+ device_id).
    """
    attachment, replayed = attachment_service.upload_attachment(
        db, storage, principal.user, case_id, media_type=media_type, stream=file.file,
        filename=file.filename, declared_content_type=file.content_type, scanner=scanner,
        idempotency_key=idempotency_key, client_id=client_id, device_id=device_id,
        client_record_id=client_record_id, client_created_at=client_created_at,
    )
    _mark_replay(response, replayed)
    if replayed:
        response.status_code = status.HTTP_200_OK
    elif attachment.scan_status in (ScanStatus.QUARANTINED, ScanStatus.SCANNING):
        response.status_code = status.HTTP_202_ACCEPTED
    return attachment_read(attachment, db)


@shared.get(
    "/{case_id}/attachments/{attachment_id}", response_model=AttachmentDownload, responses=STATE,
    summary="Get a short-lived download URL (access is audited)",
)
def download_attachment(
    case_id: uuid.UUID, attachment_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, storage: StorageDep
):
    url, ttl, content_type = attachment_service.download_url(db, storage, principal.user, case_id, attachment_id)
    return AttachmentDownload(url=url, expires_in=ttl, content_type=content_type)


@shared.delete(
    "/{case_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND,
    summary="Withdraw an attachment (police and admins; soft delete)",
)
def delete_attachment(
    case_id: uuid.UUID, attachment_id: uuid.UUID, payload: AttachmentDeleteRequest, principal: CurrentPrincipal, db: DbSession
) -> None:
    attachment_service.delete_attachment(db, principal.user, case_id, attachment_id, payload.reason)


@shared.get("/{case_id}/history", response_model=CaseHistory, responses=NOT_FOUND)
def history(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    """Status transitions and assignment history, oldest first."""
    return workflow_service.get_history(db, principal.user, case_id)


@shared.get("/{case_id}/matches", response_model=list[MatchRead], responses=NOT_FOUND)
def list_matches(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return workflow_service.list_matches(db, principal.user, case_id)


@shared.post(
    "/{case_id}/notifications", response_model=NotificationRead, status_code=status.HTTP_202_ACCEPTED, responses=STATE,
    summary="Queue an SMS/WhatsApp message to a consenting family member or owner",
)
def queue_notification(
    case_id: uuid.UUID, payload: NotificationCreate, principal: CurrentPrincipal, db: DbSession, response: Response,
    idempotency_key: IdempotencyKey = None,
):
    """
    Messages contain only the case number and station name. The recipient is encrypted and shown masked.
    Delivery is asynchronous (notifications-worker); with no provider configured messages stay PENDING.
    """
    notification, replayed = notification_service.queue(db, principal.user, case_id, payload, idempotency_key)
    _mark_replay(response, replayed)
    return notification


@shared.get("/{case_id}/notifications", response_model=list[NotificationRead], responses=NOT_FOUND)
def list_notifications(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return notification_service.list_for_case(db, principal.user, case_id)


@shared.post(
    "/{case_id}/matches/generate", response_model=MatchGenerateResult, responses=STATE,
    summary="Run automatic matching for this case now",
)
def generate_matches(case_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    """
    Deterministic rule-based scoring (see docs/MATCHING.md). New suggestions are stored as PROPOSED,
    AUTOMATIC matches for police review; nothing is confirmed and no case status changes.
    """
    created = matching_service.generate_on_request(db, principal.user, case_id)
    matches = workflow_service.list_matches(db, principal.user, case_id)
    return MatchGenerateResult(created=len(created), suggestions=[m for m in matches if m.id in set(created)])


@shared.post(
    "/{case_id}/matches", response_model=MatchRead, status_code=status.HTTP_201_CREATED, responses=STATE,
    summary="Propose a potential match for human review",
)
def propose_match(case_id: uuid.UUID, payload: MatchProposeRequest, principal: CurrentPrincipal, db: DbSession):
    return workflow_service.propose_match(db, principal.user, case_id, payload)


@shared.post(
    "/{case_id}/matches/{match_id}/review", response_model=MatchRead, responses=STATE,
    summary="Confirm or reject a proposed match (police and admins; not the proposer of a manual match)",
)
def review_match(
    case_id: uuid.UUID, match_id: uuid.UUID, payload: MatchReviewRequest, principal: CurrentPrincipal, db: DbSession,
    response: Response, idempotency_key: IdempotencyKey = None,
):
    """
    Confirming moves both cases to MATCH_FOUND (from OPEN, UNDER_REVIEW, ASSIGNED, or REOPENED) in one transaction.
    It does not resolve or reunite: resolution is a separate status change with its own verification.
    Send left_case_version/right_case_version from when review began to get 409 case_changed on stale data.
    """
    match, replayed = workflow_service.review_match(
        db, principal.user, case_id, match_id, payload, idempotency_key=idempotency_key
    )
    _mark_replay(response, replayed)
    return match


@shared.post(
    "/{case_id}/matches/{match_id}/revoke", response_model=MatchRead, responses=STATE,
    summary="Revoke a confirmed match that proved wrong (police and admins)",
)
def revoke_match(
    case_id: uuid.UUID, match_id: uuid.UUID, payload: MatchRevokeRequest, principal: CurrentPrincipal, db: DbSession
):
    """Cases in MATCH_FOUND return to ASSIGNED (or UNDER_REVIEW if unassigned). Reopen resolved cases first."""
    return workflow_service.revoke_match(db, principal.user, case_id, match_id, payload)
