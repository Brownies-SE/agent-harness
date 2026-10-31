"""Bounded web search data; returned text never carries authority."""

from typing import Literal

from pydantic import Field, field_validator

from .base import StrictModel


class SearchArguments(StrictModel):
    query: str = Field(min_length=1, max_length=400)
    limit: int = Field(default=5, ge=1, le=10)
    time_range: Literal["", "day", "month", "year"] = ""

    @field_validator("query")
    @classmethod
    def query_text(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid query")
        return value


class SearchHit(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    snippet: str = Field(max_length=1200)


class SearchResult(StrictModel):
    query: str = Field(min_length=1, max_length=400)
    results: list[SearchHit] = Field(max_length=10)
    source: Literal["SearXNG"] = "SearXNG"
    fetched_at: str = Field(min_length=20, max_length=40)
    partial: bool
