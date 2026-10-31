import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy
from agent_harness.models.mcp import ConnectionCreate, ServerCreate
from agent_harness.services.database import ChatError
from agent_harness.services.mcp import MCPService
from agent_harness.tools.mcp import MCPPermission


@pytest.fixture
def endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({**body, "_authorization": self.headers.get("Authorization")})
            method = body["method"]
            result = {
                "initialize": {"protocolVersion": "2025-03-26", "capabilities": {}},
                "tools/list": {
                    "tools": [
                        {
                            "name": "echo",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        },
                        {
                            "name": "unmapped",
                            "inputSchema": {"type": "object", "additionalProperties": False},
                        },
                    ]
                },
                "tools/call": {"content": [{"type": "text", "text": "echoed"}]},
            }.get(method)
            if "id" not in body:
                self.send_response(202)
                self.end_headers()
                return
            response = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(("data: " + response + "\n\n").encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp", requests
    server.shutdown()
    server.server_close()
    worker.join()


def make_service(chat_database, endpoint, *, allow=True, mapped=True):
    database, owner = chat_database
    url, _ = endpoint
    from urllib.parse import urlsplit

    policy = Policy(
        network_allow=("127.0.0.1",),
        network_ports=(urlsplit(url).port,),
        rules={"network": "allow" if allow else "deny", "process.read": "allow"},
    )
    name = "fixture" + uuid4().hex[:12]
    permissions = {name: {"echo": MCPPermission(("process.read",))}} if mapped else {}
    service = MCPService(
        database,
        owner,
        policy,
        Fernet.generate_key(),
        permissions=permissions,
        endpoints={name: url},
    )
    server = service.create_server(ServerCreate(name=name, url=url, auth_type="api_key"))
    connection = service.create_connection(
        ConnectionCreate(server_id=server.id, access_token="test-secret")  # noqa: S106
    )
    return service, server, connection


def test_denied_connection_never_contacts_server(chat_database, endpoint):
    service, server, connection = make_service(chat_database, endpoint, allow=False)
    with pytest.raises(ChatError):
        service.refresh(connection.id)
    assert endpoint[1] == []
    service.delete_server(server.id)


def test_real_mcp_rpc_mapping_encryption_and_owner_isolation(chat_database, endpoint):
    service, server, connection = make_service(chat_database, endpoint)
    try:
        assert connection.has_access_token
        assert "test-secret" not in connection.model_dump_json()
        with service.database.connect() as db:
            stored = db.execute(
                "SELECT access_token_enc FROM agent_chat.mcp_connections WHERE id=%s",
                (connection.id,),
            ).fetchone()["access_token_enc"]
        assert b"test-secret" not in bytes(stored)
        tools = service.refresh(connection.id)
        assert {tool.name for tool in tools} == {"echo", "unmapped"}
        executor = Executor(service.policy)
        service.register(executor)
        assert executor.execute({"tool": f"mcp.{server.name}.echo", "arguments": {"text": "hello"}})
        with pytest.raises(SecurityError, match="unknown_tool"):
            executor.execute({"tool": f"mcp.{server.name}.unmapped", "arguments": {}})
        assert endpoint[1][-1]["method"] == "tools/call"
        assert endpoint[1][-1]["params"]["name"] == "echo"
        stranger = MCPService(service.database, uuid4(), service.policy, service.encryption_key)
        for operation in (
            lambda: stranger.get_connection(connection.id),
            lambda: stranger.refresh(connection.id),
            lambda: stranger.delete_connection(connection.id),
        ):
            with pytest.raises(ChatError, match="mcp_connection_not_found"):
                operation()
    finally:
        service.delete_server(server.id)


def test_unreviewed_server_never_automatically_connects(chat_database, endpoint):
    service, server, _ = make_service(chat_database, endpoint, mapped=False)
    try:
        service.register(Executor(service.policy))
        assert endpoint[1] == []
    finally:
        service.delete_server(server.id)


def test_http_transport_runs_real_protocol_without_database(endpoint):
    from urllib.parse import urlsplit

    from agent_harness.services.mcp_transport import MCPHTTPClient
    from agent_harness.tools.mcp import MCPAdapter

    url, requests = endpoint
    policy = Policy(
        network_allow=("127.0.0.1",),
        network_ports=(urlsplit(url).port,),
        rules={"network": "allow", "process.read": "allow"},
    )
    executor = Executor(policy)
    adapter = MCPAdapter(executor, "fixture", MCPHTTPClient, endpoint=url)
    adapter.connect({"echo": MCPPermission(("process.read",))})
    result = executor.execute({"tool": "mcp.fixture.echo", "arguments": {"text": "hello"}})
    assert result["content"][0]["text"] == "echoed"
    assert [request["method"] for request in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert requests[-1]["params"] == {"name": "echo", "arguments": {"text": "hello"}}
    with pytest.raises(SecurityError, match="unknown_tool"):
        executor.execute({"tool": "mcp.fixture.unmapped", "arguments": {}})


def test_expired_connection_and_missing_key_fail_without_network(chat_database, endpoint):
    from datetime import datetime, timedelta, timezone

    service, server, connection = make_service(chat_database, endpoint)
    try:
        expired = service.create_connection(
            ConnectionCreate(
                server_id=server.id,
                access_token="fixture-access",  # noqa: S106
                refresh_token="fixture-refresh",  # noqa: S106
                token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        assert expired.status == "needs_reauth"
        assert expired.has_refresh_token
        assert "fixture-refresh" not in expired.model_dump_json()
        with pytest.raises(ChatError, match="needs_reauth"):
            service.check(connection.id)
        without_key = MCPService(service.database, service.owner, service.policy, None)
        with pytest.raises(ChatError, match="credential_key_unconfigured"):
            without_key.create_connection(
                ConnectionCreate(server_id=server.id, access_token="synthetic")  # noqa: S106
            )
        assert endpoint[1] == []
    finally:
        service.delete_server(server.id)


def test_catalog_disallows_unlisted_and_nonlocal_endpoints_without_contact(endpoint):
    url, requests = endpoint
    service = MCPService(None, uuid4(), Policy(), Fernet.generate_key())
    with pytest.raises(ChatError, match="endpoint_denied"):
        service.create_server(ServerCreate(name="unlisted", url=url))
    assert requests == []


@pytest.mark.parametrize("transport", ["stdio", "sse"])
def test_unconfigured_transports_fail_explicitly(transport):
    service = MCPService(None, uuid4(), Policy(), None)
    with pytest.raises(ChatError, match="requires_reviewed_host_command|transport_unconfigured"):
        service.create_server(
            ServerCreate(name="unsupported", url="http://127.0.0.1", transport=transport)
        )


def test_credentials_are_not_exposed_by_routes(chat_database, endpoint):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_harness.routes.mcp import router

    service, server, connection = make_service(chat_database, endpoint)
    app = FastAPI()
    app.state.mcp_service = service
    app.include_router(router)
    try:
        with TestClient(app) as client:
            assert client.get("/api/mcp/connections").status_code == 401
            result = client.get(
                f"/api/mcp/connections/{connection.id}",
                headers={"Authorization": "Bearer synthetic"},
            )
            assert result.status_code == 200
            assert result.json()["has_access_token"]
            assert "test-secret" not in result.text
            assert "access_token_enc" not in result.text
    finally:
        service.delete_server(server.id)


def test_rpc_rejects_boolean_id_and_changed_session(monkeypatch):
    from agent_harness.services import mcp_transport
    from agent_harness.services.mcp_transport import MCPHTTPClient

    client = object.__new__(MCPHTTPClient)
    client.target = None
    client.sequence = 0
    client.headers = {"MCP-Session-Id": "original"}

    def boolean_response(target, **kwargs):
        return b'{"jsonrpc":"2.0","id":true,"result":{}}'

    monkeypatch.setattr(mcp_transport, "transport", boolean_response)
    with pytest.raises(SecurityError, match="invalid_mcp_response"):
        client.rpc("tools/list")

    def changed_session(target, **kwargs):
        kwargs["response_headers"]["mcp-session-id"] = "replacement"
        return json.dumps({"jsonrpc": "2.0", "id": client.sequence, "result": {}}).encode()

    monkeypatch.setattr(mcp_transport, "transport", changed_session)
    with pytest.raises(SecurityError, match="invalid_mcp_session"):
        client.rpc("tools/list")
    assert client.headers["MCP-Session-Id"] == "original"


def test_catalog_identity_change_invalidates_credentials(chat_database, endpoint):
    service, server, connection = make_service(chat_database, endpoint)
    try:
        service.update_server(
            server.id, ServerCreate(name=server.name, url=server.url + "?changed", auth_type="none")
        )
        updated = service.get_connection(connection.id)
        assert updated.status == "needs_reauth"
        assert not updated.has_access_token
        assert not updated.has_refresh_token
        assert updated.token_expires_at is None
    finally:
        service.delete_server(server.id)


def test_catalog_name_cannot_inherit_reviewed_endpoint_authority(chat_database, endpoint):
    service, server, connection = make_service(chat_database, endpoint)
    try:
        service.update_server(
            server.id,
            ServerCreate(name=server.name, url=server.url + "?replacement", auth_type="api_key"),
        )
        service.create_connection(
            ConnectionCreate(server_id=server.id, access_token="replacement-credential")  # noqa: S106
        )
        with pytest.raises(ChatError, match="mcp_server_identity_unreviewed"):
            service.register(Executor(service.policy))
        assert endpoint[1] == []
    finally:
        service.delete_server(server.id)


def test_streamable_http_finishes_complete_sse_result_before_connection_closes():
    from time import monotonic
    from urllib.parse import urlsplit

    from agent_harness.services.mcp_transport import MCPHTTPClient
    from agent_harness.tools.network import check_url

    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "id" not in request:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(
                (
                    "data: "
                    + json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
                    + "\n\n"
                ).encode()
            )
            self.wfile.flush()
            release.wait(2)
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    policy = Policy(network_allow=("127.0.0.1",), network_ports=(urlsplit(url).port,))
    try:
        started = monotonic()
        MCPHTTPClient(check_url(policy, url))
        assert monotonic() - started < 1
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join()


def test_auth_none_never_forwards_stale_ciphertext(chat_database, endpoint):
    service, server, connection = make_service(chat_database, endpoint)
    try:
        with service.database.connect() as db:
            # Simulate stale metadata from a previous version without clearing its token.
            db.execute(
                "UPDATE agent_chat.mcp_servers SET auth_type='none' WHERE id=%s", (server.id,)
            )
        assert service.check(connection.id)["connected"]
        assert endpoint[1]
        assert all(request["_authorization"] is None for request in endpoint[1])
    finally:
        service.delete_server(server.id)


def test_catalog_identity_edit_waits_for_credential_insert_then_clears_token(
    chat_database, endpoint, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    service, server, _ = make_service(chat_database, endpoint)
    encryption_entered = threading.Event()
    release_encryption = threading.Event()
    original_encrypt = service._encrypt

    def pause_encrypt(token):
        if token is not None:
            encryption_entered.set()
            assert release_encryption.wait(5)
        return original_encrypt(token)

    monkeypatch.setattr(service, "_encrypt", pause_encrypt)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            inserting = pool.submit(
                service.create_connection,
                ConnectionCreate(
                    server_id=server.id,
                    access_token="synthetic-old-credential",  # noqa: S106
                ),
            )
            try:
                assert encryption_entered.wait(3)
                editing = pool.submit(
                    service.update_server,
                    server.id,
                    ServerCreate(
                        name=server.name, url=server.url + "?changed", auth_type="api_key"
                    ),
                )
                # Catalog edits must wait until the credential's identity snapshot commits.
                with pytest.raises(TimeoutError):
                    editing.result(timeout=0.2)
            finally:
                release_encryption.set()
            inserting.result(timeout=3)
            editing.result(timeout=3)
        updated = service.get_connection(inserting.result().id)
        assert updated.status == "needs_reauth"
        assert not updated.has_access_token
    finally:
        release_encryption.set()
        service.delete_server(server.id)


def test_connection_request_accepts_wire_uuid_and_timezone_datetime():
    server_id = uuid4()
    body = ConnectionCreate.model_validate(
        {
            "server_id": str(server_id),
            "access_token": "synthetic",
            "token_expires_at": "2030-01-01T00:00:00Z",
        }
    )
    assert body.server_id == server_id
    assert body.token_expires_at.tzinfo is not None


def test_catalog_delete_locks_before_owner_check_and_blocks_concurrent_connection(
    chat_database, endpoint
):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from contextlib import contextmanager

    service, server, _ = make_service(chat_database, endpoint)
    database = service.database
    stranger_owner = database.owner(f"concurrent-{uuid4()}@localhost")
    stranger = MCPService(database, stranger_owner, service.policy, service.encryption_key)
    checked = threading.Event()
    release_check = threading.Event()

    class CheckedDatabase:
        @contextmanager
        def connect(self):
            with database.connect() as db:

                class Connection:
                    def execute(self, query, parameters=()):
                        cursor = db.execute(query, parameters)
                        if query.startswith("SELECT id FROM agent_chat.mcp_connections "):
                            checked.set()
                            assert release_check.wait(5)
                        return cursor

                yield Connection()

    service.database = CheckedDatabase()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            deleting = pool.submit(service.delete_server, server.id)
            try:
                assert checked.wait(3)
                inserting = pool.submit(
                    stranger.create_connection,
                    ConnectionCreate(
                        server_id=server.id,
                        access_token="synthetic-stranger",  # noqa: S106
                    ),
                )
                with pytest.raises(TimeoutError):
                    inserting.result(timeout=0.2)
            finally:
                release_check.set()
            deleting.result(timeout=3)
            with pytest.raises(ChatError, match="mcp_server_not_found"):
                inserting.result(timeout=3)
    finally:
        release_check.set()
        with database.connect() as db:
            db.execute("DELETE FROM agent_chat.mcp_servers WHERE id=%s", (server.id,))
            db.execute("DELETE FROM agent_chat.users WHERE id=%s", (stranger_owner,))


@pytest.mark.parametrize("mutation", ["disable", "delete", "identity", "expiry", "credentials"])
def test_registered_mcp_tool_rechecks_live_connection_before_rpc(chat_database, endpoint, mutation):
    service, server, connection = make_service(chat_database, endpoint)
    try:
        executor = Executor(service.policy)
        service.register(executor)
        if mutation == "disable":
            service.update_connection(
                connection.id,
                ConnectionCreate(
                    server_id=server.id,
                    status="disabled",
                    access_token="synthetic",  # noqa: S106
                ),
            )
        elif mutation == "credentials":
            service.update_connection(
                connection.id,
                ConnectionCreate(server_id=server.id, access_token="replacement"),  # noqa: S106
            )
        elif mutation == "delete":
            service.delete_connection(connection.id)
        elif mutation == "identity":
            service.update_server(
                server.id,
                ServerCreate(name=server.name, url=server.url + "?changed", auth_type="api_key"),
            )
        else:
            with service.database.connect() as db:
                db.execute(
                    "UPDATE agent_chat.mcp_connections "
                    "SET token_expires_at=now()-interval '1 second' "
                    "WHERE id=%s",
                    (connection.id,),
                )
        before = len(endpoint[1])
        with pytest.raises(SecurityError):
            executor.execute(
                {"tool": f"mcp.{server.name}.echo", "arguments": {"text": "forbidden"}}
            )
        assert len(endpoint[1]) == before
    finally:
        service.delete_server(server.id)


def test_mcp_lifecycle_and_call_logs_record_host_connection_id(chat_database, endpoint):
    from agent_harness.services.execution import ExecutionService

    service, server, connection = make_service(chat_database, endpoint)
    try:
        execution = ExecutionService(service.database, service.owner, None)
        observe, approve = execution.observer(None)
        executor = Executor(service.policy, observe=observe, approve=approve)
        service.register(executor)
        executor.execute({"tool": f"mcp.{server.name}.echo", "arguments": {"text": "hello"}})
        records = execution.list()
        assert len(records) == 2
        assert all(record.connection_id == connection.id for record in records)
    finally:
        service.delete_server(server.id)


def test_executor_observer_connection_identity_comes_from_host_tool():
    from agent_harness.core.executor import Tool

    connection_id = uuid4()
    events = []
    executor = Executor(Policy(), observe=events.append)
    executor.register(
        Tool(
            "host",
            {"type": "object", "additionalProperties": False},
            (),
            lambda: {},
            connection_id=connection_id,
        )
    )
    executor.execute({"tool": "host", "arguments": {}})
    assert events[0]["connection_id"] == connection_id
    events.clear()
    with pytest.raises(SecurityError, match="invalid_request"):
        executor.execute({"tool": "host", "arguments": {}, "connection_id": str(uuid4())})
    assert events[0]["connection_id"] == connection_id
