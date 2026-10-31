from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_harness.api import create_app
from agent_harness.services.auth import AuthService
from agent_harness.services.database import ChatError

MASTER = "host-token-" + "a" * 40
PASSWORD = "local owner password 123"  # noqa: S105


@pytest.fixture
def auth(chat_database):
    database, owner = chat_database
    service = AuthService(database, owner)
    app = create_app(
        SimpleNamespace(database=database), token=MASTER, model_name="local", auth_service=service
    )
    with TestClient(app, base_url="http://localhost") as client:
        client.headers["Authorization"] = "Bearer " + MASTER
        yield service, client


def setup(client):
    return client.post("/api/auth/setup", json={"email": "Owner@Example.COM", "password": PASSWORD})


def test_setup_preserves_owner_and_stores_only_hashes(auth):
    service, client = auth
    assert client.get("/api/auth/status").json() == {"setup_required": True}
    result = setup(client)
    assert result.status_code == 200
    body = result.json()
    assert body["user"]["id"] == str(service.owner)
    assert body["user"]["email"] == "owner@example.com"
    with service.database.connect() as connection:
        account = connection.execute("SELECT * FROM agent_chat.local_account").fetchone()
        session = connection.execute("SELECT * FROM agent_chat.auth_sessions").fetchone()
    assert account["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=1$")
    assert PASSWORD not in str(account)
    assert body["session_token"] not in str(session)
    assert client.get("/api/auth/status").json() == {"setup_required": False}
    assert setup(client).status_code == 409


def test_sessions_cannot_bootstrap_or_reset_and_expire_revoke(auth):
    service, client = auth
    session = setup(client).json()["session_token"]
    client.headers["Authorization"] = "Bearer " + session
    assert client.get("/api/health").status_code == 200
    for path in ("/api/auth/status", "/api/auth/setup", "/api/auth/login"):
        response = (
            client.get(path)
            if path.endswith("status")
            else client.post(path, json={"email": "owner@example.com", "password": PASSWORD})
        )
        assert response.status_code == 401
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/health").status_code == 401
    client.headers["Authorization"] = "Bearer " + MASTER
    session = client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
    ).json()["session_token"]
    with service.database.connect() as connection:
        connection.execute(
            "UPDATE agent_chat.auth_sessions SET expires_at=now()-interval '1 second'"
        )
    client.headers["Authorization"] = "Bearer " + session
    assert client.get("/api/health").status_code == 401
    client.headers["Authorization"] = "Bearer session_" + "b" * 43
    assert client.get("/api/health").status_code == 401


def test_failed_login_never_issues_session(auth):
    service, client = auth
    setup(client)
    for email in ("owner@example.com", "someone@example.com"):
        response = client.post("/api/auth/login", json={"email": email, "password": "wrong"})
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid_credentials"}
    with service.database.connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM agent_chat.auth_sessions").fetchone()["n"]
            == 1
        )


def test_owner_isolation_and_atomic_setup(auth):
    service, client = auth
    from agent_harness.models.auth import SetupCredentials

    body = SetupCredentials(email="owner@example.com", password=PASSWORD)

    def attempt():
        try:
            return service.setup(body)
        except ChatError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(isinstance(result, int) and result == 409 for result in results) == 1
    successful = next(result for result in results if not isinstance(result, int))
    other = service.database.owner("isolated-owner@example.com")
    try:
        assert not AuthService(service.database, other).valid_session(successful.session_token)
    finally:
        with service.database.connect() as connection:
            connection.execute("DELETE FROM agent_chat.users WHERE id=%s", (other,))


def test_unauthenticated_cannot_setup_and_validation_is_sanitized(auth):
    _, client = auth
    del client.headers["Authorization"]
    assert setup(client).status_code == 401
    assert client.get("/api/auth/status").status_code == 401
    client.headers["Authorization"] = "Bearer " + MASTER
    for body in (
        {"email": "invalid", "password": PASSWORD},
        {"email": "owner@example.com", "password": "short"},
        {"email": "owner@example.com", "password": 123456789012},
        {"email": "owner@example.com", "password": PASSWORD, "user_id": "other"},
        {"email": "owner@example.com", "password": "x" * 1025},
    ):
        response = client.post("/api/auth/setup", json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "invalid_request"}


def test_rate_limit_is_bounded_and_never_issues_session(auth):
    service, client = auth
    for _ in range(10):
        assert (
            client.post(
                "/api/auth/login", json={"email": "owner@example.com", "password": "wrong"}
            ).status_code
            == 401
        )
    assert setup(client).status_code == 429
    assert len(service._attempts) == 10
    assert client.get("/api/auth/status").json() == {"setup_required": True}


def test_restart_resolves_existing_account_owner(auth):
    service, client = auth
    setup(client)
    assert service.database.local_owner() == service.owner
    client.headers["Authorization"] = "Bearer " + MASTER
    response = client.post(
        "/api/auth/login", json={"email": "OWNER@EXAMPLE.COM", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(service.owner)
