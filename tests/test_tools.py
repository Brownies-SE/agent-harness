import os
from datetime import datetime, timezone

import pytest
from test_runtime import Decisions

from agent_harness.clock import current_time_tool
from agent_harness.errors import SecurityError
from agent_harness.executor import Executor, Tool
from agent_harness.filesystem import filesystem_tools
from agent_harness.policy import Policy


def tools(tmp_path, *, rules=None, sensitive_allow=()):
    policy = Policy(
        roots=(tmp_path,),
        rules=rules
        if rules is not None
        else {"filesystem.read": "allow", "filesystem.write": "ask"},
        sensitive_allow=sensitive_allow,
    )
    executor = Executor(policy)
    for tool in filesystem_tools(policy):
        executor.register(tool)
    executor.register(current_time_tool())
    return executor


def execute(executor, name, **arguments):
    return executor.execute({"tool": name, "arguments": arguments})


def test_clock_reports_same_instant_in_utc_and_local_time():
    executor = Executor(Policy())
    executor.register(current_time_tool())
    before = datetime.now(timezone.utc)
    result = execute(executor, "get_current_time")
    after = datetime.now(timezone.utc)
    utc = datetime.fromisoformat(result["utc"])
    local = datetime.fromisoformat(result["local"])
    assert before <= utc <= after
    assert utc.utcoffset().total_seconds() == 0
    assert local.tzinfo is not None
    assert local.astimezone(timezone.utc) == utc


def test_clock_rejects_model_supplied_extra_arguments():
    executor = Executor(Policy())
    executor.register(current_time_tool())
    with pytest.raises(SecurityError, match="invalid_arguments"):
        execute(executor, "get_current_time", command="anything")


def test_list_root_and_subdirectory(tmp_path):
    (tmp_path / "z.txt").write_text("value")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "child.txt").write_text("value")
    executor = tools(tmp_path)
    result = execute(executor, "list_directory", path=".")
    assert result == {
        "entries": [{"name": "folder", "type": "directory"}, {"name": "z.txt", "type": "file"}],
        "truncated": False,
    }
    assert execute(executor, "list_directory", path="folder")["entries"] == [
        {"name": "child.txt", "type": "file"}
    ]
    assert execute(executor, "list_directory", path=str(tmp_path)) == result


def test_listing_hides_sensitive_links_and_special_files(tmp_path):
    for name in (".env", "credentials.json", "private.pem", "normal.txt"):
        (tmp_path / name).write_text("value")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "normal.txt")
    os.link(tmp_path / "normal.txt", tmp_path / "hard")
    os.mkfifo(tmp_path / "pipe")
    (tmp_path / "safe.txt").write_text("safe")
    result = execute(tools(tmp_path), "list_directory", path=".")
    assert result["entries"] == [{"name": "safe.txt", "type": "file"}]


def test_sensitive_listing_override_is_exact(tmp_path):
    (tmp_path / ".env").write_text("approved")
    (tmp_path / ".env.prod").write_text("blocked")
    executor = tools(tmp_path, sensitive_allow=(tmp_path / ".env",))
    assert execute(executor, "list_directory", path=".")["entries"] == [
        {"name": ".env", "type": "file"}
    ]


@pytest.mark.parametrize("path", ["..", "../outside", "~/.ssh", ".ssh", ".env", "/etc"])
def test_directory_paths_cannot_bypass_policy(tmp_path, path):
    with pytest.raises(SecurityError):
        execute(tools(tmp_path), "list_directory", path=path)


def test_directory_symlink_is_rejected(tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "folder", target_is_directory=True)
    with pytest.raises(SecurityError):
        execute(tools(tmp_path), "list_directory", path="alias")


@pytest.mark.parametrize(
    "name,args", [("read_file", {"path": "."}), ("write_file", {"path": ".", "content": "bad"})]
)
def test_root_is_still_invalid_for_file_tools(tmp_path, name, args):
    with pytest.raises(SecurityError, match="file_required"):
        execute(tools(tmp_path), name, **args)


def test_directory_listing_requires_read_permission(tmp_path):
    with pytest.raises(SecurityError, match="capability_denied"):
        execute(tools(tmp_path, rules={}), "list_directory", path=".")


@pytest.mark.parametrize("arguments", [{}, {"path": 7}, {"path": ".", "extra": True}])
def test_listing_arguments_are_strict(tmp_path, arguments):
    with pytest.raises(SecurityError, match="invalid_arguments"):
        execute(tools(tmp_path), "list_directory", **arguments)


def test_listing_has_bounded_output(tmp_path):
    for index in range(205):
        (tmp_path / f"item-{index:03d}.txt").touch()
    result = execute(tools(tmp_path), "list_directory", path=".")
    assert len(result["entries"]) == 200
    assert result["truncated"] is True
    names = [item["name"] for item in result["entries"]]
    assert names == sorted(names)


def test_agent_use_the_same_new_tools(tmp_path, runner):
    (tmp_path / "README.md").write_text("hello")
    decisions = Decisions(
        [
            {"tool": "list_directory", "arguments": {"path": "."}},
            {"tool": "get_current_time", "arguments": {}},
            {"final": "done"},
        ]
    )
    events = []
    agent = runner(decisions, tools(tmp_path), on_event=events.append)
    assert agent.run("List files and get the time") == "done"
    allowed = [e["tool"] for e in events if e["event"] == "security.tool_allowed"]
    assert allowed == ["list_directory", "get_current_time"]


def test_unknown_resource_types_remain_rejected():
    with pytest.raises(ValueError):
        Executor(Policy()).register(
            Tool(
                "bad",
                {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
                (),
                lambda **kw: None,
                {"target": "unknown"},
            )
        )


def test_directory_scan_is_bounded_when_all_entries_are_hidden(tmp_path):
    for index in range(1005):
        (tmp_path / f".env.{index}").touch()
    result = execute(tools(tmp_path), "list_directory", path=".")
    assert result == {"entries": [], "truncated": True}
