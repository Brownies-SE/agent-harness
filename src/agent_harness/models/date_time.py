"""Date/time operations require offset-aware timestamps for unambiguous instants."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .base import StrictModel


class DateTimeArguments(StrictModel):
    operation: Literal["now", "convert", "difference"] = "now"
    timezone_name: str = Field(
        default="",
        max_length=100,
        description="IANA zone such as America/Denver; empty uses host local time.",
    )
    timestamp: str = Field(
        default="",
        max_length=40,
        description="Offset-aware ISO8601 timestamp; required for convert/difference.",
    )
    other_timestamp: str = Field(
        default="",
        max_length=40,
        description="Second offset-aware ISO8601 timestamp for difference: second minus first.",
    )

    @field_validator("timezone_name")
    @classmethod
    def valid_zone(cls, value):
        if value:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise ValueError("unknown timezone") from exc
        return value

    @model_validator(mode="after")
    def operation_fields(self):
        from datetime import datetime

        expected = 0 if self.operation == "now" else 2 if self.operation == "difference" else 1
        if bool(self.timestamp) != (expected >= 1) or bool(self.other_timestamp) != (expected == 2):
            raise ValueError("timestamp fields do not match operation")
        for value in (self.timestamp, self.other_timestamp):
            if value and datetime.fromisoformat(value).utcoffset() is None:
                raise ValueError("timestamp must have an explicit UTC offset")
        return self


class DateTimeResult(StrictModel):
    utc: str = Field(min_length=20, max_length=40)
    local: str = Field(min_length=20, max_length=40)
    timezone_name: str = Field(min_length=1, max_length=100)
    weekday: str = Field(min_length=6, max_length=9)
    difference_seconds: float = Field(allow_inf_nan=False)
    operation: Literal["now", "convert", "difference"]
