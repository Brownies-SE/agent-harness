import json
import re

import pytest

from agent_harness.config import Config
from agent_harness.core.policy import Policy
from agent_harness.evaluations import evaluate
from agent_harness.history import RunHistory


class TaskModel:
    def __init__(self, *args, **kwargs):
        pass

    def decide(self, messages, tools):
        prompt = messages[1]["content"]
        observations = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not observations:
            if prompt.startswith("For this permission test,"):
                return {"tool": "http_get", "arguments": {"url": "https://example.com"}}
            filename = (
                "east.txt"
                if "east.txt" in prompt
                else "source.txt"
                if "source.txt" in prompt
                else ".env"
                if ".env" in prompt
                else "lookup.txt"
            )
            return {"tool": "read_file", "arguments": {"path": filename}}
        if "west.txt" in prompt:
            if len(observations) == 1:
                return {"tool": "read_file", "arguments": {"path": "west.txt"}}
            total = sum(int(re.search(r"[0-9]+", o["data"])[0]) for o in observations)
            return {"final": str(total)}
        if "report.txt" in prompt:
            marker = observations[0]["data"].strip().split()[-1]
            if len(observations) == 1:
                return {
                    "tool": "write_file",
                    "arguments": {"path": "report.txt", "content": marker},
                }
            return {"final": "done"}
        if observations[0]["status"] == "denied":
            return {"final": "DENIED"}
        return {"final": observations[0]["data"].strip().split()[-1]}


def configuration():
    return Config(Policy(), "http://127.0.0.1:11434", "test", "local")


def test_evaluations_score_real_tools_and_outcomes_with_langgraph(tmp_path):
    history = RunHistory(tmp_path / "state")
    result = evaluate(configuration(), repeats=2, history=history, model_factory=TaskModel)
    assert result["total"] == 10
    assert result["passed"] == 10
    assert result["pass_rate"] == 1
    assert {r["runner"] for r in result["cases"]} == {"langgraph"}
    assert len(history.list_runs()) == 10
    assert all(r["run_id"] for r in result["cases"])
    assert all(r["elapsed_ms"] >= 0 for r in result["cases"])
    assert "answer" not in json.dumps(result)


def test_model_claiming_success_without_tool_use_fails_every_case():
    class Pretender(TaskModel):
        def decide(self, messages, tools):
            return {"final": "DENIED, everything completed successfully"}

    result = evaluate(configuration(), model_factory=Pretender)
    assert result["total"] == 5
    assert result["passed"] == 0


def test_eval_network_case_never_reaches_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network tool bypassed evaluation policy")

    monkeypatch.setattr("agent_harness.tools.network.transport", forbidden)
    result = evaluate(configuration(), model_factory=TaskModel)
    assert result["passed"] == 5


@pytest.mark.parametrize("repeats", [0, 21, True])
def test_invalid_evaluation_options_rejected(repeats):
    with pytest.raises(ValueError):
        evaluate(configuration(), repeats=repeats, model_factory=TaskModel)


@pytest.mark.parametrize("repeat_target", [False, True])
def test_denial_tasks_require_the_exact_target_once(repeat_target):
    class IncorrectDenials(TaskModel):
        def decide(self, messages, tools):
            prompt = messages[1]["content"]
            observations = [m for m in messages if m["role"] == "tool"]
            if prompt.startswith(("For this permission test,", "Request read_file for .env")):
                if not observations or (repeat_target and len(observations) == 1):
                    if prompt.startswith("For this permission test,"):
                        url = "https://example.com" if repeat_target else "https://wrong.example"
                        return {"tool": "http_get", "arguments": {"url": url}}
                    path = ".env" if repeat_target else "/wrong/path"
                    return {"tool": "read_file", "arguments": {"path": path}}
                return {"final": "DENIED"}
            return super().decide(messages, tools)

    result = evaluate(configuration(), model_factory=IncorrectDenials)
    denied_cases = [r for r in result["cases"] if r["case"].startswith("deny_")]
    assert len(denied_cases) == 2
    assert all(not r["passed"] for r in denied_cases)


def test_failure_report_distinguishes_missing_tool_use():
    class NoTools(TaskModel):
        def decide(self, messages, tools):
            return {"final": "completed"}

    result = evaluate(configuration(), model_factory=NoTools)
    assert result["cases"][0]["reason"] == "missing_required_read"
