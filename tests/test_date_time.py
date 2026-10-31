from datetime import datetime

import pytest

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy
from agent_harness.tools.clock import current_time_tool


def clock(**arguments):
    executor = Executor(Policy())
    executor.register(current_time_tool())
    return executor.execute({"tool": "get_current_time", "arguments": arguments})


def test_converts_explicit_instant_across_dst():
    result = clock(
        operation="convert", timezone_name="America/Denver", timestamp="2026-11-01T08:30:00+00:00"
    )
    assert result["local"] == "2026-11-01T01:30:00-07:00"
    assert result["weekday"] == "Sunday"
    assert datetime.fromisoformat(result["utc"]) == datetime.fromisoformat(result["local"])


def test_elapsed_time_uses_instants_not_wall_clock():
    result = clock(
        operation="difference",
        timestamp="2026-11-01T01:30:00-06:00",
        other_timestamp="2026-11-01T01:30:00-07:00",
    )
    assert result["difference_seconds"] == 3600


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "convert", "timestamp": "2026-11-01T01:30:00"},
        {"timezone_name": "../../etc/passwd"},
        {"timezone_name": "Does/NotExist"},
        {"operation": "difference", "timestamp": "2026-01-01T00:00:00Z"},
        {"operation": "now", "timestamp": "2026-01-01T00:00:00Z"},
    ],
)
def test_invalid_or_ambiguous_date_input(arguments):
    with pytest.raises(SecurityError):
        clock(**arguments)
