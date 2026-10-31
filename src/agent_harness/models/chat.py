from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .base import StrictModel, WireUUID
from .charts import ChartBlock


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


class ConversationSearch(StrictModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=2147483647)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        if "\x00" in value:
            raise ValueError("invalid search")
        return value.strip()


class ConversationSearchHit(StrictModel):
    conversation: Conversation
    snippet: str = Field(max_length=400)
    matched_message_id: UUID | None
    matched_seq: int | None = Field(ge=1)


class TextBlock(StrictModel):
    type: Literal["text"] = "text"
    text: str


def validate_blocks(content):
    charts = sum(isinstance(block, ChartBlock) for block in content)
    if charts > 4 or len(content) - charts > 5:
        raise ValueError("too many response blocks")
    return content


def text_content(content):
    """Use every text block; chart placement must not hide later text from history/search."""
    return "\n\n".join(
        block.text if isinstance(block, TextBlock) else block["text"]
        for block in content
        if isinstance(block, TextBlock) or (isinstance(block, dict) and block.get("type") == "text")
    )


class ReplyVariant(StrictModel):
    id: UUID
    ordinal: int = Field(ge=0)
    content: list[TextBlock | ChartBlock] = Field(min_length=1, max_length=9)
    rating: Literal["up", "down"] | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    created_at: datetime

    @field_validator("content")
    @classmethod
    def valid_blocks(cls, content):
        return validate_blocks(content)


class VariantSelection(StrictModel):
    variant_id: WireUUID


class ReplyRating(VariantSelection):
    rating: Literal["up", "down"] | None


class Message(StrictModel):
    id: UUID
    conversation_id: UUID
    seq: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: list[TextBlock | ChartBlock] = Field(min_length=1, max_length=9)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    created_at: datetime
    variants: list[ReplyVariant] = Field(default_factory=list)
    selected_variant: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_blocks(self):
        validate_blocks(self.content)
        if self.role == "user" and (
            len(self.content) != 1 or not isinstance(self.content[0], TextBlock)
        ):
            raise ValueError("user messages must contain text only")
        return self


class Exchange(StrictModel):
    user: Message
    assistant: Message
    run_id: str


class Health(StrictModel):
    status: Literal["ok"] = "ok"
    model: str


class ErrorResponse(StrictModel):
    detail: str


class CurrentUser(StrictModel):
    id: UUID
    email: str
    created_at: datetime


class StreamDelta(StrictModel):
    type: Literal["delta"] = "delta"
    text: str = Field(min_length=1, max_length=65536)


class StreamChart(StrictModel):
    type: Literal["chart"] = "chart"
    chart: ChartBlock


class StreamComplete(StrictModel):
    type: Literal["complete"] = "complete"
    exchange: Exchange


class StreamError(StrictModel):
    type: Literal["error"] = "error"
    detail: str = Field(max_length=100)


StreamEvent = StreamDelta | StreamChart | StreamComplete | StreamError
