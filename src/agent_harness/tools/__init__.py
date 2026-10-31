"""One registry of built-in tools for the agent and its evaluations."""

from agent_harness.tools.charts import chart_tool
from agent_harness.tools.clock import current_time_tool
from agent_harness.tools.filesystem import filesystem_tools
from agent_harness.tools.network import http_tool
from agent_harness.tools.weather import weather_tool


def builtin_tools(policy):
    return [
        *filesystem_tools(policy),
        current_time_tool(),
        http_tool(policy),
        chart_tool(),
        weather_tool(policy),
    ]
