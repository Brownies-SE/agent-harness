"""Local system clock, with no filesystem/network/subprocess capability."""

from datetime import datetime, timezone

from .executor import Tool


def current_time_tool():
    def get_current_time():
        now = datetime.now(timezone.utc)
        return {"utc": now.isoformat(), "local": now.astimezone().isoformat()}

    return Tool(
        "get_current_time",
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
            "description": "Get current system time in UTC and local ISO8601 timestamps.",
        },
        (),
        get_current_time,
    )
