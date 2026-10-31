import hashlib
import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Mapping
from uuid import uuid4

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError
from referencing import Registry

from .errors import SecurityError
from .models.tools import ToolRequest
from .policy import Policy
from .runtime import RunStopped, check_run, current_run
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
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None


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
        data = {
            "event": name,
            "tool": tool,
            "reason": reason,
            "request_id": request_id or uuid4().hex,
        }
        control = current_run()
        if control is not None:
            if name == "security.tool_denied":
                control.denied_tools += 1
            control.record(name, tool=tool, reason=reason, request_id=data["request_id"])
            data["run_id"] = control.id
        try:
            self._audit(data)
        except Exception as exc:
            raise SecurityError("audit_unavailable") from exc

    def register(self, tool: Tool):
        for model in (tool.input_model, tool.output_model):
            if model is not None and (
                not isinstance(model, type) or not issubclass(model, BaseModel)
            ):
                raise ValueError("tool models must be host-supplied Pydantic models")
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,95}", tool.name):
            raise ValueError("invalid tool name")
        if tool.name in self._tools:
            raise ValueError("duplicate tool")
        schema = json_copy(tool.schema)
        validate_schema(schema)
        resources = dict(tool.resources)
        for key, kind in resources.items():
            if kind not in {"path", "directory", "url"} or key not in schema.get("required", []):
                raise ValueError("resource must be a required argument")
            if schema.get("properties", {}).get(key, {}).get("type") != "string":
                raise ValueError("resource must have string schema")
        if any(c.startswith("filesystem.") for c in tool.capabilities):
            if not {"path", "directory"}.intersection(resources.values()):
                raise ValueError("filesystem tools require a path mapping")
        if "network" in tool.capabilities and "url" not in resources.values():
            raise ValueError("network tools require a URL mapping")
        self._tools[tool.name] = Tool(
            tool.name,
            schema,
            tuple(tool.capabilities),
            tool.handler,
            resources,
            tool.input_model,
            tool.output_model,
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
        control = check_run()
        started = time.monotonic()
        name = request.get("tool") if isinstance(request, dict) else None
        name = name if isinstance(name, str) and name in self._tools else "<unknown>"
        if control is not None:
            control.tool_calls += 1
        status = "failed"
        try:
            result = self._execute(request)
            check_run()
            status = "completed"
            return result
        except RunStopped:
            status = "stopped"
            raise
        except SecurityError:
            status = "denied"
            raise
        finally:
            if control is not None:
                control.record(
                    "tool.finished",
                    tool=name,
                    status=status,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

    def _execute(self, request):
        request_id = uuid4().hex
        name = "<unknown>"
        try:
            request = json_copy(request)
            try:
                request = ToolRequest.model_validate(request).model_dump()
            except ValidationError as exc:
                raise SecurityError("invalid_request") from exc
            tool = self._tools.get(request["tool"])
            if tool is None:
                raise SecurityError("unknown_tool")
            name = tool.name
            args = request["arguments"]
            validator = Draft202012Validator(tool.schema, registry=Registry())
            if not validator.is_valid(args):
                raise SecurityError("invalid_arguments")
            if tool.input_model is not None:
                try:
                    tool.input_model.model_validate(args, strict=True)
                except ValidationError as exc:
                    raise SecurityError("invalid_arguments") from exc
            # Resource checks precede approval; approval can never expand resource limits.
            from .filesystem import check_path
            from .network import check_url

            for key, kind in tool.resources.items():
                try:
                    if kind in {"path", "directory"}:
                        _, canonical = check_path(
                            self.policy, args[key], allow_root=kind == "directory"
                        )
                        args[key] = str(canonical)
                    else:
                        check_url(self.policy, args[key])
                except SecurityError:
                    self.event(
                        "security.network_blocked" if kind == "url" else "security.path_blocked",
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
                check_run()
                if not isinstance(answer, str) or answer not in {"once", "run"}:
                    self.event("security.approval_denied", name, request_id=request_id)
                    raise SecurityError("approval_denied")
                self.event("security.approval_granted", name, request_id=request_id)
                if answer == "run":
                    self._grants.add(fingerprint)
            check_run()
            self.event("security.tool_allowed", name, request_id=request_id)
            check_run()
            try:
                result = json_copy(tool.handler(**args), MAX_RESULT_BYTES)
                if tool.output_model is not None:
                    tool.output_model.model_validate(result, strict=True)
                return result
            except SecurityError:
                raise
            except Exception as exc:
                raise SecurityError("tool_failed") from exc
        except SecurityError as exc:
            self.event("security.tool_denied", name, str(exc), request_id)
            raise
