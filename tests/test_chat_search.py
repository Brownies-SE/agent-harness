from uuid import uuid4

from fastapi.testclient import TestClient
from test_chat_database import service

from agent_harness.api import create_app
from agent_harness.models.chat import ConversationCreate, SendMessage

TOKEN = "synthetic-search-regression-token-32-characters"  # noqa: S105 - fixture


def client_for(chat):
    return TestClient(
        create_app(chat, token=TOKEN, model_name="test"),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + TOKEN},
    )


def test_search_finds_saved_titles_and_message_bodies_with_context(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    titled = chat.create(ConversationCreate(title="Amber project design"))
    body = chat.create(ConversationCreate(title="Other discussion"))
    turn = chat.send(body.id, SendMessage(content="The cobalt notebook is on the shelf."))
    with client_for(chat) as client:
        response = client.post("/api/conversations/search", json={"query": "AMBER"})
        assert response.status_code == 200
        assert [hit["conversation"]["id"] for hit in response.json()] == [str(titled.id)]
        response = client.post("/api/conversations/search", json={"query": "cobalt notebook"})
        assert response.status_code == 200
        hit = response.json()[0]
        assert hit["conversation"]["id"] == str(body.id)
        assert hit["matched_message_id"] == str(turn.user.id)
        assert hit["matched_seq"] == 1
        assert "cobalt notebook" in hit["snippet"]
        assert len(hit["snippet"]) <= 400
        assert len(client.post("/api/conversations/search", json={"query": ""}).json()) == 2


def test_search_is_owner_scoped_and_treats_patterns_as_literal(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    stranger_id = database.owner(f"search-stranger-{uuid4()}@localhost")
    try:
        stranger = service(database, stranger_id)
        private = stranger.create(ConversationCreate(title="Secret search treasury"))
        stranger.send(private.id, SendMessage(content="exclusive searchword"))
        chat.create(ConversationCreate(title="100%_literal"))
        with client_for(chat) as client:
            for query in ("treasury", "exclusive searchword", "' OR true --", "no-match"):
                response = client.post("/api/conversations/search", json={"query": query})
                assert response.status_code == 200
                assert response.json() == []
            assert len(client.post("/api/conversations/search", json={"query": "%_"}).json()) == 1
            assert len(client.post("/api/conversations/search", json={"query": "%"}).json()) == 1
            assert (
                len(
                    client.post(
                        "/api/conversations/search", json={"query": "", "limit": 1, "offset": 1}
                    ).json()
                )
                == 0
            )
    finally:
        with database.connect() as connection:
            connection.execute("DELETE FROM agent_chat.users WHERE id=%s", (stranger_id,))


def test_search_rejects_unbounded_requests_and_needs_auth(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    with client_for(chat) as client:
        for body in (
            {"query": "x" * 201},
            {"query": "x\x00"},
            {"query": "hello", "limit": 0},
            {"query": "hello", "limit": 101},
            {"query": "x", "offset": -1},
            {"query": True},
            {"query": "hi", "user_id": str(owner)},
        ):
            assert client.post("/api/conversations/search", json=body).status_code == 422
        assert (
            client.post(
                "/api/conversations/search",
                json={"query": "x"},
                headers={"Authorization": "Bearer invalid"},
            ).status_code
            == 401
        )
