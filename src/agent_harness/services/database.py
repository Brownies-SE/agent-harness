import os
from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from pydantic import ValidationError


class ChatError(Exception):
    def __init__(self, detail, status_code=503):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class Database:
    def __init__(self, url):
        try:
            parameters = conninfo_to_dict(url)
        except (psycopg.Error, TypeError) as exc:
            raise ValueError("invalid local database configuration") from exc
        allowed = {"host", "port", "dbname", "user", "password"}
        if (
            set(parameters) - allowed
            or parameters.get("host") not in {"127.0.0.1", "::1"}
            or not all(parameters.get(k) for k in ("dbname", "user", "password"))
            or not parameters.get("port", "5432").isdigit()
            or not 1 <= int(parameters.get("port", "5432")) <= 65535
        ):
            raise ValueError("database requires explicit loopback host and credentials")
        self.url = url
        self.parameters = {"port": "5432", **parameters}

    @contextmanager
    def connect(self):
        if os.environ.get("PGSERVICE"):
            raise ChatError("chat_storage_unavailable")
        try:
            with psycopg.connect(
                **self.parameters,
                hostaddr=self.parameters["host"],
                sslmode="disable",
                gssencmode="disable",
                connect_timeout=5,
                options="-c statement_timeout=5000 -c lock_timeout=1000",
                row_factory=dict_row,
            ) as connection:
                yield connection
        except psycopg.errors.LockNotAvailable as exc:
            raise ChatError("conversation_busy", 409) from exc
        except (psycopg.Error, ValidationError) as exc:
            raise ChatError("chat_storage_unavailable") from exc

    def migrate(self):
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(71304921)")
            connection.execute("CREATE SCHEMA IF NOT EXISTS agent_chat")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agent_chat.schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            versions = connection.execute(
                "SELECT version FROM agent_chat.schema_migrations ORDER BY version"
            ).fetchall()
            if versions not in ([], [{"version": 1}]):
                raise ChatError("unsupported_chat_schema")
            if not versions:
                migration = files("agent_harness.migrations").joinpath("0001_chat.sql")
                connection.execute(migration.read_text())
                connection.execute("INSERT INTO agent_chat.schema_migrations(version) VALUES (1)")

    def owner(self, email):
        with self.connect() as connection:
            return connection.execute(
                "INSERT INTO agent_chat.users(email) VALUES (%s) "
                "ON CONFLICT (email) DO UPDATE SET email=EXCLUDED.email RETURNING id",
                (email,),
            ).fetchone()["id"]

    def health(self):
        with self.connect() as connection:
            connection.execute("SELECT 1")
