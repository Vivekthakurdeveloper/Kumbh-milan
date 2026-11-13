"""Cross-case match review queue and face-candidate review endpoints."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.models.enums import ConfidenceBand, MatchDomain, MatchSource, MatchStatus
from app.schemas.common import Page, PageParams
from app.schemas.faces import MatchDetail, MatchHistoryEvent, MatchPhotoUrl
from app.schemas.workflow import MatchQueueItem
from app.services import workflow as workflow_service
from app.storage.media import MediaLinks, get_media_links

router = APIRouter(prefix="/matches", tags=["Matches"])
NOT_FOUND = {**AUTHED, **errors(404)}
MediaDep = Annotated[MediaLinks, Depends(get_media_links)]


@router.get(
    "",
    response_model=Page[MatchQueueItem],
    responses=AUTHED,
    summary="Match review queue across cases visible to the caller",
)
def list_match_queue(
    principal: CurrentPrincipal,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    status: MatchStatus | None = MatchStatus.PROPOSED,
    confidence_band: ConfidenceBand | None = None,
    match_domain: MatchDomain | None = None,
    source: MatchSource | None = None,
) -> Page[MatchQueueItem]:
    """
    Returns matches that touch at least one in-scope case. Defaults to PROPOSED so police can
    work the review queue without opening each case. Confirm or reject via
    POST /cases/{id}/matches/{mid}/review. Filter source=FACE for AI candidates.
    """
    items, total = workflow_service.list_match_queue(
        db,
        principal.user,
        status=status,
        confidence_band=confidence_band,
        match_domain=match_domain,
        source=source,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{match_id}",
    response_model=MatchDetail,
    responses=NOT_FOUND,
    summary="Side-by-side match review detail",
)
def get_match(match_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> MatchDetail:
    return workflow_service.get_match_detail(db, principal.user, match_id)


@router.get(
    "/{match_id}/photos/{side}",
    response_model=MatchPhotoUrl,
    responses=NOT_FOUND,
    summary="Short-lived, browser-reachable URL for a candidate photograph on a match",
)
def get_match_photo(
    match_id: uuid.UUID,
    side: Literal["left", "right"],
    principal: CurrentPrincipal,
    db: DbSession,
    media: MediaDep,
) -> MatchPhotoUrl:
    """
    The `url` is built for the browser: on the object store's public host when S3_PUBLIC_ENDPOINT_URL is
    configured, otherwise on this API (`/api/v1/media/{token}`). It is never an internal hostname. Use it
    directly as `<img src>`; it expires after `expires_in` seconds, so fetch a new one rather than caching it.
    The response carries no similarity score.
    """
    return workflow_service.match_photo_url(db, media, principal.user, match_id, side)


@router.get(
    "/{match_id}/history",
    response_model=list[MatchHistoryEvent],
    responses=NOT_FOUND,
    summary="Audit history for a match (suggestion, approvals, rejection)",
)
def get_match_history(
    match_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> list[MatchHistoryEvent]:
    return workflow_service.match_history(db, principal.user, match_id)
