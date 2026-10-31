"""Closed arithmetic-only calculator contracts."""

from pydantic import Field

from .base import StrictModel


class CalculatorArguments(StrictModel):
    expression: str = Field(min_length=1, max_length=512)


class CalculatorResult(StrictModel):
    expression: str = Field(min_length=1, max_length=512)
    result: str = Field(min_length=1, max_length=200)
    precision: int = Field(default=28, ge=28, le=28)
