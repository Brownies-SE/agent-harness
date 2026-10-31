from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .base import StrictModel


class ConversationCreate(StrictModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid text")
        return value


class ConversationRename(ConversationCreate):
    title: str = Field(min_length=1, max_length=200)


class SendMessage(StrictModel):
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid text")
        return value


class Conversation(StrictModel):
    id: UUID
    title: str
    summary: str | None = None
    compacted_through_message_id: UUID | None = None
    memory_processed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TextBlock(StrictModel):
    type: Literal["text"] = "text"
    text: str


class Message(StrictModel):
    id: UUID
    conversation_id: UUID
    seq: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: list[TextBlock] = Field(min_length=1, max_length=1)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    created_at: datetime


class Exchange(StrictModel):
    user: Message
    assistant: Message
    run_id: str


class Health(StrictModel):
    status: Literal["ok"] = "ok"
    model: str


class ErrorResponse(StrictModel):
    detail: str
