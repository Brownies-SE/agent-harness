import json
from threading import Event

import pytest
from fastapi.testclient import TestClient

from agent_harness.api import create_app
from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor, Tool
from agent_harness.core.policy import Policy
from agent_harness.core.runtime import RunStopped, check_run
from agent_harness.models.chat import ConversationCreate, SendMessage
from agent_harness.services.chat import ChatService

TOKEN = "synthetic-streaming-token-at-least-32-characters"  # noqa: S105


class StreamModel:
    def decide(self, messages, tools):
        raise AssertionError("stream must consume incremental provider output")

    def decide_stream(self, messages, tools, on_text):
        on_text("Hello ")
        on_text("world")
        return {"final": "Hello world"}


def service(database, owner, model=None, executor=None):
    return ChatService(
        database,
        owner,
        lambda: LangGraphAgent(model or StreamModel(), executor or Executor(Policy())),
    )


def test_streaming_proposals_cannot_bypass_deny_or_invoke_unknown_handlers():
    called = []
    executor = Executor(Policy(rules={"process.write": "deny"}))
    executor.register(
        Tool(
            "write",
            {"type": "object", "additionalProperties": False},
            ("process.write",),
            lambda **kw: called.append(kw),
        )
    )

    class Proposals:
        calls = 0

        def decide_stream(self, messages, tools, on_text):
            self.calls += 1
            if self.calls == 1:
                return {"tool": "write", "arguments": {}}
            if self.calls == 2:
                return {"tool": "unknown", "arguments": {}}
            on_text("Done")
            return {"final": "Done"}

    agent = LangGraphAgent(Proposals(), executor)
    agent.on_text = lambda text: None
    assert agent.run("test") == "Done"
    assert called == []
    assert agent.last_run.denied_tools == 2


def test_streaming_send_cancels_and_rolls_back_provisional_text(chat_database):
    database, owner = chat_database
    cancel = Event()
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate())
    chunks = []

    def receive(text):
        chunks.append(text)
        cancel.set()
        check_run()

    with pytest.raises(RunStopped, match="run_cancelled"):
        chat.send(conversation.id, SendMessage(content="hello"), on_text=receive, cancel=cancel)
    assert chunks == ["Hello "]
    assert chat.messages(conversation.id) == []


def test_streaming_api_saves_before_complete_and_preserves_normal_api(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate())
    app = create_app(chat, token=TOKEN, model_name="test")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        path = f"/api/conversations/{conversation.id}/messages/stream"
        assert client.post(path, json={"content": "hello"}).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        assert (
            client.post(
                path, headers=headers, json={"content": "hello", "approved": True}
            ).status_code
            == 422
        )
        response = client.post(path, headers=headers, json={"content": "hello"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert "".join(row["text"] for row in events if row["type"] == "delta") == "Hello world"
        assert events[-1]["type"] == "complete"
        assert events[-1]["exchange"]["assistant"]["id"] == str(
            chat.messages(conversation.id)[1].id
        )
        chat.delete(conversation.id)
        assert client.post(path, headers=headers, json={"content": "hello"}).status_code == 404
        other = database.owner("stream-foreign@localhost")
        foreign = service(database, other).create(ConversationCreate())
        try:
            assert (
                client.post(
                    f"/api/conversations/{foreign.id}/messages/stream",
                    headers=headers,
                    json={"content": "hello"},
                ).status_code
                == 404
            )
        finally:
            with database.connect() as connection:
                connection.execute("DELETE FROM agent_chat.users WHERE id=%s", (other,))


def test_stream_error_is_sanitized_and_not_persisted(chat_database):
    class Broken(StreamModel):
        def decide_stream(self, messages, tools, on_text):
            on_text("provisional")
            raise SecurityError("secret-provider-result")

    database, owner = chat_database
    chat = service(database, owner, Broken())
    conversation = chat.create(ConversationCreate())
    app = create_app(chat, token=TOKEN, model_name="test")
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + TOKEN}
    ) as client:
        response = client.post(
            f"/api/conversations/{conversation.id}/messages/stream", json={"content": "hello"}
        )
    assert '"type":"error"' in response.text
    assert "secret-provider-result" not in response.text
    assert chat.messages(conversation.id) == []


def test_stream_disconnect_cancels_worker_releases_slot_and_keeps_history_atomic(chat_database):
    import asyncio
    import time

    from agent_harness.services.database import ChatError
    from agent_harness.services.streaming import prepare_stream

    class Waiting(StreamModel):
        def decide_stream(self, messages, tools, on_text):
            on_text("provisional")
            while True:
                check_run()
                time.sleep(0.01)

    database, owner = chat_database
    chat = service(database, owner, Waiting())
    conversation = chat.create(ConversationCreate())
    body = SendMessage(content="hello")
    stream = prepare_stream(chat, conversation.id, body)
    with pytest.raises(ChatError, match="conversation_busy"):
        prepare_stream(chat, conversation.id, body)

    async def disconnect():
        iterator = stream.iterate()
        assert b'"type":"delta"' in await anext(iterator)
        await iterator.aclose()

    asyncio.run(disconnect())
    stream.thread.join(timeout=2)
    assert not stream.thread.is_alive()
    assert chat.messages(conversation.id) == []
    assert chat.stream_slots.acquire(blocking=False)
    chat.stream_slots.release()


def test_stream_documentation_references_resolvable_typed_events(chat_database):
    database, owner = chat_database
    app = create_app(service(database, owner), token=TOKEN, model_name="test")
    schema = app.openapi()
    response = schema["paths"]["/api/conversations/{conversation_id}/messages/stream"]["post"][
        "responses"
    ]["200"]
    event_schema = response["content"]["text/event-stream"]["schema"]
    assert event_schema.get("type") == "object"
    for model in event_schema["anyOf"]:
        name = model["$ref"].split("/")[-1]
        assert name in schema["components"]["schemas"]
    assert "Exchange" in schema["components"]["schemas"]


def test_abandoned_stream_response_releases_reservation_before_worker_starts(chat_database):
    from agent_harness.services.streaming import prepare_stream

    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate())
    stream = prepare_stream(chat, conversation.id, SendMessage(content="hello"))
    stream.close()
    stream.close()  # Cleanup must be idempotent.
    assert chat.stream_slots.acquire(blocking=False)
    chat.stream_slots.release()


def test_cancel_during_storage_writes_rolls_back_before_commit(chat_database, monkeypatch):
    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate())
    cancel = Event()
    insert = chat._insert

    def interrupt(*args, **kwargs):
        message = insert(*args, **kwargs)
        if message.role == "assistant":
            cancel.set()
        return message

    monkeypatch.setattr(chat, "_insert", interrupt)
    with pytest.raises(RunStopped, match="run_cancelled"):
        chat.send(
            conversation.id, SendMessage(content="hello"), on_text=lambda text: None, cancel=cancel
        )
    assert chat.messages(conversation.id) == []


def test_settled_history_rejects_active_generation_then_returns_committed_snapshot(chat_database):
    from concurrent.futures import ThreadPoolExecutor

    from agent_harness.services.database import ChatError

    started = Event()
    release = Event()

    class Waiting(StreamModel):
        def decide_stream(self, messages, tools, on_text):
            started.set()
            assert release.wait(3)
            return {"final": "saved"}

    database, owner = chat_database
    chat = service(database, owner, Waiting())
    conversation = chat.create(ConversationCreate())
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(
            chat.send, conversation.id, SendMessage(content="hello"), on_text=lambda text: None
        )
        try:
            assert started.wait(2)
            with pytest.raises(ChatError, match="conversation_busy"):
                chat.messages(conversation.id, settled=True)
        finally:
            release.set()
        pending.result(timeout=3)
    assert len(chat.messages(conversation.id, settled=True)) == 2
