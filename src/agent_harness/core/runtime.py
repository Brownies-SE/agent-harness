"""Host-owned run lifecycle for the LangGraph agent."""

import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from uuid import uuid4

from agent_harness.core.errors import SecurityError, safe_reason

_ACTIVE = ContextVar("agent_harness_run", default=None)


class RunStopped(SecurityError):
    """Cancellation or a run budget; never turn this into another model observation."""


@dataclass(frozen=True)
class RunLimits:
    max_steps: int = 8
    timeout_seconds: float = 120
    max_output_tokens: int = 512

    def __post_init__(self):
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 100:
            raise ValueError("max_steps must be between 1 and 100")
        if (
            type(self.timeout_seconds) not in {int, float}
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 3600
        ):
            raise ValueError("timeout_seconds must be positive and at most 3600")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 16384:
            raise ValueError("max_output_tokens must be between 1 and 16384")


def current_run():
    return _ACTIVE.get()


def check_run():
    control = current_run()
    if control is not None:
        control.check()
    return control


class RunControl:
    def __init__(self, limits=None, *, cancel=None, history=None, on_event=None):
        self.limits = limits or RunLimits()
        self.cancel = cancel if cancel is not None else threading.Event()
        self.history = history
        self.on_event = on_event
        self.id = uuid4().hex
        self.started = time.monotonic()
        self.deadline = self.started + self.limits.timeout_seconds
        self.model_calls = 0
        self.tool_calls = 0
        self.denied_tools = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.status = "created"
        self.reason = ""

    def stop_reason(self):
        if self.cancel.is_set():
            return "run_cancelled"
        if time.monotonic() >= self.deadline:
            return "run_timeout"
        return ""

    def check(self):
        reason = self.stop_reason()
        if reason:
            raise RunStopped(reason)

    def record(self, event, **metadata):
        data = {
            "event": event,
            "run_id": self.id,
            "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            **metadata,
        }
        if self.history is not None:
            self.history.append(self.id, data)
        if self.on_event is not None:
            try:
                self.on_event(data)
            except Exception as exc:
                raise SecurityError("audit_unavailable") from exc

    def model_started(self):
        self.check()
        if self.model_calls >= self.limits.max_steps:
            raise RunStopped("step_limit")
        self.model_calls += 1
        self.record("model.started")

    def usage(self, usage):
        # Provider-reported accounting is informational, never a permission/budget grant.
        for field, attribute in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ):
            value = usage.get(field) if isinstance(usage, dict) else None
            if type(value) is int and 0 <= value <= 1000000000:
                setattr(self, attribute, getattr(self, attribute) + value)
        details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if type(cached) is int and 0 <= cached <= 1000000000:
            self.cache_read_tokens += cached

    def finish(self, status, reason=""):
        self.status = status
        self.reason = reason
        self.record("run.finished", status=status, reason=reason)
        if self.history is not None:
            self.history.finish(
                self.id,
                status=status,
                reason=reason,
                elapsed_ms=int((time.monotonic() - self.started) * 1000),
                model_calls=self.model_calls,
                tool_calls=self.tool_calls,
                denied_tools=self.denied_tools,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
            )

    @contextmanager
    def activate(self):
        self.started = time.monotonic()
        self.deadline = self.started + self.limits.timeout_seconds
        if self.history is not None:
            self.history.begin(self.id, asdict(self.limits))
        token = _ACTIVE.set(self)
        self.status = "running"
        try:
            self.record("run.started")
            self.check()
            yield self
            self.check()
        except RunStopped as exc:
            status = {
                "run_cancelled": "cancelled",
                "run_timeout": "timed_out",
                "step_limit": "limited",
            }[str(exc)]
            self.finish(status, str(exc))
            raise
        except KeyboardInterrupt:
            self.finish("cancelled", "run_cancelled")
            raise
        except SecurityError as exc:
            self.finish("failed", safe_reason(str(exc)))
            raise
        except BaseException:
            self.finish("failed", "run_failed")
            raise
        else:
            self.finish("completed")
        finally:
            _ACTIVE.reset(token)
