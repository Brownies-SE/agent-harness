from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from .base import StrictModel, WireUUID

Text = Annotated[str, Field(min_length=1, max_length=8000)]


def valid_text(value):
    if not value.strip() or "\x00" in value:
        raise ValueError("invalid text")
    return value


class EmbeddingRequest(StrictModel):
    texts: list[Text] = Field(min_length=1, max_length=16)
    query: bool = False

    @field_validator("texts")
    @classmethod
    def nonblank(cls, values):
        for value in values:
            valid_text(value)
        if sum(len(value) for value in values) > 32000:
            raise ValueError("too much text")
        return values


class EmbeddingResponse(StrictModel):
    model: str
    dimensions: Literal[1024] = 1024
    embeddings: list[list[float]]


class MemoryCreate(StrictModel):
    content: Text
    source: Literal["stated", "inferred"] = "stated"
    source_conversation_id: WireUUID | None = None

    _nonblank = field_validator("content")(valid_text)


class MemoryUpdate(StrictModel):
    content: Text

    _nonblank = field_validator("content")(valid_text)


class Memory(StrictModel):
    id: UUID
    content: str
    source: Literal["stated", "inferred"]
    source_conversation_id: UUID | None
    created_at: datetime
    updated_at: datetime


class MemorySearch(StrictModel):
    query: Text
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=-1.0, ge=-1.0, le=1.0, allow_inf_nan=False)

    _nonblank = field_validator("query")(valid_text)


class MemoryHit(StrictModel):
    memory: Memory
    score: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
