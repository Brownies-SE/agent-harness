from uuid import UUID, uuid4

import pytest

from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy
from agent_harness.models.execution import ApprovalDecision, CreateToolCall
from agent_harness.services.database import ChatError
from agent_harness.services.execution import ExecutionService
from agent_harness.tools import builtin_tools


@pytest.fixture
def execution(chat_database, tmp_path):
    database, owner = chat_database
    with database.connect() as db:
        conversation = db.execute(
            "INSERT INTO agent_chat.conversations(user_id,title) "
            "VALUES (%s,'execution') RETURNING id",
            (owner,),
        ).fetchone()["id"]
    policy = Policy(roots=(tmp_path,), rules={"filesystem.write": "ask"})

    def factory(**kwargs):
        executor = Executor(policy, **kwargs)
        for tool in builtin_tools(policy):
            executor.register(tool)
        return executor

    return ExecutionService(database, owner, factory), conversation, tmp_path, factory


def test_explicit_approval_is_single_use_and_owner_scoped(execution):
    service, conversation, root, factory = execution
    call = service.call(
        CreateToolCall(
            conversation_id=conversation,
            tool="write_file",
            arguments={"path": "note.txt", "content": "approved"},
        )
    )
    assert call.status == "awaiting_approval"
    assert not (root / "note.txt").exists()
    stranger = ExecutionService(service.database, uuid4(), factory)
    with pytest.raises(ChatError, match="tool_call_not_found"):
        stranger.approve(call.id, ApprovalDecision(decision="approve"))
    approved = service.approve(call.id, ApprovalDecision(decision="approve"))
    assert approved.status == "succeeded"
    assert (root / "note.txt").read_text() == "approved"
    with pytest.raises(ChatError, match="not_awaiting_approval"):
        service.approve(call.id, ApprovalDecision(decision="approve"))


def test_approval_cannot_bypass_changed_host_deny_or_resource(execution, tmp_path):
    service, conversation, root, _ = execution
    pending = service.call(
        CreateToolCall(
            conversation_id=conversation,
            tool="write_file",
            arguments={"path": "note.txt", "content": "forbidden"},
        )
    )
    denied_policy = Policy(roots=(root,), rules={"filesystem.write": "deny"})

    def deny_factory(**kwargs):
        executor = Executor(denied_policy, **kwargs)
        for tool in builtin_tools(denied_policy):
            executor.register(tool)
        return executor

    service.executor_factory = deny_factory
    result = service.approve(pending.id, ApprovalDecision(decision="approve"))
    assert result.status == "denied"
    assert not (root / "note.txt").exists()


def test_failed_calls_are_durable_and_unknown_permissions_rejected(execution):
    service, conversation, root, _ = execution
    result = service.call(
        CreateToolCall(
            conversation_id=conversation,
            tool="write_file",
            arguments={"path": "../escape.txt", "content": "bad"},
        )
    )
    assert result.status == "denied"
    assert service.get(result.id).status == "denied"
    assert not (root.parent / "escape.txt").exists()
    with pytest.raises(ChatError, match="unknown_tool"):
        service.set_permission("invented", "allow")


def test_database_failure_prevents_handler(execution, monkeypatch):
    from contextlib import contextmanager

    service, conversation, root, _ = execution

    @contextmanager
    def unavailable():
        raise ChatError("storage_unavailable")
        yield

    monkeypatch.setattr(service.database, "connect", unavailable)
    with pytest.raises(ChatError):
        service.call(
            CreateToolCall(
                conversation_id=conversation,
                tool="write_file",
                arguments={"path": "note.txt", "content": "bad"},
            )
        )
    assert not (root / "note.txt").exists()


def test_approval_rechecks_resource_after_directory_replaced_with_symlink(
    execution, tmp_path_factory
):
    service, conversation, root, _ = execution
    directory = root / "folder"
    directory.mkdir()
    pending = service.call(
        CreateToolCall(
            conversation_id=conversation,
            tool="write_file",
            arguments={"path": "folder/note.txt", "content": "forbidden"},
        )
    )
    assert pending.status == "awaiting_approval"
    outside = tmp_path_factory.mktemp("outside")
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)
    approved = service.approve(pending.id, ApprovalDecision(decision="approve"))
    assert approved.status == "denied"
    assert not (outside / "note.txt").exists()


def test_owner_isolation_and_explicit_denial(execution):
    service, conversation, root, factory = execution
    pending = service.call(
        CreateToolCall(
            conversation_id=conversation,
            tool="write_file",
            arguments={"path": "note.txt", "content": "forbidden"},
        )
    )
    stranger = ExecutionService(service.database, uuid4(), factory)
    assert stranger.list() == []
    with pytest.raises(ChatError, match="tool_call_not_found"):
        stranger.get(pending.id)
    with pytest.raises(ChatError, match="conversation_not_found"):
        stranger.call(
            CreateToolCall(
                conversation_id=conversation,
                tool="write_file",
                arguments={"path": "note.txt", "content": "forbidden"},
            )
        )
    assert service.approve(pending.id, ApprovalDecision(decision="deny")).status == "denied"
    assert not (root / "note.txt").exists()


def test_chat_callbacks_record_run_and_link_message(execution):
    from agent_harness.core.errors import SecurityError
    from agent_harness.core.runtime import RunControl

    service, conversation, root, factory = execution
    observe, approve = service.observer(conversation)
    executor = factory(observe=observe, approve=approve)
    run = RunControl()
    with run.activate(), pytest.raises(SecurityError, match="approval_denied"):
        executor.execute(
            {"tool": "write_file", "arguments": {"path": "note.txt", "content": "pending"}}
        )
    record = service.list(conversation_id=conversation)[0]
    assert record.run_id == UUID(run.id)
    assert record.status == "awaiting_approval"
    with service.database.connect() as db:
        message_id = db.execute(
            "INSERT INTO agent_chat.messages(conversation_id,seq,role,content) "
            "VALUES (%s,1,'assistant','[]') RETURNING id",
            (conversation,),
        ).fetchone()["id"]
    service.link_run(run.id, message_id)
    assert service.get(record.id).message_id == message_id
    assert not (root / "note.txt").exists()


def test_handler_failure_log_uses_fixed_error_and_no_raw_exception(execution):
    from agent_harness.core.executor import Tool

    service, conversation, _, _ = execution

    def fail():
        raise RuntimeError("private-handler-detail")

    def factory(**kwargs):
        executor = Executor(Policy(), **kwargs)
        executor.register(Tool("fail", {"type": "object", "additionalProperties": False}, (), fail))
        return executor

    service.executor_factory = factory
    result = service.call(CreateToolCall(conversation_id=conversation, tool="fail", arguments={}))
    assert result.status == "failed"
    assert result.error == "tool_failed"
    assert "private-handler-detail" not in result.model_dump_json()


def test_stored_allow_never_bypasses_host_ask_and_deny_restricts_approval(execution):
    service, conversation, root, _ = execution
    service.set_permission("write_file", "allow")
    pending = service.call(
        CreateToolCall(
            conversation_id=conversation,
            tool="write_file",
            arguments={"path": "note.txt", "content": "forbidden"},
        )
    )
    assert pending.status == "awaiting_approval"
    service.set_permission("write_file", "deny")
    assert service.approve(pending.id, ApprovalDecision(decision="approve")).status == "denied"
    assert not (root / "note.txt").exists()


def test_original_denial_completion_cannot_overwrite_independent_approval(execution):
    service, conversation, root, _ = execution
    observe, approve = service.observer(conversation)
    request = {"tool": "write_file", "arguments": {"path": str(root / "race.txt"), "content": "ok"}}
    observe({"phase": "started", "request": request})
    assert approve({**request, "capabilities": ("filesystem.write",)}) == "deny"
    pending = service.list(conversation_id=conversation)[0]
    completed = service.approve(pending.id, ApprovalDecision(decision="approve"))
    assert completed.status == "succeeded"
    observe(
        {
            "phase": "finished",
            "request": None,
            "result": None,
            "status": "denied",
            "reason": "approval_denied",
        }
    )
    assert service.get(pending.id).status == "succeeded"
    assert service.get(pending.id).output == completed.output


def test_host_lifecycle_observer_is_durable_without_conversation(execution):
    from agent_harness.core.executor import Tool

    service, _, _, _ = execution
    observe, approve = service.observer(None)
    executor = Executor(Policy(), observe=observe, approve=approve)
    executor.register(
        Tool(
            "host_lifecycle",
            {"type": "object", "additionalProperties": False},
            (),
            lambda: {"connected": True},
        )
    )
    executor.execute({"tool": "host_lifecycle", "arguments": {}})
    record = service.list()[0]
    assert record.conversation_id is None
    assert record.status == "succeeded"
    assert record.output == {"connected": True}


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("failure", ["security", "exception"])
def test_old_attempt_fallback_cannot_fail_newly_claimed_approval(execution, explicit, failure):
    from agent_harness.core.errors import SecurityError

    service, conversation, root, _ = execution
    call_id = service._insert(
        conversation,
        {
            "tool": "write_file",
            "arguments": {"path": str(root / "race.txt"), "content": "pending"},
        },
    )

    def factory(**kwargs):
        # Another request claims approval while this older attempt is returning its denial.
        with service.database.connect() as db:
            db.execute(
                "UPDATE agent_chat.tool_calls SET status='running',approved_at=now() "
                "WHERE id=%s AND user_id=%s",
                (call_id, service.owner),
            )
        if failure == "security":
            raise SecurityError("approval_denied")
        raise RuntimeError("private-factory-error")

    service.executor_factory = factory
    result = service._execute(call_id, explicit=explicit)
    assert result.status == ("failed" if explicit else "running")
    assert "private-factory-error" not in result.model_dump_json()
    assert not (root / "race.txt").exists()
