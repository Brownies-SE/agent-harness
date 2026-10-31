import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.executor import Executor, Tool
from agent_harness.core.policy import Policy
from agent_harness.core.runtime import RunControl, RunLimits, RunStopped, current_run
from agent_harness.tools.filesystem import filesystem_tools
from agent_harness.tools.network import check_url, transport


def test_cache_usage_counts_only_valid_provider_numbers():
    control = RunControl()
    control.usage({"prompt_tokens_details": {"cached_tokens": 9}})
    control.usage({"prompt_tokens_details": {"cached_tokens": True}})
    assert control.cache_read_tokens == 9


class Decisions:
    def __init__(self, responses):
        self.responses = iter(responses)

    def decide(self, messages, tools):
        value = next(self.responses)
        return value() if callable(value) else value


@pytest.mark.parametrize(
    "values",
    [
        {"max_steps": 0},
        {"max_steps": True},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("nan")},
        {"max_output_tokens": 0},
        {"max_output_tokens": True},
    ],
)
def test_invalid_limits_rejected(values):
    with pytest.raises(ValueError):
        RunLimits(**values)


def test_cancelled_before_model_never_calls_it(runner):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RunStopped, match="run_cancelled"):
        runner(Decisions([]), Executor(Policy())).run("test", cancel=cancel)
    assert current_run() is None


def test_model_cannot_dispatch_tool_after_deadline(runner):
    executed = []

    def decision():
        time.sleep(0.03)
        return {"tool": "noop", "arguments": {}}

    executor = Executor(Policy())
    executor.register(
        Tool(
            "noop",
            {"type": "object", "properties": {}, "additionalProperties": False},
            (),
            lambda: executed.append(1),
        )
    )
    with pytest.raises(RunStopped, match="run_timeout"):
        runner(Decisions([decision]), executor, limits=RunLimits(timeout_seconds=0.01)).run("test")
    assert executed == []


def test_cancellation_during_approval_prevents_write(tmp_path, runner):
    cancel = threading.Event()

    def approve(details):
        cancel.set()
        return "run"

    policy = Policy(roots=(tmp_path,), rules={"filesystem.write": "ask"})
    executor = Executor(policy, approve=approve)
    for tool in filesystem_tools(policy):
        executor.register(tool)
    decision = {"tool": "write_file", "arguments": {"path": "output", "content": "value"}}
    with pytest.raises(RunStopped, match="run_cancelled"):
        runner(Decisions([decision]), executor).run("test", cancel=cancel)
    assert not (tmp_path / "output").exists()


def test_approval_run_grant_is_cleared_even_after_cancellation(tmp_path):
    cancel = threading.Event()
    approvals = []

    def approve(details):
        approvals.append(details)
        if len(approvals) == 1:
            cancel.set()
            return "run"
        return "deny"

    policy = Policy(roots=(tmp_path,), rules={"filesystem.write": "ask"})
    executor = Executor(policy, approve=approve)
    for tool in filesystem_tools(policy):
        executor.register(tool)
    decision = {"tool": "write_file", "arguments": {"path": "output", "content": "value"}}
    model = Decisions([decision, decision, {"final": "done"}])
    agent = LangGraphAgent(model, executor)
    with pytest.raises(RunStopped):
        agent.run("first", cancel=cancel)
    assert agent.run("second") == "done"
    assert len(approvals) == 2
    assert not (tmp_path / "output").exists()


class Trickle(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "100")
        self.end_headers()
        try:
            for _ in range(100):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.02)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("cancelled", [False, True])
def test_network_trickle_cannot_extend_deadline_or_cancellation(cancelled):
    service = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    cancel = threading.Event()
    target = check_url(
        Policy(network_allow=("127.0.0.1",), network_ports=(service.server_port,)),
        f"http://127.0.0.1:{service.server_port}/",
    )
    timer = threading.Timer(0.1, cancel.set)
    # Test cancellation separately from the short deadline to avoid a scheduler race.
    control = RunControl(RunLimits(timeout_seconds=2 if cancelled else 0.12), cancel=cancel)
    started = time.monotonic()
    try:
        if cancelled:
            timer.start()
        with pytest.raises(RunStopped, match="run_cancelled" if cancelled else "run_timeout"):
            with control.activate():
                transport(target, timeout=2)
        assert time.monotonic() - started < 1
    finally:
        timer.cancel()
        service.shutdown()
        service.server_close()
        thread.join()


@pytest.mark.parametrize("mode", ["cancel", "deadline"])
def test_backend_unwinding_cannot_turn_a_stopped_run_into_success(runner, mode):
    class Finishing(runner):
        def _run(self, prompt):
            result = super()._run(prompt)
            control = current_run()
            if mode == "cancel":
                control.cancel.set()
            else:
                control.deadline = time.monotonic() - 1
            return result

    agent = Finishing(Decisions([{"final": "done"}]), Executor(Policy()))
    with pytest.raises(RunStopped, match="run_cancelled" if mode == "cancel" else "run_timeout"):
        agent.run("test")
    assert agent.last_run.status == ("cancelled" if mode == "cancel" else "timed_out")


def test_run_deadline_interrupts_a_silent_local_peer():
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    stop = threading.Event()

    def hold_connection():
        connection, _ = listener.accept()
        try:
            stop.wait(3)
        finally:
            connection.close()

    thread = threading.Thread(target=hold_connection, daemon=True)
    thread.start()
    port = listener.getsockname()[1]
    try:
        target = check_url(
            Policy(network_allow=("127.0.0.1",), network_ports=(port,)), f"http://127.0.0.1:{port}/"
        )
        began = time.monotonic()
        with pytest.raises(RunStopped, match="run_timeout"):
            with RunControl(RunLimits(timeout_seconds=0.1)).activate():
                transport(target, timeout=2)
        assert time.monotonic() - began < 1
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=1)
