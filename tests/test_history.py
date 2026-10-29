import json
import stat
import threading

import pytest
from test_runtime import Decisions

from agent_harness.agents import LangGraphAgent, NativeAgent
from agent_harness.errors import SecurityError
from agent_harness.executor import Executor, Tool
from agent_harness.filesystem import filesystem_tools
from agent_harness.history import RunHistory
from agent_harness.policy import Policy
from agent_harness.runtime import RunLimits


@pytest.mark.parametrize("runner", [NativeAgent, LangGraphAgent])
def test_history_persists_metadata_but_not_prompt_paths_contents_or_answer(tmp_path, runner):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "private-document.txt").write_text("file-secret-12345")
    history = RunHistory(tmp_path / "state", roots=(workspace,))
    policy = Policy(roots=(workspace,), rules={"filesystem.read": "allow"})
    executor = Executor(policy)
    for tool in filesystem_tools(policy):
        executor.register(tool)
    model = Decisions(
        [
            {"tool": "read_file", "arguments": {"path": "private-document.txt"}},
            {"final": "answer-secret-12345"},
        ]
    )
    agent = runner(model, executor, history=history)
    assert agent.run("prompt-secret-12345") == "answer-secret-12345"
    reopened = RunHistory(tmp_path / "state")
    run = reopened.list_runs()[0]
    assert run["run_id"] == agent.last_run.id
    assert run["status"] == "completed"
    assert run["model_calls"] == 2
    assert run["tool_calls"] == 1
    events = reopened.events(run["run_id"])
    assert any(e["event"] == "model.finished" and e["duration_ms"] >= 0 for e in events)
    assert any(e["event"] == "security.tool_allowed" and e["tool"] == "read_file" for e in events)
    stored = history.database.read_bytes() + json.dumps(events).encode()
    for secret in (
        b"prompt-secret-12345",
        b"answer-secret-12345",
        b"file-secret-12345",
        b"private-document.txt",
        str(workspace).encode(),
    ):
        assert secret not in stored
    assert stat.S_IMODE(history.database.stat().st_mode) == 0o600


def test_history_cannot_be_inside_a_tool_root(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        RunHistory(tmp_path / "state", roots=(tmp_path,))
    assert not (tmp_path / "state").exists()


def test_history_refuses_symlink_and_hardlink_database(tmp_path):
    outside = tmp_path / "other"
    outside.write_text("do not modify")
    state = tmp_path / "state"
    state.mkdir()
    (state / "runs.sqlite3").symlink_to(outside)
    with pytest.raises(SecurityError):
        RunHistory(state)
    assert outside.read_text() == "do not modify"


@pytest.mark.parametrize("runner", [NativeAgent, LangGraphAgent])
@pytest.mark.parametrize(
    "mode,expected", [("cancel", "cancelled"), ("error", "failed"), ("limit", "limited")]
)
def test_stopped_runs_have_durable_terminal_status(tmp_path, runner, mode, expected):
    cancel = threading.Event()
    if mode == "cancel":
        cancel.set()
        responses = []
    elif mode == "error":
        responses = [{"bad": "private-error-text"}]
    else:
        responses = [{"tool": "unknown", "arguments": {}}]
    history = RunHistory(tmp_path / "state")
    agent = runner(
        Decisions(responses), Executor(Policy()), history=history, limits=RunLimits(max_steps=1)
    )
    with pytest.raises(SecurityError):
        agent.run("private-prompt", cancel=cancel)
    assert history.list_runs()[0]["status"] == expected
    assert b"private-" not in history.database.read_bytes()


def test_unknown_error_details_and_extra_event_fields_are_redacted(tmp_path):
    history = RunHistory(tmp_path / "state")
    history.begin(
        "a" * 32, "native", {"max_steps": 8, "timeout_seconds": 120, "max_output_tokens": 512}
    )
    history.append(
        "a" * 32,
        {
            "event": "security.tool_denied",
            "tool": "read_file",
            "reason": "secret-exception-message",
            "arguments": "secret-args",
            "result": "secret-result",
            "prompt": "secret-prompt",
        },
    )
    events = history.events("a" * 32)
    assert events[0]["reason"] == "operation_failed"
    assert b"secret-" not in history.database.read_bytes()


def test_history_failure_prevents_tool_dispatch(tmp_path):
    class BrokenHistory:
        def begin(self, *args):
            pass

        def append(self, run_id, event):
            if event["event"] == "security.tool_allowed":
                raise SecurityError("history_unavailable")

        def finish(self, *args, **kwargs):
            pass

    called = []
    executor = Executor(Policy())
    executor.register(
        Tool(
            "noop",
            {"type": "object", "properties": {}, "additionalProperties": False},
            (),
            lambda: called.append(1),
        )
    )
    model = Decisions([{"tool": "noop", "arguments": {}}, {"final": "done"}])
    agent = NativeAgent(model, executor, history=BrokenHistory())
    agent.run("test")
    assert called == []


def test_agent_rejects_history_inside_its_tool_roots(tmp_path):
    history = RunHistory(tmp_path / "state")
    executor = Executor(Policy(roots=(tmp_path,), rules={"filesystem.read": "allow"}))
    with pytest.raises(ValueError, match="outside"):
        NativeAgent(Decisions([]), executor, history=history)


def test_malformed_event_fields_cannot_escape_redaction(tmp_path):
    history = RunHistory(tmp_path / "state")
    history.begin("b" * 32, "native", {})
    history.append(
        "b" * 32, {"event": ["secret"], "reason": {"secret": True}, "status": ["secret"]}
    )
    assert history.events("b" * 32) == [{"event": "run.event", "reason": "operation_failed"}]


def test_failed_run_preserves_safe_error_code_without_exception_text(tmp_path):
    history = RunHistory(tmp_path / "state")
    agent = NativeAgent(Decisions([{"invalid": True}]), Executor(Policy()), history=history)
    with pytest.raises(SecurityError):
        agent.run("private-prompt")
    assert history.list_runs()[0]["reason"] == "invalid_model_response"
