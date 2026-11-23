"""Realtime staff events: a cursor poll endpoint and a Server-Sent Events stream."""

import json
import time
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.v1.responses import AUTHED
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.auth.permissions import Permission, require_permission
from app.common.errors import AppError
from app.config import get_settings
from app.database import SessionLocal
from app.services import realtime

router = APIRouter(prefix="/events", tags=["Realtime events"])


class EventBatch(BaseModel):
    events: list[dict]
    next_cursor: str


@router.get("", response_model=EventBatch, responses=AUTHED,
            summary="Events after a cursor (omit the cursor to start from now). Use when SSE is unavailable.")
def poll(
    principal: CurrentPrincipal,
    db: DbSession,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=realtime.MAX_BATCH)] = 100,
) -> EventBatch:
    events, next_cursor = realtime.fetch(
        db, principal.user, cursor or realtime.initial_cursor(), lag_seconds=get_settings().realtime_lag_seconds,
        limit=limit,
    )
    return EventBatch(events=events, next_cursor=next_cursor)


def _sse(event: str, data: dict, event_id: str | None = None) -> str:
    lines = [f"id: {event_id}"] if event_id else []
    lines += [f"event: {event}", f"data: {json.dumps(data, default=str)}"]
    return "\n".join(lines) + "\n\n"


@router.get("/stream", responses=AUTHED,
            summary="Server-Sent Events. Reconnect with Last-Event-ID (or ?cursor=) to resume without gaps.")
def stream(
    principal: CurrentPrincipal,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=200)] = None,
) -> StreamingResponse:
    require_permission(principal.user, Permission.CASE_READ)
    settings = get_settings()
    start = last_event_id or cursor or realtime.initial_cursor()
    realtime.decode_cursor(start)
    user_id, session_id, token_version = principal.user.id, principal.session_id, principal.user.token_version

    def generate() -> Iterator[str]:
        position = start
        deadline = time.monotonic() + settings.realtime_stream_max_seconds
        yield f"retry: {int(settings.realtime_poll_interval_seconds * 1000)}\n\n"
        while True:
            try:
                with SessionLocal() as db:
                    user = realtime.revalidate(db, user_id, session_id, token_version)
                    events, position = realtime.fetch(
                        db, user, position, lag_seconds=settings.realtime_lag_seconds
                    )
            except AppError as exc:
                yield _sse("error", {"code": exc.code})
                return
            for event in events:
                yield _sse(event["type"], event, event["id"])
            if time.monotonic() >= deadline:
                yield _sse("reconnect", {"cursor": position})
                return
            if not events:
                yield ": keep-alive\n\n"
                time.sleep(settings.realtime_poll_interval_seconds)

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
