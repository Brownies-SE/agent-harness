"""Local clock, explicit time-zone conversion and elapsed-time calculations."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agent_harness.core.executor import Tool
from agent_harness.models.date_time import DateTimeArguments, DateTimeResult


def current_time_tool():
    def get_current_time(operation="now", timezone_name="", timestamp="", other_timestamp=""):
        args = DateTimeArguments(
            operation=operation,
            timezone_name=timezone_name,
            timestamp=timestamp,
            other_timestamp=other_timestamp,
        )
        instant = (
            datetime.now(timezone.utc)
            if operation == "now"
            else datetime.fromisoformat(args.timestamp).astimezone(timezone.utc)
        )
        local = (
            instant.astimezone(ZoneInfo(timezone_name)) if timezone_name else instant.astimezone()
        )
        difference = (
            (
                datetime.fromisoformat(other_timestamp).astimezone(timezone.utc) - instant
            ).total_seconds()
            if operation == "difference"
            else 0.0
        )
        return {
            "utc": instant.isoformat(),
            "local": local.isoformat(),
            "timezone_name": timezone_name or str(local.tzinfo),
            "weekday": local.strftime("%A"),
            "difference_seconds": difference,
            "operation": operation,
        }

    return Tool(
        "get_current_time",
        {
            **DateTimeArguments.model_json_schema(),
            "description": (
                "Get current date/time, convert an offset-aware ISO8601 timestamp to "
                "an IANA zone, or compute elapsed seconds (other_timestamp minus timestamp). "
                "Default now uses host local time. Difference is elapsed time, not calendar days."
            ),
        },
        (),
        get_current_time,
        input_model=DateTimeArguments,
        output_model=DateTimeResult,
    )
