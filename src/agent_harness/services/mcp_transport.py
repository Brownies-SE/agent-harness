"""Bounded MCP streamable HTTP over the host's checked, pinned transport."""

import json
import re
from contextlib import nullcontext

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import json_copy
from agent_harness.core.schema import validate_schema
from agent_harness.tools.network import transport


class MCPHTTPClient:
    def __init__(self, target, token=None, *, operation_guard=None):
        self.target = target
        self.operation_guard = operation_guard or nullcontext
        self.headers = {"Accept": "application/json, text/event-stream"}
        if token:
            self.headers["Authorization"] = "Bearer " + token
        self.sequence = 0
        self.discovered = []
        result = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "agent-harness", "version": "0.1.0"},
            },
        )
        version = result.get("protocolVersion")
        if version not in {"2024-11-05", "2025-03-26", "2025-06-18"}:
            raise SecurityError("mcp_protocol_unsupported")
        self.headers["MCP-Protocol-Version"] = version
        self.rpc("notifications/initialized", notification=True)

    def rpc(self, method, params=None, *, notification=False):
        self.sequence += 1
        request = {"jsonrpc": "2.0", "method": method}
        if not notification:
            request["id"] = self.sequence
        if params is not None:
            request["params"] = params
        response_headers = {}

        def response_from(data):
            text = data.decode("utf-8")
            content_type = response_headers.get("content-type", "").split(";")[0].lower()
            if content_type == "text/event-stream":
                messages = []
                # A final partial block is not an SSE event until its empty line arrives.
                for block in text.replace("\r\n", "\n").split("\n\n")[:-1]:
                    lines = [
                        line[5:].lstrip(" ")
                        for line in block.splitlines()
                        if line.startswith("data:")
                    ]
                    if lines:
                        messages.append(json.loads("\n".join(lines)))
                matches = [
                    item
                    for item in messages
                    if isinstance(item, dict)
                    and type(item.get("id")) is int
                    and item["id"] == self.sequence
                ]
                if len(matches) != 1:
                    raise ValueError
                return matches[0]
            return json.loads(text)

        def complete(data):
            if notification:
                return True
            try:
                response = response_from(data)
                return (
                    isinstance(response, dict)
                    and type(response.get("id")) is int
                    and response["id"] == self.sequence
                )
            except (ValueError, UnicodeError, RecursionError):
                return False

        data = transport(
            self.target,
            method="POST",
            body=json.dumps(request).encode(),
            headers=self.headers,
            response_headers=response_headers,
            limit=256 * 1024,
            timeout=10,
            stop_when=complete,
        )
        session = response_headers.get("mcp-session-id")
        if session is not None:
            if (
                not session
                or len(session) > 512
                or any(ord(c) < 33 or ord(c) > 126 for c in session)
            ):
                raise SecurityError("invalid_mcp_session")
            if method == "initialize":
                self.headers["MCP-Session-Id"] = session
            elif session != self.headers.get("MCP-Session-Id"):
                raise SecurityError("invalid_mcp_session")
        if notification:
            return None
        try:
            response = response_from(data)
            if (
                not isinstance(response, dict)
                or response.get("jsonrpc") != "2.0"
                or type(response.get("id")) is not int
                or response["id"] != self.sequence
                or "error" in response
                or not isinstance(response.get("result"), dict)
            ):
                raise ValueError
            return json_copy(response["result"], 256 * 1024)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SecurityError("invalid_mcp_response") from exc

    def list_tools(self):
        result = self.rpc("tools/list")
        tools = result.get("tools")
        if not isinstance(tools, list) or len(tools) > 256 or result.get("nextCursor"):
            raise SecurityError("mcp_discovery_limit")
        seen = set()
        for metadata in tools:
            if not isinstance(metadata, dict):
                raise SecurityError("invalid_mcp_discovery")
            name = metadata.get("name")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,50}", name)
                or name in seen
            ):
                raise SecurityError("invalid_mcp_tool_name")
            seen.add(name)
            description = metadata.get("description")
            if description is not None and (
                not isinstance(description, str) or len(description) > 8192
            ):
                raise SecurityError("invalid_mcp_description")
            validate_schema(metadata.get("inputSchema"))
        self.discovered = tools
        return tools

    def call_tool(self, name, arguments):
        with self.operation_guard():
            return self.rpc("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        # Each bounded HTTP request closes its socket; no background session survives.
        pass
