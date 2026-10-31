import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from agent_harness.core.agents import LangGraphAgent
from agent_harness.core.errors import SecurityError
from agent_harness.core.executor import Executor
from agent_harness.core.policy import Policy
from agent_harness.models.chat import ConversationCreate, ConversationRename, SendMessage
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


def test_complete_requested_schema_exists_and_migration_is_idempotent(chat_database):
    database, owner = chat_database
    chat = service(database, owner)
    conversation = chat.create(ConversationCreate(title="preserved"))
    chat.send(conversation.id, SendMessage(content="keep this"))
    database.migrate()
    with database.connect() as connection:
        tables = {
            row["table_name"]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='agent_chat'"
            ).fetchall()
        }
        assert tables == {
            "schema_migrations",
            "users",
            "conversations",
            "messages",
            "mcp_servers",
            "mcp_connections",
            "mcp_tools",
            "tool_permissions",
            "tool_calls",
            "files",
            "memories",
            "local_account",
            "auth_sessions",
            "message_variants",
        }
        columns = {
            (row["table_name"], row["column_name"])
            for row in connection.execute(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='agent_chat'"
            ).fetchall()
        }
        assert {
            ("conversations", "summary"),
            ("conversations", "deleted_at"),
            ("conversations", "compacted_through_message_id"),
            ("conversations", "memory_processed_at"),
            ("messages", "cache_read_tokens"),
        } <= columns
        assert (
            connection.execute(
                "SELECT a.atttypmod AS dimensions FROM pg_attribute a JOIN pg_class c "
                "ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='agent_chat' AND c.relname='memories' AND a.attname='embedding'"
            ).fetchone()["dimensions"]
            == 1024
        )
        assert [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM agent_chat.schema_migrations ORDER BY version"
            ).fetchall()
        ] == [1, 2, 3, 4, 5, 6, 7]
    assert chat.get(conversation.id).title == "preserved"
    assert len(chat.messages(conversation.id)) == 2


def test_v1_upgrade_preserves_rows_and_enforces_scoped_references(chat_database):
    from importlib.resources import files
    from uuid import uuid4

    import psycopg
    from psycopg import sql

    database, _ = chat_database
    namespace = "upgrade_" + uuid4().hex
    with database.connect() as connection:

        def execute(statement, parameters=()):
            return connection.execute(
                sql.SQL(statement).format(ns=sql.Identifier(namespace)), parameters
            )

        try:
            execute("CREATE SCHEMA {ns}")
            migration_root = files("agent_harness.migrations")
            for filename in ("0001_chat.sql", "0002_resources.sql"):
                migration = migration_root.joinpath(filename).read_text()
                connection.execute(migration.replace("agent_chat.", namespace + "."))
                if filename == "0001_chat.sql":
                    owner = execute(
                        "INSERT INTO {ns}.users(email) VALUES ('synthetic@localhost') RETURNING id"
                    ).fetchone()["id"]
                    stranger = execute(
                        "INSERT INTO {ns}.users(email) VALUES ('other@localhost') RETURNING id"
                    ).fetchone()["id"]
                    conversation = execute(
                        "INSERT INTO {ns}.conversations(user_id,title) "
                        "VALUES (%s,'keep') RETURNING id",
                        (owner,),
                    ).fetchone()["id"]
                    other = execute(
                        "INSERT INTO {ns}.conversations(user_id,title) "
                        "VALUES (%s,'other') RETURNING id",
                        (stranger,),
                    ).fetchone()["id"]
                    message = execute(
                        "INSERT INTO {ns}.messages(conversation_id,seq,role,content) "
                        "VALUES (%s,1,'user','[]') RETURNING id",
                        (conversation,),
                    ).fetchone()["id"]
                    other_message = execute(
                        "INSERT INTO {ns}.messages(conversation_id,seq,role,content) "
                        "VALUES (%s,1,'user','[]') RETURNING id",
                        (other,),
                    ).fetchone()["id"]
            assert execute(
                "SELECT title,summary FROM {ns}.conversations WHERE id=%s", (conversation,)
            ).fetchone() == {"title": "keep", "summary": None}
            assert (
                execute(
                    "SELECT cache_read_tokens FROM {ns}.messages WHERE id=%s", (message,)
                ).fetchone()["cache_read_tokens"]
                == 0
            )

            for statement, arguments in (
                (
                    "UPDATE {ns}.conversations SET compacted_through_message_id=%s WHERE id=%s",
                    (other_message, conversation),
                ),
                (
                    "INSERT INTO {ns}.files(user_id,conversation_id,storage_key,filename,origin) "
                    "VALUES (%s,%s,'local/blob','note.txt','upload')",
                    (owner, other),
                ),
                (
                    "INSERT INTO {ns}.memories(user_id,source_conversation_id,content,source) "
                    "VALUES (%s,%s,'synthetic','stated')",
                    (owner, other),
                ),
            ):
                with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
                    execute(statement, arguments)

            execute(
                "UPDATE {ns}.conversations SET compacted_through_message_id=%s WHERE id=%s",
                (message, conversation),
            )
            execute(
                "INSERT INTO {ns}.files(user_id,conversation_id,storage_key,filename,origin) "
                "VALUES (%s,%s,'local/blob','note.txt','upload')",
                (owner, conversation),
            )
            execute(
                "INSERT INTO {ns}.memories(user_id,source_conversation_id,content,source) "
                "VALUES (%s,%s,'synthetic','stated')",
                (owner, conversation),
            )
            execute("DELETE FROM {ns}.messages WHERE id=%s", (message,))
            assert (
                execute(
                    "SELECT compacted_through_message_id FROM {ns}.conversations WHERE id=%s",
                    (conversation,),
                ).fetchone()["compacted_through_message_id"]
                is None
            )
            execute("DELETE FROM {ns}.conversations WHERE id=%s", (conversation,))
            assert (
                execute("SELECT conversation_id FROM {ns}.files").fetchone()["conversation_id"]
                is None
            )
            assert (
                execute("SELECT source_conversation_id FROM {ns}.memories").fetchone()[
                    "source_conversation_id"
                ]
                is None
            )
        finally:
            # The temporary schema and all fixtures disappear without touching existing test data.
            connection.rollback()


def test_stored_allow_policy_cannot_authorize_a_tool(chat_database, tmp_path):
    from agent_harness.tools import builtin_tools

    database, owner = chat_database
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO agent_chat.tool_permissions(user_id,tool_key,policy) "
            "VALUES (%s,'builtin:write_file','allow')",
            (owner,),
        )
    decisions = iter(
        [
            {"tool": "write_file", "arguments": {"path": "note.txt", "content": "forbidden"}},
            {"final": "denied"},
        ]
    )
    policy = Policy(roots=(tmp_path,), rules={"filesystem.write": "ask"})

    def agent():
        executor = Executor(policy)
        for tool in builtin_tools(policy):
            executor.register(tool)
        return LangGraphAgent(Model(lambda messages: next(decisions)), executor)

    chat = ChatService(database, owner, agent)
    conversation = chat.create(ConversationCreate())
    chat.send(conversation.id, SendMessage(content="write it"))
    assert not (tmp_path / "note.txt").exists()


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
            == 2
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
