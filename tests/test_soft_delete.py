from uuid import uuid4

import pytest
from test_chat_database import service
from test_memory import Embeddings

from agent_harness.models.chat import (
    ConversationCreate,
    ConversationRename,
    ConversationSearch,
    SendMessage,
)
from agent_harness.models.execution import ApprovalDecision, CreateToolCall
from agent_harness.models.files import FileUpload
from agent_harness.models.memory import MemoryCreate
from agent_harness.services.database import ChatError
from agent_harness.services.files import FileService
from agent_harness.services.memory import MemoryService

pytest_plugins = ["test_execution"]


def test_soft_delete_preserves_history_and_rejects_chat_actions(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate(title="retained needle"))
    exchange = chat.send(conversation.id, SendMessage(content="retained needle"))
    with pytest.raises(ChatError, match="conversation_not_found"):
        service(database, uuid4()).delete(conversation.id)
    chat.delete(conversation.id)
    with database.connect() as db:
        row = db.execute(
            "SELECT deleted_at,updated_at FROM agent_chat.conversations WHERE id=%s",
            (conversation.id,),
        ).fetchone()
        assert row is not None and row["deleted_at"] is not None
        assert row["updated_at"] >= row["deleted_at"]
        assert (
            db.execute(
                "SELECT count(*) AS n FROM agent_chat.messages WHERE conversation_id=%s",
                (conversation.id,),
            ).fetchone()["n"]
            == 2
        )
        assert (
            db.execute(
                "SELECT count(*) AS n FROM agent_chat.message_variants WHERE message_id=%s",
                (exchange.assistant.id,),
            ).fetchone()["n"]
            == 1
        )
    assert chat.list() == []
    assert chat.search(ConversationSearch(query="needle")) == []
    assert chat.search(ConversationSearch(query="")) == []
    for operation in (
        lambda: chat.get(conversation.id),
        lambda: chat.messages(conversation.id),
        lambda: chat.send(conversation.id, SendMessage(content="no")),
        lambda: chat.rename(conversation.id, ConversationRename(title="no")),
        lambda: chat.rate(conversation.id, exchange.assistant.id, exchange.assistant.id, "up"),
        lambda: chat.select_variant(conversation.id, exchange.assistant.id, exchange.assistant.id),
        lambda: chat.regenerate(conversation.id, exchange.assistant.id),
    ):
        with pytest.raises(ChatError, match="conversation_not_found"):
            operation()


def test_deleted_chat_rejects_new_resources_but_retains_owned_resources(chat_database, tmp_path):
    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate())
    files = FileService(database, owner, tmp_path / "blobs")
    upload = FileUpload(conversation_id=conversation.id, filename="note.txt", content_base64="aGk=")
    stored = files.upload(upload)
    memories = MemoryService(database, owner, Embeddings())
    request = MemoryCreate(content="independent fact", source_conversation_id=conversation.id)
    memory = memories.create(request)
    with database.connect() as db:
        db.execute(
            "UPDATE agent_chat.conversations SET deleted_at=now() WHERE id=%s", (conversation.id,)
        )
    assert files.download(stored.id)[1] == b"hi"
    assert memories.get(memory.id).content == "independent fact"
    for operation in (
        lambda: files.upload(upload),
        lambda: memories.create(request),
        lambda: memories.extract(conversation.id),
        lambda: memories.compact(conversation.id),
    ):
        with pytest.raises(ChatError, match="conversation_not_found"):
            operation()


def test_deleted_chat_cannot_execute_pending_approval_or_new_tools(execution):
    calls, conversation, root, _ = execution
    request = CreateToolCall(
        conversation_id=conversation,
        tool="write_file",
        arguments={"path": "deleted.txt", "content": "forbidden"},
    )
    pending = calls.call(request)
    assert pending.status == "awaiting_approval"
    with calls.database.connect() as db:
        db.execute(
            "UPDATE agent_chat.conversations SET deleted_at=now() WHERE id=%s", (conversation,)
        )
    assert calls.get(pending.id).status == "awaiting_approval"
    with pytest.raises(ChatError, match="conversation_not_found"):
        calls.approve(pending.id, ApprovalDecision(decision="approve"))
    with pytest.raises(ChatError, match="conversation_not_found"):
        calls.call(request)
    assert not (root / "deleted.txt").exists()


def test_deleted_chat_observer_cannot_start_handler(execution):
    from agent_harness.core.errors import SecurityError

    calls, conversation, root, factory = execution
    observe, approve = calls.observer(conversation)
    service(calls.database, calls.owner).delete(conversation)
    executor = factory(observe=observe, approve=approve)
    with pytest.raises((ChatError, SecurityError)):
        executor.execute({"tool": "write_file", "arguments": {"path": "no.txt", "content": "no"}})
    assert calls.list(conversation_id=conversation) == []
    assert not (root / "no.txt").exists()


def test_chat_observer_uses_existing_lock_and_delete_fails_while_turn_runs(execution):
    from test_chat_database import Model

    from agent_harness.core.agents import LangGraphAgent
    from agent_harness.models.chat import SendMessage
    from agent_harness.services.chat import ChatService

    calls, conversation, _, factory = execution
    decisions = iter(
        [
            {"tool": "write_file", "arguments": {"path": "pending.txt", "content": "no"}},
            {"final": "pending approval"},
        ]
    )

    def decide(messages):
        with pytest.raises(ChatError, match="conversation_busy"):
            service(calls.database, calls.owner).delete(conversation)
        return next(decisions)

    chat = ChatService(
        calls.database,
        calls.owner,
        lambda: LangGraphAgent(Model(decide), factory()),
        execution_service=calls,
    )
    chat.send(conversation, SendMessage(content="attempt tool"))
    assert calls.list(conversation_id=conversation)[0].status == "awaiting_approval"
    chat.delete(conversation)
    assert calls.list(conversation_id=conversation)[0].status == "awaiting_approval"


def test_tool_claim_cannot_race_past_committed_chat_deletion(execution):
    calls, conversation, root, _ = execution
    call_id = calls._insert(
        conversation,
        {
            "tool": "write_file",
            "arguments": {"path": "no.txt", "content": "no"},
        },
    )
    service(calls.database, calls.owner).delete(conversation)
    assert calls._execute(call_id).status == "failed"
    assert not (root / "no.txt").exists()
