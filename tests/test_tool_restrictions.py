import pytest

from agent_harness import Executor, Policy, SecurityError, Tool


def test_additional_tool_denial_cannot_be_overridden_by_approval():
    called = []
    executor = Executor(Policy(), tool_rules={"pure": "deny"}, approve=lambda details: "once")
    executor.register(
        Tool(
            "pure", {"type": "object", "additionalProperties": False}, (), lambda: called.append(1)
        )
    )
    with pytest.raises(SecurityError, match="capability_denied"):
        executor.execute({"tool": "pure", "arguments": {}})
    assert called == []


def test_additional_allow_never_expands_host_capabilities(tmp_path):
    from agent_harness.tools import builtin_tools

    executor = Executor(
        Policy(roots=(tmp_path,)), tool_rules={"write_file": "allow"}, approve=lambda d: "once"
    )
    for tool in builtin_tools(executor.policy):
        executor.register(tool)
    with pytest.raises(SecurityError, match="capability_denied"):
        executor.execute({"tool": "write_file", "arguments": {"path": "a", "content": "bad"}})
    assert not (tmp_path / "a").exists()


def test_observer_failure_prevents_tool_execution():
    called = []

    def failed(event):
        raise RuntimeError("do not expose this")

    executor = Executor(Policy(), observe=failed)
    executor.register(
        Tool(
            "pure", {"type": "object", "additionalProperties": False}, (), lambda: called.append(1)
        )
    )
    with pytest.raises(SecurityError, match="audit_unavailable"):
        executor.execute({"tool": "pure", "arguments": {}})
    assert called == []


def test_transport_rejects_header_injection_before_connecting():
    from agent_harness.tools.network import Target, transport

    target = Target("http", "127.0.0.1", "127.0.0.1", 1, "/")
    for headers in (
        {"Authorization": "Bearer safe\r\nInjected: yes"},
        {"Host": "attacker.invalid"},
        {"Content-Length": "999"},
        {"X-Test": 17},
    ):
        with pytest.raises(SecurityError, match="invalid_request"):
            transport(target, headers=headers)
