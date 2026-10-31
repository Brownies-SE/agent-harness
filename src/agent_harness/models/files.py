import base64
import binascii
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .base import StrictModel, WireUUID

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_BASE64_CHARS = 4 * ((MAX_FILE_BYTES + 2) // 3)
MIME_PATTERN = re.compile(r"[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+\-]+", re.ASCII)


class FileUpload(StrictModel):
    filename: str = Field(min_length=1, max_length=255, examples=["note.txt"])
    content_base64: str = Field(
        max_length=MAX_BASE64_CHARS,
        examples=["aGVsbG8="],
        description="Base64 file bytes, up to 5 MiB decoded.",
    )
    mime_type: str | None = Field(default=None, max_length=200)
    conversation_id: WireUUID | None = None

    @field_validator("filename")
    @classmethod
    def safe_name(cls, value):
        if (
            not value.strip()
            or value in (".", "..")
            or "/" in value
            or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid filename")
        return value

    @field_validator("content_base64")
    @classmethod
    def bounded_content(cls, value):
        try:
            content = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid base64 content") from exc
        if len(content) > MAX_FILE_BYTES or base64.b64encode(content).decode("ascii") != value:
            raise ValueError("invalid file content")
        return value

    @field_validator("mime_type")
    @classmethod
    def safe_mime(cls, value):
        if value is not None and not MIME_PATTERN.fullmatch(value):
            raise ValueError("invalid MIME type")
        return value


class StoredFile(StrictModel):
    id: UUID
    conversation_id: UUID | None
    filename: str
    mime_type: str | None
    size_bytes: int = Field(ge=0, le=MAX_FILE_BYTES)
    origin: Literal["upload", "generated"]
    created_at: datetime
