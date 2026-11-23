import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ChatChannel, ChatRole, ChatSessionStatus
from app.schemas.common import ReadModel, StrictModel


class ChatSessionCreate(StrictModel):
    channel: ChatChannel = ChatChannel.WEB
    locale: str = Field("hi", min_length=2, max_length=8)


class ChatSessionCreated(BaseModel):
    session_id: uuid.UUID
    session_secret: str = Field(description="Send as X-Chat-Session-Secret on every later call; shown once")
    status: ChatSessionStatus
    reply: str


class ChatMessageCreate(StrictModel):
    text: str = Field(min_length=1, max_length=1000)


class ChatReply(BaseModel):
    intent: str
    reply: str
    handoff: bool
    status: ChatSessionStatus


class ChatMessageRead(ReadModel):
    id: uuid.UUID
    role: ChatRole
    intent: str | None
    text_redacted: str
    created_at: datetime


class ChatTranscript(BaseModel):
    session_id: uuid.UUID
    status: ChatSessionStatus
    locale: str
    channel: ChatChannel
    messages: list[ChatMessageRead]


class ChatSessionRead(ReadModel):
    id: uuid.UUID
    channel: ChatChannel
    locale: str
    status: ChatSessionStatus
    handoff_reason: str | None
    handed_off_at: datetime | None
    claimed_by_user_id: uuid.UUID | None
    message_count: int
    last_activity_at: datetime
    created_at: datetime


class StaffChatReply(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
