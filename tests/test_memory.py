import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_chat_database import service as chat_service

from agent_harness.core.policy import Policy
from agent_harness.models.chat import ConversationCreate, SendMessage
from agent_harness.models.memory import EmbeddingRequest, MemoryCreate, MemorySearch, MemoryUpdate
from agent_harness.services.database import ChatError
from agent_harness.services.embeddings import LocalEmbeddings
from agent_harness.services.memory import MemoryService


class Embeddings:
    model = "test-local-embeddings"

    def embed(self, texts, *, query=False):
        return [[1.0] + [0.0] * 1023 for _ in texts]


def test_embedding_strict_input_and_remote_bypass():
    with pytest.raises(ValidationError):
        EmbeddingRequest(texts=["ok"], endpoint="http://example.com")
    with pytest.raises(ValidationError):
        EmbeddingRequest(texts=[" "])
    with pytest.raises(ValueError):
        LocalEmbeddings(Policy(local_only=False), "http://127.0.0.1:11434")
    with pytest.raises(ChatError):
        LocalEmbeddings(Policy(), "http://8.8.8.8:11434").embed(["hello"])


@pytest.mark.parametrize("vector", [[1.0] * 1023, [0.0] * 1024, [float("nan")] * 1024])
def test_embedding_rejects_malformed_model_vectors(monkeypatch, vector):
    monkeypatch.setattr(
        "agent_harness.services.embeddings.transport",
        lambda *args, **kwargs: json.dumps({"embeddings": [vector]}).encode(),
    )
    with pytest.raises(ChatError):
        LocalEmbeddings(Policy(), "http://127.0.0.1:11434").embed(["hello"])


def test_embedding_real_loopback_protocol():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"embeddings": [[1.0] * 1024]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        embedding = LocalEmbeddings(Policy(), f"http://127.0.0.1:{server.server_port}")
        assert len(embedding.embed(["hello"], query=True)[0]) == 1024
        assert received == [
            (
                "/api/embed",
                {
                    "model": "mxbai-embed-large",
                    "input": ["Represent this sentence for searching relevant passages: hello"],
                    "truncate": False,
                },
            )
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_memory_owner_isolation_and_real_vector_search(chat_database):
    database, owner = chat_database
    memories = MemoryService(database, owner, Embeddings())
    foreign = MemoryService(database, uuid4(), Embeddings())
    memory = memories.create(MemoryCreate(content="I prefer tea"))
    assert memories.get(memory.id) == memory
    assert foreign.list() == []
    assert foreign.search(MemorySearch(query="tea")) == []
    for operation in (
        lambda: foreign.get(memory.id),
        lambda: foreign.update(memory.id, MemoryUpdate(content="stolen")),
        lambda: foreign.delete(memory.id),
    ):
        with pytest.raises(ChatError) as error:
            operation()
        assert error.value.status_code == 404
    assert memories.search(MemorySearch(query="tea"))[0].memory.id == memory.id
    updated = memories.update(memory.id, MemoryUpdate(content="I prefer coffee"))
    assert updated.content == "I prefer coffee"
    memories.delete(memory.id)
    assert memories.list() == []


def test_foreign_source_and_summary_marker_bypass(chat_database):
    database, owner = chat_database
    chat = chat_service(database, owner)
    conversation = chat.create(ConversationCreate())
    chat.send(conversation.id, SendMessage(content="I prefer tea"))
    foreign = MemoryService(database, uuid4(), Embeddings(), lambda messages, purpose: "evil")
    for operation in (
        lambda: foreign.extract(conversation.id),
        lambda: foreign.compact(conversation.id),
        lambda: foreign.create(
            MemoryCreate(content="stolen", source_conversation_id=conversation.id)
        ),
    ):
        with pytest.raises(ChatError) as error:
            operation()
        assert error.value.status_code == 404
    unchanged = chat.get(conversation.id)
    assert unchanged.summary is None
    assert unchanged.memory_processed_at is None
    assert unchanged.compacted_through_message_id is None


def test_extraction_compaction_preserve_history_and_failure_atomicity(chat_database):
    database, owner = chat_database
    chat = chat_service(database, owner)
    conversation = chat.create(ConversationCreate())
    exchange = chat.send(conversation.id, SendMessage(content="I prefer tea"))
    seen = []

    def summarize(messages, purpose):
        seen.append((messages, purpose))
        return '["Prefers tea"]' if purpose == "extract" else "User prefers tea."

    memories = MemoryService(database, owner, Embeddings(), summarize)
    extracted = memories.extract(conversation.id)
    assert extracted[0].source == "inferred"
    assert chat.get(conversation.id).memory_processed_at is not None
    compacted = memories.compact(conversation.id)
    assert compacted.summary == "User prefers tea."
    assert compacted.compacted_through_message_id == exchange.assistant.id
    assert len(chat.messages(conversation.id)) == 2
    assert seen[0][0][0] == {"role": "user", "content": "I prefer tea"}
    chat.send(conversation.id, SendMessage(content="I also prefer green tea"))
    marker = chat.get(conversation.id).memory_processed_at
    broken = MemoryService(
        database, owner, Embeddings(), lambda messages, purpose: '{"tool":"evil"}'
    )
    with pytest.raises(ChatError):
        broken.extract(conversation.id)
    assert chat.get(conversation.id).memory_processed_at == marker
    assert len(memories.list()) == 1


def test_deeply_nested_model_embedding_output_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "agent_harness.services.embeddings.transport",
        lambda *args, **kwargs: b'{"embeddings":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
    )
    with pytest.raises(ChatError) as error:
        LocalEmbeddings(Policy(), "http://127.0.0.1:11434").embed(["hello"])
    assert error.value.detail == "embedding_unavailable"


def test_search_ignores_other_embedding_spaces_and_skips_empty_inference(chat_database):
    database, owner = chat_database

    class RecordingEmbeddings(Embeddings):
        model = "first-local-model"

        def __init__(self):
            self.calls = 0

        def embed(self, texts, *, query=False):
            self.calls += 1
            return super().embed(texts, query=query)

    embedder = RecordingEmbeddings()
    memories = MemoryService(database, owner, embedder)
    assert memories.search(MemorySearch(query="tea")) == []
    assert embedder.calls == 0
    created = memories.create(MemoryCreate(content="I prefer tea"))
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT embedding_model FROM agent_chat.memories WHERE id=%s", (created.id,)
            ).fetchone()["embedding_model"]
            == embedder.model
        )
    assert memories.search(MemorySearch(query="tea"))[0].memory.id == created.id
    embedder.model = "second-local-model"
    calls = embedder.calls
    assert memories.search(MemorySearch(query="tea")) == []
    assert embedder.calls == calls
    memories.update(created.id, MemoryUpdate(content="I prefer green tea"))
    assert memories.search(MemorySearch(query="tea"))[0].memory.id == created.id
    with database.connect() as connection:
        connection.execute(
            "UPDATE agent_chat.memories SET embedding_model=NULL WHERE id=%s", (created.id,)
        )
    calls = embedder.calls
    assert memories.search(MemorySearch(query="tea")) == []
    assert embedder.calls == calls


def test_extraction_only_processes_new_messages_and_retries_failures(chat_database):
    database, owner = chat_database
    chat = chat_service(database, owner)
    conversation = chat.create(ConversationCreate())
    chat.send(conversation.id, SendMessage(content="I prefer tea"))
    calls = []

    def summarize(messages, purpose):
        calls.append(messages)
        return '["Prefers tea"]'

    memories = MemoryService(database, owner, Embeddings(), summarize)
    assert len(memories.extract(conversation.id)) == 1
    marker = chat.get(conversation.id).memory_processed_at
    assert memories.extract(conversation.id) == []
    assert len(calls) == 1
    assert len(memories.list()) == 1
    assert chat.get(conversation.id).memory_processed_at == marker
    chat.send(conversation.id, SendMessage(content="I also prefer green tea"))
    failing = MemoryService(
        database, owner, Embeddings(), lambda messages, purpose: '{"tool":"evil"}'
    )
    with pytest.raises(ChatError):
        failing.extract(conversation.id)
    assert chat.get(conversation.id).memory_processed_at == marker
    assert len(memories.extract(conversation.id)) == 1
    assert len(calls) == 2
    assert calls[-1] == [
        {"role": "user", "content": "I also prefer green tea"},
        {"role": "assistant", "content": "hello"},
    ]
    assert len(memories.list()) == 2
