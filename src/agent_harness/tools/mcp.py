from dataclasses import dataclass, field
from typing import Mapping

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Tool, json_copy


@dataclass(frozen=True)
class MCPPermission:
    capabilities: tuple[str, ...]
    resources: Mapping[str, str] = field(default_factory=dict)


def bind_handler(client, tool_name):
    def invoke(**arguments):
        return client.call_tool(tool_name, arguments)

    return invoke


class MCPAdapter:
    """Transport-neutral synchronous MCP client boundary, configured by trusted host code.

    factory returns a client implementing list_tools() and call_tool(name, arguments).
    For HTTP it receives a checked Target and MUST connect to its pinned address.
    For stdio it receives no arguments. It must bound startup, discovery and calls.
    No SDK/stdio process or network client is started by merely constructing this adapter.
    """

    def __init__(self, executor, server, factory, *, endpoint=None, connection_id=None):
        self.executor = executor
        self.server = server
        self.factory = factory
        self.endpoint = endpoint
        self.connection_id = connection_id
        self.client = None
        self._connected = False

    def connect(self, permissions: Mapping[str, MCPPermission]):
        if self._connected:
            raise ValueError("MCP adapter already connected")
        permissions = {
            k: MCPPermission(tuple(v.capabilities), dict(v.resources))
            for k, v in permissions.items()
        }
        if self.endpoint is None:
            schema = {"type": "object", "properties": {}, "additionalProperties": False}
            capabilities = ("subprocess",)
            resources = {}
            args = {}
        else:
            schema = {
                "type": "object",
                "properties": {"url": {"type": "string", "const": self.endpoint}},
                "required": ["url"],
                "additionalProperties": False,
            }
            capabilities = ("network",)
            resources = {"url": "url"}
            args = {"url": self.endpoint}

        def establish(**kwargs):
            from agent_harness.tools.network import check_url

            if self._connected:
                raise SecurityError("mcp_already_connected")
            if self.endpoint is None:
                client = self.factory()
            else:
                client = self.factory(check_url(self.executor.policy, kwargs["url"]))
            try:
                discovered = json_copy(client.list_tools())
                if not isinstance(discovered, list):
                    raise SecurityError("invalid_mcp_discovery")
                pending = []
                seen = set()
                for metadata in discovered:
                    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
                        raise SecurityError("invalid_mcp_discovery")
                    name = metadata["name"]
                    if name in seen:
                        raise SecurityError("duplicate_mcp_tool")
                    seen.add(name)
                    permission = permissions.get(name)
                    if permission is None:
                        continue

                    # Server annotations/descriptions can never supply these capabilities.
                    handler = bind_handler(client, name)
                    pending.append(
                        Tool(
                            f"mcp.{self.server}.{name}",
                            metadata.get("inputSchema", {}),
                            permission.capabilities,
                            handler,
                            permission.resources,
                            connection_id=self.connection_id,
                        )
                    )
                self.executor.register_many(pending)
                self.client = client
                self._connected = True
                return {"connected": True, "registered": len(pending)}
            except Exception:
                close = getattr(client, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        self.executor.event(
                            "security.mcp_cleanup_failed",
                            f"mcp.{self.server}.connect",
                            "cleanup_failed",
                        )
                raise

        name = f"mcp.{self.server}.connect"
        self.executor.register(
            Tool(name, schema, capabilities, establish, resources, connection_id=self.connection_id)
        )
        return self.executor.execute({"tool": name, "arguments": args})
