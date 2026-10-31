from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_chat_database import service

from agent_harness.api import create_app
from agent_harness.models.chat import ConversationCreate, SendMessage
from agent_harness.services.database import ChatError

TOKEN = "synthetic-feedback-test-token-32-characters"  # noqa: S105 - fixture


def client_for(chat):
    return TestClient(
        create_app(chat, token=TOKEN, model_name="test"),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + TOKEN},
    )


def test_rating_persists_on_assistant_variant_and_can_be_cleared(chat_database):
    db, owner = chat_database
    chat = service(db, owner)
    conversation = chat.create(ConversationCreate())
    exchange = chat.send(conversation.id, SendMessage(content="hello"))
    path = f"/api/conversations/{conversation.id}/messages/{exchange.assistant.id}/rating"
    with client_for(chat) as client:
        for value in ("up", "down", None):
            response = client.patch(
                path, json={"variant_id": str(exchange.assistant.id), "rating": value}
            )
            assert response.status_code == 200
            assert response.json()["variants"][0]["rating"] == value
            assert chat.messages(conversation.id)[1].variants[0].rating == value
        assert (
            client.patch(
                path, json={"variant_id": str(exchange.assistant.id), "rating": "love"}
            ).status_code
            == 422
        )


def test_feedback_cannot_target_other_owners_user_messages_or_foreign_variants(chat_database):
    db, owner = chat_database
    chat = service(db, owner)
    cid = chat.create(ConversationCreate()).id
    exchange = chat.send(cid, SendMessage(content="private"))
    stranger_id = db.owner(f"stranger-{uuid4()}@localhost")
    try:
        stranger = service(db, stranger_id)
        with pytest.raises(ChatError) as failure:
            stranger.rate(cid, exchange.assistant.id, exchange.assistant.id, "up")
        assert failure.value.status_code == 404
        with pytest.raises(ChatError):
            chat.rate(cid, exchange.user.id, exchange.user.id, "up")
        with pytest.raises(ChatError):
            chat.rate(cid, exchange.assistant.id, uuid4(), "up")
    finally:
        with db.connect() as connection:
            connection.execute("DELETE FROM agent_chat.users WHERE id=%s", (stranger_id,))


def test_regeneration_preserves_turns_and_uses_only_context_before_target(chat_database):
    db, owner = chat_database
    prompts = []

    def answer(messages):
        prompts.append(messages)
        return {"final": f"answer-{len(prompts)}"}

    chat = service(db, owner, answer)
    cid = chat.create(ConversationCreate()).id
    first = chat.send(cid, SendMessage(content="first question"))
    chat.send(cid, SendMessage(content="later private question"))
    before = chat.messages(cid)
    with client_for(chat) as client:
        response = client.post(f"/api/conversations/{cid}/messages/{first.assistant.id}/regenerate")
        assert response.status_code == 200
        result = response.json()
        assert result["id"] == str(first.assistant.id)
        assert len(result["variants"]) == 2
        assert result["variants"][0]["content"][0]["text"] == "answer-1"
        assert result["variants"][1]["content"][0]["text"] == "answer-3"
        assert result["selected_variant"] == 1
        assert result["content"][0]["text"] == "answer-3"
        assert "later private question" not in str(prompts[-1])
        original = result["variants"][0]["id"]
        selected = client.patch(
            f"/api/conversations/{cid}/messages/{first.assistant.id}/selection",
            json={"variant_id": original},
        )
        assert selected.status_code == 200
        assert selected.json()["content"][0]["text"] == "answer-1"
    after = chat.messages(cid)
    assert [row.id for row in after] == [row.id for row in before]
    assert len(after[1].variants) == 2
    assert after[1].selected_variant == 0


def test_regeneration_failure_does_not_replace_saved_answer(chat_database):
    db, owner = chat_database
    chat = service(db, owner)
    cid = chat.create(ConversationCreate()).id
    exchange = chat.send(cid, SendMessage(content="hello"))

    def broken(messages):
        raise RuntimeError("synthetic inference failure")

    chat.agent_factory = service(db, owner, broken).agent_factory
    with pytest.raises(RuntimeError):
        chat.regenerate(cid, exchange.assistant.id)
    saved = chat.messages(cid)[1]
    assert saved.content[0].text == "hello"
    assert len(saved.variants) == 1


def test_historical_regeneration_does_not_recall_future_extracted_memories(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    cid = chat.create(ConversationCreate()).id
    first = chat.send(cid, SendMessage(content="first question"))

    class FutureMemories:
        def search(self, request):
            raise AssertionError("future memory retrieval during historical regeneration")

    chat.memory_service = FutureMemories()
    regenerated = chat.regenerate(cid, first.assistant.id)
    assert len(regenerated.variants) == 2
