import json
import os

import pytest

from agent_harness.core.executor import Executor, SecurityError, Tool
from agent_harness.core.policy import Policy
from agent_harness.tools.filesystem import filesystem_tools
from agent_harness.tools.network import check_url

SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def request(name="read_file", **args):
    return {"tool": name, "arguments": args}


def setup_fs(tmp_path, **kwargs):
    policy = Policy(
        roots=(tmp_path,), rules={"filesystem.read": "allow", "filesystem.write": "allow"}, **kwargs
    )
    events = []
    executor = Executor(policy, audit=events.append)
    for tool in filesystem_tools(policy):
        executor.register(tool)
    return executor, events


def test_read_and_write_in_root(tmp_path):
    executor, events = setup_fs(tmp_path)
    executor.execute(request("write_file", path="hello.txt", content="hello"))
    assert executor.execute(request(path="hello.txt")) == "hello"
    assert events[-1]["event"] == "security.tool_allowed"
    assert "hello" not in json.dumps(events)


@pytest.mark.parametrize(
    "path",
    [
        "~/.ssh/id_rsa",
        "../../secret",
        ".env",
        ".env.prod",
        "nested/../hello.txt",
        ".ssh/id_rsa",
        "credentials.json",
        "browser/Default/Cookies",
        "private.pem",
    ],
)
def test_sensitive_and_traversal_denied(tmp_path, path):
    executor, events = setup_fs(tmp_path)
    with pytest.raises(SecurityError):
        executor.execute(request(path=path))
    assert any(e["event"] == "security.path_blocked" for e in events)


def test_symlink_escape_and_hardlink_denied(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "outside"
    secret.write_text("secret")
    (root / "link").symlink_to(secret)
    os.link(secret, root / "hard")
    executor, _ = setup_fs(root)
    for path in ("link", "hard"):
        with pytest.raises(SecurityError):
            executor.execute(request(path=path))
        with pytest.raises(SecurityError):
            executor.execute(request("write_file", path=path, content="overwrite"))
    assert secret.read_text() == "secret"


def test_directory_symlink_denied(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "ok").write_text("value")
    (tmp_path / "alias").symlink_to(tmp_path / "dir", target_is_directory=True)
    executor, _ = setup_fs(tmp_path)
    with pytest.raises(SecurityError):
        executor.execute(request(path="alias/ok"))


def test_fifo_denied_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    executor, _ = setup_fs(tmp_path)
    with pytest.raises(SecurityError):
        executor.execute(request(path="pipe"))


def test_sensitive_override_is_exact_and_still_in_root(tmp_path):
    (tmp_path / ".env").write_text("approved")
    executor, _ = setup_fs(tmp_path, sensitive_allow=(tmp_path / ".env",))
    assert executor.execute(request(path=".env")) == "approved"
    with pytest.raises(SecurityError):
        executor.execute(request(path=".env.prod"))


@pytest.mark.parametrize("args", [{"path": 3}, {}, {"path": "ok", "extra": True}])
def test_schema_failure_never_calls_handler(tmp_path, args):
    called = []
    executor = Executor(Policy(roots=(tmp_path,), rules={"filesystem.read": "allow"}))
    executor.register(
        Tool("read", SCHEMA, ("filesystem.read",), lambda **kw: called.append(kw), {"path": "path"})
    )
    with pytest.raises(SecurityError):
        executor.execute(request("read", **args))
    assert called == []


def test_unknown_capabilities_and_tools_denied():
    called = []
    executor = Executor(Policy())
    executor.register(
        Tool(
            "unknown_cap",
            {"type": "object", "properties": {}, "additionalProperties": False},
            ("magic",),
            lambda: called.append(True),
        )
    )
    for name in ("unknown_cap", "missing", "unknown_cap"):
        with pytest.raises(SecurityError):
            executor.execute(request(name))
    assert called == []


@pytest.mark.parametrize(
    "bad",
    [None, [], {"tool": "x", "arguments": {}, "policy": "allow"}, {"tool": "x", "arguments": []}],
)
def test_request_envelope_is_strict(bad):
    with pytest.raises(SecurityError):
        Executor(Policy()).execute(bad)


def test_network_requires_capability_and_destination():
    called = []
    schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    }
    executor = Executor(Policy())
    executor.register(
        Tool("http", schema, ("network",), lambda **kw: called.append(kw), {"url": "url"})
    )
    for _ in range(3):
        with pytest.raises(SecurityError):
            executor.execute(request("http", url="https://example.com"))
    assert called == []


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://8.8.8.8",
        "http://169.254.169.254",
        "http://[::ffff:8.8.8.8]",
        "file:///etc/passwd",
        "http://user:pass@127.0.0.1",
        "http://127.0.0.1:99999",
        "http://127.0.0.1\\@example.com",
        "http://127.0.0.1:22",
    ],
)
def test_network_url_bypasses_denied(url):
    policy = Policy(network_allow=("localhost", "127.0.0.1", "8.8.8.8"), network_ports=(80, 443))
    with pytest.raises(SecurityError):
        check_url(policy, url)


def test_local_url_allowed_only_with_explicit_host_and_port():
    with pytest.raises(SecurityError):
        check_url(Policy(), "http://127.0.0.1:8080")
    target = check_url(
        Policy(network_allow=("127.0.0.1",), network_ports=(8080,)), "http://127.0.0.1:8080/test"
    )
    assert target.host == "127.0.0.1"
    assert target.address == "127.0.0.1"
    assert target.port == 8080


def test_dns_public_answer_denied(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 80))])
    with pytest.raises(SecurityError):
        check_url(Policy(network_allow=("localhost",), network_ports=(80,)), "http://localhost")


def test_approval_once_and_run_are_exact_and_denies_still_win(tmp_path):
    calls = []
    answers = iter(["once", "run", "deny"])

    def approve(details):
        calls.append(details)
        return next(answers)

    policy = Policy(roots=(tmp_path,), rules={"filesystem.write": "ask"})
    executor = Executor(policy, approve=approve)
    for tool in filesystem_tools(policy):
        executor.register(tool)
    payload = request("write_file", path="a", content="one")
    executor.execute(payload)
    executor.execute(payload)
    executor.execute(payload)
    with pytest.raises(SecurityError):
        executor.execute(request("write_file", path="b", content="one"))
    with pytest.raises(SecurityError):
        executor.execute(request("write_file", path=".env", content="one"))
    assert len(calls) == 3
    assert not (tmp_path / "b").exists()
    assert not (tmp_path / ".env").exists()


def test_ask_without_callback_denies(tmp_path):
    policy = Policy(roots=(tmp_path,), rules={"filesystem.write": "ask"})
    executor = Executor(policy)
    for tool in filesystem_tools(policy):
        executor.register(tool)
    with pytest.raises(SecurityError):
        executor.execute(request("write_file", path="a", content="one"))
    assert not (tmp_path / "a").exists()


def test_audit_failure_prevents_execution():
    called = []

    def broken(event):
        raise OSError("audit unavailable")

    executor = Executor(Policy(), audit=broken)
    executor.register(
        Tool(
            "noop",
            {"type": "object", "properties": {}, "additionalProperties": False},
            (),
            lambda: called.append(1),
        )
    )
    with pytest.raises(SecurityError):
        executor.execute(request("noop"))
    assert called == []


def test_policy_cannot_be_mutated():
    rules = {"network": "deny"}
    policy = Policy(rules=rules)
    rules["network"] = "allow"
    assert policy.rules["network"] == "deny"
    with pytest.raises(TypeError):
        policy.rules["network"] = "allow"


def test_registration_requires_resource_mapping():
    executor = Executor(Policy())
    with pytest.raises(ValueError):
        executor.register(Tool("read", SCHEMA, ("filesystem.read",), lambda **kw: None))


def test_registration_schema_is_copied():
    schema = json.loads(json.dumps(SCHEMA))
    executor = Executor(Policy())
    executor.register(Tool("read", schema, (), lambda **kw: "ok"))
    schema["properties"]["path"]["type"] = "number"
    with pytest.raises(SecurityError):
        executor.execute(request("read", path=42))


@pytest.mark.parametrize(
    "extra", [{"pattern": "(a+)+$"}, {"oneOf": [{"type": "string"}]}, {"uniqueItems": True}]
)
def test_unsupported_costly_schema_keywords_rejected(extra):
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string", **extra}},
        "additionalProperties": False,
    }
    with pytest.raises(ValueError):
        Executor(Policy()).register(Tool("unsafe_schema", schema, (), lambda **kw: None))


def test_ipv6_loopback_is_an_allowed_local_destination(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **kw: [(10, 1, 6, "", ("::1", 8080, 0, 0))]
    )
    target = check_url(
        Policy(network_allow=("localhost",), network_ports=(8080,)), "http://localhost:8080"
    )
    assert target.address == "::1"


def test_explicit_zero_port_is_denied():
    with pytest.raises(SecurityError):
        check_url(Policy(network_allow=("127.0.0.1",), network_ports=(80,)), "http://127.0.0.1:0")
