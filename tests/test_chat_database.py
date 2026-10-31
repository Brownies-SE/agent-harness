import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from agent_harness.agents import LangGraphAgent
from agent_harness.errors import SecurityError
from agent_harness.executor import Executor
from agent_harness.models.chat import ConversationCreate, ConversationRename, SendMessage
from agent_harness.policy import Policy
from agent_harness.services.chat import ChatService
from agent_harness.services.database import ChatError, Database


class Model:
    def __init__(self, callback=lambda messages: {"final": "hello"}):
        self.callback = callback

    def decide(self, messages, tools):
        return self.callback(messages)


def service(database, owner, callback=lambda messages: {"final": "hello"}):
    return ChatService(database, owner, lambda: LangGraphAgent(Model(callback), Executor(Policy())))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:password@example.com/chat",
        "postgresql://user:password@127.0.0.1/chat?hostaddr=8.8.8.8",
        "postgresql://user:password@127.0.0.1/chat?service=remote",
        "postgresql://user:password@127.0.0.1/chat?options=-c%20search_path=public",
        "postgresql://user:password@localhost/chat",
    ],
)
def test_database_cannot_escape_loopback_or_load_service_configuration(url):
    with pytest.raises(ValueError):
        Database(url)


def test_conversations_and_messages_survive_service_restart(chat_database):
    database, owner = chat_database
    first = service(database, owner)
    conversation = first.create(ConversationCreate(title="First chat"))
    exchange = first.send(conversation.id, SendMessage(content="Hi"))
    second = service(Database(database.url), owner)
    assert second.get(conversation.id).title == "First chat"
    assert second.list()[0].id == conversation.id
    messages = second.messages(conversation.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert [m.seq for m in messages] == [1, 2]
    assert messages[0].content[0].text == "Hi"
    assert messages[1].content[0].text == "hello"
    assert exchange.assistant.id == messages[1].id
    second.rename(conversation.id, ConversationRename(title="Renamed"))
    assert second.get(conversation.id).title == "Renamed"
    second.delete(conversation.id)
    assert second.list() == []
    with pytest.raises(ChatError, match="conversation_not_found"):
        second.messages(conversation.id)
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM agent_chat.messages WHERE conversation_id=%s",
                (conversation.id,),
            ).fetchone()["n"]
            == 0
        )


def test_failed_turn_rolls_back_pair_without_replaying_tools(chat_database):
    database, owner = chat_database

    def fail(messages):
        raise SecurityError("invalid_model_response")

    chat = service(database, owner, fail)
    conversation = chat.create(ConversationCreate())
    with pytest.raises(SecurityError):
        chat.send(conversation.id, SendMessage(content="fail"))
    assert chat.messages(conversation.id) == []
    recovered = service(database, owner).send(conversation.id, SendMessage(content="ok"))
    assert recovered.user.seq == 1


def test_assistant_storage_failure_rolls_back_user_message(chat_database):
    database, owner = chat_database
    chat = service(database, owner, lambda messages: {"final": "unstoreable\x00text"})
    conversation = chat.create(ConversationCreate())
    with pytest.raises(ChatError, match="chat_storage_unavailable"):
        chat.send(conversation.id, SendMessage(content="not partially saved"))
    assert chat.messages(conversation.id) == []


def test_context_is_bounded_but_saved_history_is_not_deleted(chat_database):
    database, owner = chat_database
    contexts = []

    def answer(messages):
        contexts.append(messages)
        return {"final": "saved answer"}

    chat = service(database, owner, answer)
    conversation = chat.create(ConversationCreate())
    for i in range(10):
        chat.send(conversation.id, SendMessage(content=f"message {i}"))
    assert len(chat.messages(conversation.id)) == 20
    assert len(contexts[-1]) == 18
    assert contexts[-1][1]["content"] == "message 1"
    assert json.loads(contexts[-1][2]["content"]) == {"final": "saved answer"}
    assert [m.seq for m in chat.messages(conversation.id, after_seq=16, limit=2)] == [17, 18]


def test_owner_isolation_applies_to_every_operation(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate())
    from uuid import uuid4

    stranger = service(database, uuid4())
    assert stranger.list() == []
    for operation in (
        lambda: stranger.get(conversation.id),
        lambda: stranger.messages(conversation.id),
        lambda: stranger.send(conversation.id, SendMessage(content="intrude")),
        lambda: stranger.rename(conversation.id, ConversationRename(title="intrude")),
        lambda: stranger.delete(conversation.id),
    ):
        with pytest.raises(ChatError, match="conversation_not_found"):
            operation()
    assert chat.get(conversation.id).title == "New chat"


def test_concurrent_sends_fail_fast_without_interleaving(chat_database):
    database, owner = chat_database
    entered, release = Event(), Event()

    def wait(messages):
        entered.set()
        assert release.wait(5)
        return {"final": "done"}

    chat = service(database, owner, wait)
    conversation = chat.create(ConversationCreate())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(chat.send, conversation.id, SendMessage(content="first"))
        try:
            assert entered.wait(5)
            with pytest.raises(ChatError, match="conversation_busy"):
                service(database, owner).send(conversation.id, SendMessage(content="second"))
        finally:
            release.set()
        pending.result()
    assert [m.content[0].text for m in chat.messages(conversation.id)] == ["first", "done"]
