from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from agent_harness.core.executor import json_copy
from agent_harness.core.policy import CAPABILITIES, Policy
from agent_harness.core.runtime import RunLimits
from agent_harness.models.settings import EmbeddingConfig
from agent_harness.tools.mcp import MCPPermission


def obj(properties):
    return {"type": "object", "properties": properties, "additionalProperties": False}


STRING_LIST = {"type": "array", "items": {"type": "string"}}
CONFIG_SCHEMA = obj(
    {
        "run": obj(
            {
                "max_steps": {"type": "integer", "minimum": 1, "maximum": 100},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
                "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": 16384},
            }
        ),
        "security": obj({"local_only": {"type": "boolean"}, "telemetry": {"type": "boolean"}}),
        "permissions": obj({c: {"enum": ["allow", "deny", "ask"]} for c in CAPABILITIES}),
        "network": obj(
            {
                "default": {"const": "deny"},
                "allow": STRING_LIST,
                "ports": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 65535},
                },
            }
        ),
        "filesystem": obj({"roots": STRING_LIST, "sensitive_allow": STRING_LIST}),
        "model": obj(
            {
                "provider": {"const": "local"},
                "endpoint": {"type": "string"},
                "name": {"type": "string"},
            }
        ),
        "embeddings": obj({"endpoint": {"type": "string"}, "model": {"type": "string"}}),
        "mcp_permissions": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": obj(
                    {
                        "capabilities": {"type": "array", "items": {"enum": sorted(CAPABILITIES)}},
                        "resources": {
                            "type": "object",
                            "additionalProperties": {"enum": ["path", "directory", "url"]},
                        },
                    }
                ),
            },
        },
        "mcp_endpoints": {"type": "object", "additionalProperties": {"type": "string"}},
    }
)


class StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("duplicate or invalid configuration key")
        result[key] = loader.construct_object(value_node)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class Config:
    policy: Policy
    endpoint: str
    model: str
    provider: str
    limits: RunLimits = RunLimits()
    embeddings: EmbeddingConfig | None = None
    mcp_permissions: dict = field(default_factory=dict)
    mcp_endpoints: dict = field(default_factory=dict)


def load_config(path):
    path = Path(path).absolute()
    try:
        with path.open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("configuration too large")
        # StrictLoader inherits SafeLoader; override only duplicate-key handling.
        data = json_copy(yaml.load(raw, Loader=StrictLoader))  # noqa: S506
        if data is None:
            data = {}
        if not Draft202012Validator(CONFIG_SCHEMA).is_valid(data):
            raise ValueError("invalid configuration schema")
        filesystem = data.get("filesystem", {})
        security = data.get("security", {})
        network = data.get("network", {})
        model = data.get("model", {})

        def paths(key):
            return tuple(path.parent / Path(p).expanduser() for p in filesystem.get(key, []))

        policy = Policy(
            **security,
            roots=paths("roots"),
            sensitive_allow=paths("sensitive_allow"),
            rules=data.get("permissions", {}),
            network_allow=network.get("allow", ()),
            network_ports=network.get("ports", ()),
        )
        return Config(
            policy,
            model.get("endpoint", "http://127.0.0.1:11434"),
            model.get("name", ""),
            model.get("provider", "local"),
            RunLimits(**data.get("run", {})),
            EmbeddingConfig(**data["embeddings"]) if "embeddings" in data else None,
            {
                server: {
                    tool: MCPPermission(
                        tuple(settings.get("capabilities", [])), settings.get("resources", {})
                    )
                    for tool, settings in tools.items()
                }
                for server, tools in data.get("mcp_permissions", {}).items()
            },
            data.get("mcp_endpoints", {}),
        )
    except (OSError, yaml.YAMLError, RecursionError, TypeError) as exc:
        raise ValueError("configuration unavailable or invalid") from exc
