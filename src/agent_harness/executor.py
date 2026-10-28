import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Mapping
from uuid import uuid4

from jsonschema import Draft202012Validator
from referencing import Registry

from .errors import SecurityError
from .policy import Policy
from .schema import validate_schema

MAX_REQUEST_BYTES = 128 * 1024
MAX_RESULT_BYTES = 256 * 1024


@dataclass(frozen=True)
class Tool:
    name: str
    schema: dict
    capabilities: tuple[str, ...]
    handler: Callable
    resources: Mapping[str, str] = field(default_factory=dict)


def json_copy(value, limit=MAX_REQUEST_BYTES):
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=True)
        if len(encoded) > limit:
            raise SecurityError("payload_too_large")
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise SecurityError("invalid_json") from exc


class Executor:
    """Only trusted host code constructs/configures this object, never model output."""

    def __init__(self, policy: Policy, *, approve=None, audit=None):
        self.policy = policy
        self._approve = approve
        self._audit = audit or (lambda event: None)
        self._tools = {}
        self._grants = set()

    def start_run(self):
        """Host-only lifecycle boundary; do not share one executor across concurrent runs."""
        self._grants.clear()

    def event(self, name, tool, reason="", request_id=None):
        try:
            self._audit(
                {
                    "event": name,
                    "tool": tool,
                    "reason": reason,
                    "request_id": request_id or uuid4().hex,
                }
            )
        except Exception as exc:
            raise SecurityError("audit_unavailable") from exc

    def register(self, tool: Tool):
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,95}", tool.name):
            raise ValueError("invalid tool name")
        if tool.name in self._tools:
            raise ValueError("duplicate tool")
        schema = json_copy(tool.schema)
        validate_schema(schema)
        resources = dict(tool.resources)
        for key, kind in resources.items():
            if kind not in {"path", "url"} or key not in schema.get("required", []):
                raise ValueError("resource must be a required argument")
            if schema.get("properties", {}).get(key, {}).get("type") != "string":
                raise ValueError("resource must have string schema")
        if any(c.startswith("filesystem.") for c in tool.capabilities):
            if "path" not in resources.values():
                raise ValueError("filesystem tools require a path mapping")
        if "network" in tool.capabilities and "url" not in resources.values():
            raise ValueError("network tools require a URL mapping")
        self._tools[tool.name] = Tool(
            tool.name, schema, tuple(tool.capabilities), tool.handler, resources
        )

    def register_many(self, tools):
        """Publish a discovery batch atomically for this sequential executor."""
        before = dict(self._tools)
        try:
            for tool in tools:
                self.register(tool)
        except Exception:
            self._tools = before
            raise

    def descriptions(self):
        return [{"name": t.name, "parameters": deepcopy(t.schema)} for t in self._tools.values()]

    def execute(self, request):
        request_id = uuid4().hex
        name = "<unknown>"
        try:
            request = json_copy(request)
            if not isinstance(request, dict) or set(request) != {"tool", "arguments"}:
                raise SecurityError("invalid_request")
            if not isinstance(request["tool"], str) or not isinstance(request["arguments"], dict):
                raise SecurityError("invalid_request")
            tool = self._tools.get(request["tool"])
            if tool is None:
                raise SecurityError("unknown_tool")
            name = tool.name
            args = request["arguments"]
            validator = Draft202012Validator(tool.schema, registry=Registry())
            if not validator.is_valid(args):
                raise SecurityError("invalid_arguments")
            # Resource checks precede approval; approval can never expand resource limits.
            from .filesystem import check_path
            from .network import check_url

            for key, kind in tool.resources.items():
                try:
                    if kind == "path":
                        _, canonical = check_path(self.policy, args[key])
                        args[key] = str(canonical)
                    else:
                        check_url(self.policy, args[key])
                except SecurityError:
                    self.event(
                        "security.path_blocked" if kind == "path" else "security.network_blocked",
                        name,
                        "resource_denied",
                        request_id,
                    )
                    raise
            decisions = [self.policy.decision(c) for c in tool.capabilities]
            if "deny" in decisions:
                raise SecurityError("capability_denied")
            fingerprint = hashlib.sha256(
                json.dumps([name, args, tool.capabilities], sort_keys=True).encode()
            ).hexdigest()
            if "ask" in decisions and fingerprint not in self._grants:
                self.event("security.approval_requested", name, request_id=request_id)
                answer = "deny"
                if self._approve:
                    try:
                        answer = self._approve(
                            {
                                "tool": name,
                                "arguments": deepcopy(args),
                                "capabilities": tool.capabilities,
                            }
                        )
                    except Exception:
                        answer = "deny"
                if not isinstance(answer, str) or answer not in {"once", "run"}:
                    self.event("security.approval_denied", name, request_id=request_id)
                    raise SecurityError("approval_denied")
                self.event("security.approval_granted", name, request_id=request_id)
                if answer == "run":
                    self._grants.add(fingerprint)
            self.event("security.tool_allowed", name, request_id=request_id)
            try:
                return json_copy(tool.handler(**args), MAX_RESULT_BYTES)
            except SecurityError:
                raise
            except Exception as exc:
                raise SecurityError("tool_failed") from exc
        except SecurityError as exc:
            self.event("security.tool_denied", name, str(exc), request_id)
            raise
