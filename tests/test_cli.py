import json
import signal
import sys

import pytest
from test_runtime import Decisions

from agent_harness import cli
from agent_harness.history import RunHistory


def test_cli_without_command_prints_help_without_reading_input(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("noninteractive CLI attempted to read input or build an agent")

    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(cli, "build_agent", forbidden)
    monkeypatch.setattr(sys, "argv", ["agent-harness"])
    assert cli.main() == 0
    assert "usage:" in capsys.readouterr().out


def test_cli_rejects_removed_chat_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent-harness", "chat"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_noninteractive_cli_never_prompts_for_tool_approval(tmp_path, monkeypatch, capsys):
    from agent_harness.config import load_config
    from agent_harness.core.runtime import RunLimits

    path = tmp_path / "config.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path.write_text(
        "model: {name: test}\nfilesystem: {roots: [./workspace]}\n"
        "permissions: {filesystem.write: ask}"
    )
    monkeypatch.setattr(
        cli,
        "LocalModel",
        lambda *a, **kw: Decisions(
            [
                {"tool": "write_file", "arguments": {"path": "note.txt", "content": "forbidden"}},
                {"final": "denied"},
            ]
        ),
    )

    def forbidden(details):
        pytest.fail("CLI installed an interactive approval callback")

    monkeypatch.setattr(cli, "terminal_approval", forbidden, raising=False)
    agent = cli.build_agent(load_config(path), RunLimits(), None)
    assert agent.run("write it") == "denied"
    assert not (workspace / "note.txt").exists()
    assert "Approval required" not in capsys.readouterr().err


def config(tmp_path):
    path = tmp_path / "harness.yaml"
    path.write_text("model: {name: test}\nrun: {max_steps: 3, timeout_seconds: 30}")
    return path


def test_cli_run_then_history_without_model_or_config(tmp_path, monkeypatch, capsys):
    policy = config(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setattr(
        cli, "LocalModel", lambda *a, **kw: Decisions([{"final": "private-answer"}])
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-harness",
            "run",
            "--config",
            str(policy),
            "--history-dir",
            str(state),
            "--max-steps",
            "2",
            "--max-output-tokens",
            "77",
            "--prompt",
            "private-prompt",
        ],
    )
    assert cli.main() == 0
    output = capsys.readouterr()
    answer = json.loads(output.out)
    assert answer["answer"] == "private-answer"
    assert len(answer["run_id"]) == 32
    assert "private-" not in output.err
    monkeypatch.setattr(sys, "argv", ["agent-harness", "history", "--history-dir", str(state)])
    assert cli.main() == 0
    run = json.loads(capsys.readouterr().out)[0]
    assert run["status"] == "completed"
    assert run["runner"] == "langgraph"
    assert run["limits"]["max_steps"] == 2
    assert run["limits"]["max_output_tokens"] == 77
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-harness", "history", "--history-dir", str(state), "--run-id", answer["run_id"]],
    )
    assert cli.main() == 0
    events = json.loads(capsys.readouterr().out)["events"]
    assert events[-1]["event"] == "run.finished"


def test_no_history_creates_no_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "LocalModel", lambda *a, **kw: Decisions([{"final": "done"}]))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-harness",
            "run",
            "--config",
            str(config(tmp_path)),
            "--no-history",
            "--history-dir",
            str(tmp_path / "state"),
            "--prompt",
            "test",
        ],
    )
    assert cli.main() == 0
    assert not (tmp_path / "state").exists()


def test_sigint_cancels_run_and_restores_original_handler(tmp_path, monkeypatch, capsys):
    previous = signal.getsignal(signal.SIGINT)

    def interrupted():
        signal.raise_signal(signal.SIGINT)
        return {"final": "should not finish"}

    monkeypatch.setattr(cli, "LocalModel", lambda *a, **kw: Decisions([interrupted]))
    state = tmp_path / "state"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-harness",
            "run",
            "--config",
            str(config(tmp_path)),
            "--history-dir",
            str(state),
            "--prompt",
            "test",
        ],
    )
    assert cli.main() == 130
    assert signal.getsignal(signal.SIGINT) == previous
    assert RunHistory(state).list_runs()[0]["status"] == "cancelled"
    assert "should not finish" not in capsys.readouterr().out
