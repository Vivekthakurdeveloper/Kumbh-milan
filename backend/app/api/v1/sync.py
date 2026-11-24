"""Offline synchronization endpoints for mobile clients."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile
from fastapi import status as http_status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.config import get_settings
from app.schemas.sync import (
    AttachmentUploadFinalizeResponse,
    AttachmentUploadSessionCreate,
    AttachmentUploadSessionRead,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncStatusResponse,
)
from app.services import attachment_sessions as attachment_session_service
from app.services import sync as sync_service
from app.storage import ObjectStorage, get_storage
from app.storage.scanner import MalwareScanner, get_scanner

router = APIRouter(prefix="/sync", tags=["Mobile sync"])
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
ScannerDep = Annotated[MalwareScanner, Depends(get_scanner)]

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", description="Optional batch key; per-record client_record_id is the durable guard"),
]


@router.post(
    "/push",
    response_model=SyncPushResponse,
    responses={**AUTHED, **errors(409)},
    summary="Push offline-created or offline-updated records",
)
def push(
    payload: SyncPushRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> SyncPushResponse:
    """
    Process up to SYNC_MAX_BATCH records independently. Each record returns SYNCED, DUPLICATE,
    CONFLICT, or FAILED. HTTP 200 is returned even when some records fail so the client can
    reconcile per record. Station scope and RBAC match the normal case APIs.
    """
    del response  # Reserved for future Idempotent-Replayed marking of whole-batch keys.
    return sync_service.push(db, principal.user, payload, idempotency_key=idempotency_key)


@router.get(
    "/pull",
    response_model=SyncPullResponse,
    responses=AUTHED,
    summary="Download cases updated after a sync cursor",
)
def pull(
    principal: CurrentPrincipal,
    db: DbSession,
    device_id: Annotated[str, Query(min_length=1, max_length=128)],
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    tombstone_cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    client_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> SyncPullResponse:
    """
    Case keyset pagination uses (sync_changed_at, id). Tombstones have an independent
    (occurred_at, id) cursor and contain only ids/reasons for cases previously delivered to this user.
    Results are limited to cases the caller may see.
    A changed server_updated_at means notes or attachments may have changed; fetch those with the
    existing case endpoints. Recent writes may be withheld for SYNC_PULL_LAG_SECONDS.
    """
    return sync_service.pull(
        db,
        principal.user,
        device_id=device_id,
        cursor=cursor,
        tombstone_cursor=tombstone_cursor,
        limit=min(limit, get_settings().sync_max_batch),
        client_id=client_id,
    )


@router.post(
    "/attachments/sessions",
    response_model=AttachmentUploadSessionRead,
    status_code=http_status.HTTP_201_CREATED,
    responses={**AUTHED, **errors(409, 413, 422)},
    summary="Create or resume an offline attachment upload manifest",
)
def create_attachment_session(
    payload: AttachmentUploadSessionCreate,
    principal: CurrentPrincipal,
    db: DbSession,
    response: Response,
):
    row, replayed = attachment_session_service.create_session(db, principal.user, payload)
    if replayed:
        response.status_code = http_status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return attachment_session_service.to_read(row)


@router.put(
    "/attachments/sessions/{session_id}/content",
    response_model=AttachmentUploadSessionRead,
    responses={**AUTHED, **errors(404, 409, 413, 422, 503)},
    summary="Append bytes at the exact server-reported upload offset",
)
def upload_attachment_chunk(
    session_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
    offset: Annotated[int, Query(ge=0)],
    file: Annotated[UploadFile, File()],
):
    row = attachment_session_service.upload_chunk(
        db,
        storage,
        principal.user,
        session_id,
        offset=offset,
        stream=file.file,
    )
    return attachment_session_service.to_read(row)


@router.post(
    "/attachments/sessions/{session_id}/finalize",
    response_model=AttachmentUploadFinalizeResponse,
    responses={**AUTHED, **errors(404, 409, 413, 415, 422, 503)},
    summary="Verify checksum and finalize through quarantine and malware scanning",
)
def finalize_attachment_session(
    session_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
    scanner: ScannerDep,
):
    return attachment_session_service.finalize_session(
        db,
        storage,
        scanner,
        principal.user,
        session_id,
    )


@router.delete(
    "/attachments/sessions/{session_id}",
    response_model=AttachmentUploadSessionRead,
    responses={**AUTHED, **errors(404, 409)},
    summary="Abort an incomplete offline attachment upload",
)
def abort_attachment_session(
    session_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
):
    row = attachment_session_service.abort_session(
        db,
        storage,
        principal.user,
        session_id,
    )
    return attachment_session_service.to_read(row)


@router.get(
    "/status",
    response_model=SyncStatusResponse,
    responses=AUTHED,
    summary="Report last sync times and the current server cursor",
)
def status(
    principal: CurrentPrincipal,
    db: DbSession,
    device_id: Annotated[str, Query(min_length=1, max_length=128)],
    pending_count: Annotated[int | None, Query(ge=0)] = None,
    failed_count: Annotated[int | None, Query(ge=0)] = None,
    client_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> SyncStatusResponse:
    """pending_count and failed_count are stored as reported by the client; the server never invents them."""
    return sync_service.status(
        db,
        principal.user,
        device_id=device_id,
        pending_count=pending_count,
        failed_count=failed_count,
        client_id=client_id,
    )
