"""Local system clock, with no filesystem/network/subprocess capability."""

from datetime import datetime, timezone

from agent_harness.core.executor import Tool
from agent_harness.models.tools import ClockResult, EmptyArguments


def current_time_tool():
    def get_current_time():
        now = datetime.now(timezone.utc)
        return {"utc": now.isoformat(), "local": now.astimezone().isoformat()}

    return Tool(
        "get_current_time",
        {
            **EmptyArguments.model_json_schema(),
            "description": "Get current system time in UTC and local ISO8601 timestamps.",
        },
        (),
        get_current_time,
        input_model=EmptyArguments,
        output_model=ClockResult,
    )
