from typing import Any, Literal

from pydantic import ConfigDict, Field, RootModel

from .base import StrictModel


class ToolRequest(StrictModel):
    tool: str
    arguments: dict[str, Any]


class FinalAnswer(StrictModel):
    final: str


class EmptyArguments(StrictModel):
    pass


class PathArguments(StrictModel):
    path: str = Field(min_length=1, max_length=4096)


class WriteFileArguments(PathArguments):
    content: str


class HttpArguments(StrictModel):
    url: str


class TextResult(RootModel[str]):
    model_config = ConfigDict(strict=True, frozen=True)


class WriteFileResult(StrictModel):
    written: int = Field(ge=0)


class DirectoryEntry(StrictModel):
    name: str
    type: Literal["file", "directory"]


class DirectoryResult(StrictModel):
    entries: list[DirectoryEntry] = Field(max_length=200)
    truncated: bool


class ClockResult(StrictModel):
    utc: str
    local: str
