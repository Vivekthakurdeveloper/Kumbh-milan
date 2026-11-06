"""Offline synchronization endpoints for mobile clients."""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.config import get_settings
from app.schemas.sync import SyncPushRequest, SyncPushResponse, SyncPullResponse, SyncStatusResponse
from app.services import sync as sync_service

router = APIRouter(prefix="/sync", tags=["Mobile sync"])

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
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    client_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> SyncPullResponse:
    """
    Keyset pagination on (sync_changed_at, id). Results are limited to cases the caller may see.
    A changed server_updated_at means notes or attachments may have changed; fetch those with the
    existing case endpoints. Recent writes may be withheld for SYNC_PULL_LAG_SECONDS.
    """
    return sync_service.pull(
        db,
        principal.user,
        device_id=device_id,
        cursor=cursor,
        limit=min(limit, get_settings().sync_max_batch),
        client_id=client_id,
    )


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
