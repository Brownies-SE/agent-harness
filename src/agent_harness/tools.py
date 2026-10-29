"""One registry of built-in tools for the agent and its evaluations."""

from .clock import current_time_tool
from .filesystem import filesystem_tools
from .network import http_tool


def builtin_tools(policy):
    return [*filesystem_tools(policy), current_time_tool(), http_tool(policy)]
