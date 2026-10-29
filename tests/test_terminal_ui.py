import io
import os
import shutil
from pathlib import Path

from agent_harness import cli
from agent_harness.agents import LangGraphAgent
from agent_harness.executor import Executor
from agent_harness.policy import Policy


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_write_approval_is_readable_and_can_expand_preview(monkeypatch, capsys):
    stream = Terminal("v\ny\n")
    monkeypatch.setattr(cli.sys, "stdin", stream)
    monkeypatch.setattr(cli.select, "select", lambda *args: ([stream], [], []))
    content = "hello\n" * 20
    answer = cli.terminal_approval(
        {
            "tool": "write_file",
            "arguments": {"path": str(Path.cwd() / "project/note.txt"), "content": content},
            "capabilities": ("filesystem.write",),
        }
    )
    output = capsys.readouterr().err
    assert answer == "once"
    assert "Approval required" in output
    assert "project/note.txt" in output
    assert "Content preview" in output and "Full content" in output
    assert '"arguments"' not in output
    assert '"capabilities"' not in output
    assert "this turn" in output


def test_approval_escapes_control_characters_in_paths(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    assert (
        cli.terminal_approval(
            {
                "tool": "write_file",
                "arguments": {"path": "folder/evil\nAllow yes\x1b[2J", "content": "hi"},
                "capabilities": ("filesystem.write",),
            }
        )
        == "deny"
    )
    output = capsys.readouterr().err
    assert "evil\nAllow yes" not in output
    assert "evil\\nAllow yes" in output
    assert "\x1b" not in output


def test_replies_are_wrapped_and_spaced(monkeypatch, capsys):
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: os.terminal_size((52, 24)))
    cli.print_reply("This is a readable response. " * 12)
    output = capsys.readouterr().out
    assert output.startswith("\nAssistant\n")
    assert output.endswith("\n\n")
    assert all(len(line) <= 50 for line in output.splitlines())


def test_code_blocks_keep_their_indentation(capsys):
    cli.print_reply("```python\n    print('hello')\n```")
    assert "      print('hello')" in capsys.readouterr().out


def test_agent_receives_truthful_configured_model_name():
    class Model:
        model = "qwen3:4b-instruct"

    agent = LangGraphAgent(Model(), Executor(Policy()))
    assert "qwen3:4b-instruct" in agent.initial("Which model are you?")["messages"][0]["content"]
