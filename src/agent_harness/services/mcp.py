from contextlib import contextmanager
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from psycopg.types.json import Jsonb

from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.models.mcp import Connection, MCPTool, Server
from agent_harness.services.database import ChatError
from agent_harness.services.mcp_transport import MCPHTTPClient
from agent_harness.tools.mcp import MCPAdapter, MCPPermission
from agent_harness.tools.network import check_url, is_local


class MCPService:
    def __init__(
        self,
        database,
        owner,
        policy,
        encryption_key,
        *,
        permissions=None,
        endpoints=None,
        discovery_factory=None,
    ):
        self.database = database
        self.owner = owner
        self.policy = policy
        self.encryption_key = encryption_key
        self.endpoints = dict(endpoints or {})
        self.discovery_factory = discovery_factory
        self.cipher = Fernet(encryption_key) if encryption_key else None
        # Copy host mappings; cached metadata and API input cannot grant capabilities.
        self.permissions = {
            name: {
                tool: MCPPermission(tuple(value.capabilities), dict(value.resources))
                for tool, value in tools.items()
            }
            for name, tools in (permissions or {}).items()
        }

    def _validate_url(self, body):
        if body.transport == "stdio":
            raise ChatError("mcp_stdio_requires_reviewed_host_command", 422)
        if body.transport == "sse":
            raise ChatError("mcp_legacy_sse_transport_unconfigured", 422)
        try:
            target = check_url(self.policy, body.url)
            if not is_local(target.address):
                raise SecurityError("local_only_network_denied")
        except SecurityError as exc:
            raise ChatError("mcp_endpoint_denied", 403) from exc

    def create_server(self, body):
        self._validate_url(body)
        with self.database.connect() as db:
            if db.execute(
                "SELECT id FROM agent_chat.mcp_servers WHERE name=%s", (body.name,)
            ).fetchone():
                raise ChatError("mcp_server_name_exists", 409)
            row = db.execute(
                "INSERT INTO agent_chat.mcp_servers(name,url,transport,auth_type) "
                "VALUES (%s,%s,%s,%s) RETURNING id,name,url,transport,auth_type,created_at",
                (body.name, body.url, body.transport, body.auth_type),
            ).fetchone()
        return Server(**row)

    def list_servers(self, *, limit=50, offset=0):
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT id,name,url,transport,auth_type,created_at FROM "
                "agent_chat.mcp_servers "
                "ORDER BY created_at,id LIMIT %s OFFSET %s",
                (limit, offset),
            ).fetchall()
        return [Server(**row) for row in rows]

    def get_server(self, server_id):
        with self.database.connect() as db:
            row = db.execute(
                "SELECT id,name,url,transport,auth_type,created_at FROM "
                "agent_chat.mcp_servers WHERE id=%s",
                (server_id,),
            ).fetchone()
        if row is None:
            raise ChatError("mcp_server_not_found", 404)
        return Server(**row)

    def update_server(self, server_id, body):
        self._validate_url(body)
        with self.database.connect() as db:
            before = db.execute(
                "SELECT name,url,transport,auth_type FROM agent_chat.mcp_servers "
                "WHERE id=%s FOR UPDATE",
                (server_id,),
            ).fetchone()
            if before is None:
                raise ChatError("mcp_server_not_found", 404)
            if db.execute(
                "SELECT id FROM agent_chat.mcp_connections "
                "WHERE server_id=%s AND user_id<>%s LIMIT 1",
                (server_id, self.owner),
            ).fetchone():
                raise ChatError("mcp_server_in_use", 409)
            identity_changed = any(before[key] != getattr(body, key) for key in before)
            row = db.execute(
                "UPDATE agent_chat.mcp_servers SET name=%s,url=%s,transport=%s,auth_type=%s "
                "WHERE id=%s RETURNING id,name,url,transport,auth_type,created_at",
                (body.name, body.url, body.transport, body.auth_type, server_id),
            ).fetchone()
            if row is None:
                raise ChatError("mcp_server_not_found", 404)
            db.execute("DELETE FROM agent_chat.mcp_tools WHERE server_id=%s", (server_id,))
            if identity_changed:
                db.execute(
                    "UPDATE agent_chat.mcp_connections SET status='needs_reauth',"
                    "access_token_enc=NULL,refresh_token_enc=NULL,token_expires_at=NULL "
                    "WHERE server_id=%s",
                    (server_id,),
                )
        return Server(**row)

    def delete_server(self, server_id):
        with self.database.connect() as db:
            if not db.execute(
                "SELECT id FROM agent_chat.mcp_servers WHERE id=%s FOR UPDATE", (server_id,)
            ).fetchone():
                raise ChatError("mcp_server_not_found", 404)
            if db.execute(
                "SELECT id FROM agent_chat.mcp_connections "
                "WHERE server_id=%s AND user_id<>%s LIMIT 1",
                (server_id, self.owner),
            ).fetchone():
                raise ChatError("mcp_server_in_use", 409)
            if not db.execute(
                "DELETE FROM agent_chat.mcp_servers WHERE id=%s RETURNING id", (server_id,)
            ).fetchone():
                raise ChatError("mcp_server_not_found", 404)

    def _encrypt(self, token):
        if token is None:
            return None
        if self.cipher is None:
            raise ChatError("mcp_credential_key_unconfigured")
        return self.cipher.encrypt(token.get_secret_value().encode())

    def create_connection(self, body):
        status = body.status
        if body.token_expires_at and body.token_expires_at <= datetime.now(timezone.utc):
            status = "needs_reauth"
        with self.database.connect() as db:
            server = db.execute(
                "SELECT auth_type FROM agent_chat.mcp_servers WHERE id=%s FOR SHARE",
                (body.server_id,),
            ).fetchone()
            if server is None:
                raise ChatError("mcp_server_not_found", 404)
            if server["auth_type"] == "none" and (body.access_token or body.refresh_token):
                raise ChatError("mcp_credentials_not_required", 422)
            if server["auth_type"] != "none" and body.access_token is None:
                raise ChatError("mcp_access_token_required", 422)
            # Catalog identity edits wait for this insert, then invalidate its credentials.
            access = self._encrypt(body.access_token)
            refresh = self._encrypt(body.refresh_token)
            row = db.execute(
                "INSERT INTO agent_chat.mcp_connections "
                "(user_id,server_id,status,access_token_enc,refresh_token_enc,"
                "token_expires_at,scopes) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,server_id) DO UPDATE SET "
                "status=EXCLUDED.status,access_token_enc=EXCLUDED.access_token_enc,"
                "refresh_token_enc=EXCLUDED.refresh_token_enc,"
                "token_expires_at=EXCLUDED.token_expires_at,scopes=EXCLUDED.scopes "
                "RETURNING id,server_id,status,token_expires_at,scopes,created_at,"
                "(access_token_enc IS NOT NULL) AS has_access_token,(refresh_token_enc IS "
                "NOT NULL) AS has_refresh_token",
                (
                    self.owner,
                    body.server_id,
                    status,
                    access,
                    refresh,
                    body.token_expires_at,
                    body.scopes,
                ),
            ).fetchone()
        return Connection(**row)

    def _expire(self, db):
        db.execute(
            "UPDATE agent_chat.mcp_connections SET status='needs_reauth' "
            "WHERE user_id=%s AND status='active' AND token_expires_at<=now()",
            (self.owner,),
        )

    def list_connections(self, *, limit=50, offset=0):
        with self.database.connect() as db:
            self._expire(db)
            rows = db.execute(
                "SELECT id,server_id,status,token_expires_at,scopes,created_at,"
                "(access_token_enc IS NOT NULL) AS has_access_token,(refresh_token_enc IS "
                "NOT NULL) AS has_refresh_token FROM agent_chat.mcp_connections "
                "WHERE user_id=%s ORDER BY created_at,id LIMIT %s OFFSET %s",
                (self.owner, limit, offset),
            ).fetchall()
        return [Connection(**{**row, "scopes": row["scopes"] or []}) for row in rows]

    def get_connection(self, connection_id):
        with self.database.connect() as db:
            self._expire(db)
            row = db.execute(
                "SELECT id,server_id,status,token_expires_at,scopes,created_at,"
                "(access_token_enc IS NOT NULL) AS has_access_token,(refresh_token_enc IS "
                "NOT NULL) AS has_refresh_token FROM agent_chat.mcp_connections "
                "WHERE id=%s AND user_id=%s",
                (connection_id, self.owner),
            ).fetchone()
        if row is None:
            raise ChatError("mcp_connection_not_found", 404)
        return Connection(**{**row, "scopes": row["scopes"] or []})

    def update_connection(self, connection_id, body):
        connection = self.get_connection(connection_id)
        if connection.server_id != body.server_id:
            raise ChatError("mcp_connection_server_mismatch", 422)
        return self.create_connection(body)

    def delete_connection(self, connection_id):
        with self.database.connect() as db:
            if not db.execute(
                "DELETE FROM agent_chat.mcp_connections WHERE id=%s AND user_id=%s RETURNING id",
                (connection_id, self.owner),
            ).fetchone():
                raise ChatError("mcp_connection_not_found", 404)

    def list_tools(self, server_id):
        self.get_server(server_id)
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT id,server_id,name,description,input_schema,deferred,fetched_at "
                "FROM agent_chat.mcp_tools WHERE server_id=%s ORDER BY name LIMIT 256",
                (server_id,),
            ).fetchall()
        return [MCPTool(**row) for row in rows]

    def _connect(self, connection_id, executor, *, permissions):
        # Pair endpoint and credential in one database snapshot during catalog edits.
        with self.database.connect() as db:
            self._expire(db)
            row = db.execute(
                "SELECT s.id,s.name,s.url,s.transport,s.auth_type,s.created_at,"
                "c.status,c.access_token_enc FROM agent_chat.mcp_connections c "
                "JOIN agent_chat.mcp_servers s ON s.id=c.server_id "
                "WHERE c.id=%s AND c.user_id=%s",
                (connection_id, self.owner),
            ).fetchone()
        if row is None:
            raise ChatError("mcp_connection_not_found", 404)
        if row["status"] != "active":
            raise ChatError("mcp_connection_" + row["status"], 409)
        server = Server(**{key: row[key] for key in Server.model_fields})
        if permissions and (
            self.endpoints.get(server.name) != server.url
            or permissions != self.permissions.get(server.name)
        ):
            raise ChatError("mcp_server_identity_unreviewed", 403)
        self._validate_url(server)
        token = None
        if server.auth_type != "none" and row["access_token_enc"] is not None:
            if self.cipher is None:
                raise ChatError("mcp_credential_key_unconfigured")
            try:
                token = self.cipher.decrypt(bytes(row["access_token_enc"])).decode()
            except (InvalidToken, UnicodeError) as exc:
                raise ChatError("mcp_credentials_unavailable") from exc
        if server.auth_type != "none" and not token:
            raise ChatError("mcp_access_token_required", 409)

        @contextmanager
        def operation_guard():
            with self.database.connect() as db:
                live = db.execute(
                    "SELECT s.name,s.url,s.transport,s.auth_type,c.status,c.token_expires_at,"
                    "c.access_token_enc FROM agent_chat.mcp_servers s "
                    "JOIN agent_chat.mcp_connections c ON c.server_id=s.id "
                    "WHERE s.id=%s AND c.id=%s AND c.user_id=%s FOR SHARE OF s,c",
                    (server.id, connection_id, self.owner),
                ).fetchone()
                if live is None or live["status"] != "active":
                    raise SecurityError("resource_denied")
                expiry = live["token_expires_at"]
                if expiry is not None and expiry <= datetime.now(timezone.utc):
                    raise SecurityError("resource_denied")
                if any(
                    live[key] != getattr(server, key)
                    for key in ("name", "url", "transport", "auth_type")
                ):
                    raise SecurityError("resource_denied")
                if live["access_token_enc"] != row["access_token_enc"]:
                    raise SecurityError("resource_denied")
                # Hold both shared locks through RPC so revocation and edits serialize.
                yield

        adapter = MCPAdapter(
            executor,
            server.name,
            lambda target: MCPHTTPClient(target, token, operation_guard=operation_guard),
            endpoint=server.url,
            connection_id=connection_id,
        )
        try:
            adapter.connect(permissions)
        except (SecurityError, ValueError) as exc:
            raise ChatError(
                "mcp_connection_failed", 403 if isinstance(exc, SecurityError) else 503
            ) from exc
        return adapter, server

    def refresh(self, connection_id):
        executor = self.discovery_factory() if self.discovery_factory else Executor(self.policy)
        adapter, server = self._connect(connection_id, executor, permissions={})
        try:
            with self.database.connect() as db:
                # Serialize cache replacement with catalog edits/deletion.
                if not db.execute(
                    "SELECT id FROM agent_chat.mcp_servers WHERE id=%s FOR UPDATE", (server.id,)
                ).fetchone():
                    raise ChatError("mcp_server_not_found", 404)
                db.execute("DELETE FROM agent_chat.mcp_tools WHERE server_id=%s", (server.id,))
                for tool in adapter.client.discovered:
                    db.execute(
                        "INSERT INTO agent_chat.mcp_tools "
                        "(server_id,name,description,input_schema) VALUES (%s,%s,%s,%s)",
                        (
                            server.id,
                            tool["name"],
                            tool.get("description"),
                            Jsonb(tool["inputSchema"]),
                        ),
                    )
        finally:
            adapter.client.close()
        return self.list_tools(server.id)

    def check(self, connection_id):
        tools = self.refresh(connection_id)
        return {"connected": True, "tools": len(tools)}

    def register(self, executor):
        """Host entry point: fresh policy-checked discovery; cached schemas grant nothing."""
        adapters = []
        for connection in self.list_connections(limit=100):
            server = self.get_server(connection.server_id)
            permissions = self.permissions.get(server.name)
            if connection.status == "active" and permissions:
                if self.endpoints.get(server.name) != server.url:
                    raise ChatError("mcp_server_identity_unreviewed", 403)
                adapter, _ = self._connect(connection.id, executor, permissions=permissions)
                adapters.append(adapter)
        return adapters
