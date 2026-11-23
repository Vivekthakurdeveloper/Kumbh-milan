"""Citizen chatbot and the staff handoff queue."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.api.v1.responses import AUTHED, errors
from app.auth.dependencies import CurrentPrincipal, DbSession
from app.models.enums import ChatSessionStatus
from app.schemas.chat import (
    ChatMessageCreate,
    ChatMessageRead,
    ChatReply,
    ChatSessionCreate,
    ChatSessionCreated,
    ChatSessionRead,
    ChatTranscript,
    StaffChatReply,
)
from app.schemas.common import Page, PageParams
from app.services import chatbot

public_router = APIRouter(prefix="/public/chat", tags=["Public chatbot"])
router = APIRouter(prefix="/chat", tags=["Chat handoff"])
SessionSecret = Annotated[str | None, Header(alias="X-Chat-Session-Secret", max_length=100)]
StatusToken = Annotated[
    str | None,
    Header(alias="X-Case-Status-Token", description="Token from /public/status/verify-otp; enables status answers"),
]
PUBLIC = errors(404, 410, 422, 429)


def _transcript(session, rows) -> ChatTranscript:
    return ChatTranscript(session_id=session.id, status=session.status, locale=session.locale,
                          channel=session.channel, messages=[ChatMessageRead.model_validate(r) for r in rows])


@public_router.post("/sessions", response_model=ChatSessionCreated, status_code=status.HTTP_201_CREATED,
                    responses=errors(422, 429), summary="Start a chat (no account needed)")
def create_session(payload: ChatSessionCreate, db: DbSession) -> ChatSessionCreated:
    session, secret, greeting = chatbot.create_session(db, payload.channel, payload.locale)
    return ChatSessionCreated(session_id=session.id, session_secret=secret, status=session.status, reply=greeting)


@public_router.post("/sessions/{session_id}/messages", response_model=ChatReply, responses=PUBLIC)
def send_message(session_id: uuid.UUID, payload: ChatMessageCreate, db: DbSession, secret: SessionSecret = None,
                 status_token: StatusToken = None) -> ChatReply:
    reply = chatbot.post_message(db, session_id, secret, payload.text, status_token)
    return ChatReply(intent=reply.intent, reply=reply.text, handoff=reply.handoff, status=reply.session.status)


@public_router.get("/sessions/{session_id}/messages", response_model=ChatTranscript, responses=PUBLIC,
                   summary="Transcript, including staff replies after a handoff")
def citizen_messages(session_id: uuid.UUID, db: DbSession, secret: SessionSecret = None):
    return _transcript(*chatbot.citizen_messages(db, session_id, secret))


@public_router.post("/sessions/{session_id}/close", status_code=status.HTTP_204_NO_CONTENT, responses=PUBLIC)
def citizen_close(session_id: uuid.UUID, db: DbSession, secret: SessionSecret = None) -> None:
    chatbot.citizen_close(db, session_id, secret)


@router.get("/sessions", response_model=Page[ChatSessionRead], responses=AUTHED,
            summary="Chats waiting for staff (default HANDED_OFF)")
def queue(principal: CurrentPrincipal, db: DbSession, page: Annotated[PageParams, Depends()],
          status_: Annotated[ChatSessionStatus | None, Query(alias="status")] = None):
    rows, total = chatbot.staff_queue(db, principal.user, status_, limit=page.limit, offset=page.offset)
    return Page(items=[ChatSessionRead.model_validate(r) for r in rows], total=total, limit=page.limit,
                offset=page.offset)


@router.get("/sessions/{session_id}", response_model=ChatTranscript, responses={**AUTHED, **errors(404)})
def transcript(session_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return _transcript(*chatbot.staff_transcript(db, principal.user, session_id))


@router.post("/sessions/{session_id}/reply", response_model=ChatMessageRead, responses={**AUTHED, **errors(404, 409)})
def reply(session_id: uuid.UUID, payload: StaffChatReply, principal: CurrentPrincipal, db: DbSession):
    return chatbot.staff_reply(db, principal.user, session_id, payload.text)


@router.post("/sessions/{session_id}/close", response_model=ChatSessionRead, responses={**AUTHED, **errors(404)})
def close(session_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return chatbot.staff_close(db, principal.user, session_id)
