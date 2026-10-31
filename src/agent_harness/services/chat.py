from __future__ import annotations

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from ..models.chat import (
    Conversation,
    ConversationCreate,
    ConversationRename,
    Exchange,
    Message,
    SendMessage,
)
from .database import ChatError, Database


class ChatService:
    def __init__(self, database: Database, owner: UUID, agent_factory):
        self.database = database
        self.owner = owner
        self.agent_factory = agent_factory

    def _conversation(self, connection, conversation_id, *, lock=False):
        query = (
            "SELECT id, title, summary, compacted_through_message_id, memory_processed_at, "
            "created_at, updated_at FROM agent_chat.conversations "
            "WHERE id=%s AND user_id=%s"
        )
        if lock:
            query += " FOR UPDATE NOWAIT"
        row = connection.execute(
            query,
            (conversation_id, self.owner),
        ).fetchone()
        if row is None:
            raise ChatError("conversation_not_found", 404)
        return Conversation.model_validate(row)

    def create(self, request: ConversationCreate) -> Conversation:
        with self.database.connect() as connection:
            row = connection.execute(
                "INSERT INTO agent_chat.conversations(user_id, title) VALUES (%s, %s) "
                "RETURNING id, title, summary, compacted_through_message_id, "
                "memory_processed_at, created_at, updated_at",
                (self.owner, request.title),
            ).fetchone()
            return Conversation.model_validate(row)

    def list(self, *, limit=50, offset=0) -> list[Conversation]:
        self._pagination(limit, offset)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, title, summary, compacted_through_message_id, memory_processed_at, "
                "created_at, updated_at FROM agent_chat.conversations "
                "WHERE user_id=%s ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
                (self.owner, limit, offset),
            ).fetchall()
            return [Conversation.model_validate(row) for row in rows]

    def get(self, conversation_id: UUID) -> Conversation:
        with self.database.connect() as connection:
            return self._conversation(connection, conversation_id)

    def rename(self, conversation_id: UUID, request: ConversationRename) -> Conversation:
        with self.database.connect() as connection:
            self._conversation(connection, conversation_id, lock=True)
            row = connection.execute(
                "UPDATE agent_chat.conversations SET title=%s, updated_at=clock_timestamp() "
                "WHERE id=%s AND user_id=%s RETURNING id, title, summary, "
                "compacted_through_message_id, memory_processed_at, created_at, updated_at",
                (request.title, conversation_id, self.owner),
            ).fetchone()
            return Conversation.model_validate(row)

    def delete(self, conversation_id: UUID):
        with self.database.connect() as connection:
            self._conversation(connection, conversation_id, lock=True)
            connection.execute(
                "DELETE FROM agent_chat.conversations WHERE id=%s AND user_id=%s",
                (conversation_id, self.owner),
            )

    @staticmethod
    def _pagination(limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise ValueError("offset must be a nonnegative integer")

    def messages(self, conversation_id: UUID, *, after_seq=0, limit=100) -> list[Message]:
        self._pagination(limit, after_seq)
        with self.database.connect() as connection:
            self._conversation(connection, conversation_id)
            rows = connection.execute(
                "SELECT * FROM agent_chat.messages WHERE conversation_id=%s AND seq>%s "
                "ORDER BY seq LIMIT %s",
                (conversation_id, after_seq, limit),
            ).fetchall()
            return [Message.model_validate(row) for row in rows]

    def send(self, conversation_id: UUID, request: SendMessage) -> Exchange:
        with self.database.connect() as connection:
            self._conversation(connection, conversation_id, lock=True)
            rows = connection.execute(
                "SELECT * FROM agent_chat.messages WHERE conversation_id=%s "
                "ORDER BY seq DESC LIMIT 16",
                (conversation_id,),
            ).fetchall()
            previous = [Message.model_validate(row) for row in reversed(rows)]
            context = [
                {
                    "role": message.role,
                    "content": json.dumps({"final": message.content[0].text})
                    if message.role == "assistant"
                    else message.content[0].text,
                }
                for message in previous
            ]
            while context and len(json.dumps(context)) > 8000:
                del context[:2]
            agent = self.agent_factory()
            answer = agent.run(request.content, context=context)
            seq = rows[0]["seq"] + 1 if rows else 1
            user = self._insert(connection, conversation_id, seq, "user", request.content)
            assistant = self._insert(
                connection,
                conversation_id,
                seq + 1,
                "assistant",
                answer,
                agent.last_run.input_tokens,
                agent.last_run.output_tokens,
            )
            connection.execute(
                "UPDATE agent_chat.conversations SET updated_at=clock_timestamp() "
                "WHERE id=%s AND user_id=%s",
                (conversation_id, self.owner),
            )
            return Exchange(user=user, assistant=assistant, run_id=agent.last_run.id)

    @staticmethod
    def _insert(connection, conversation_id, seq, role, text, input_tokens=0, output_tokens=0):
        row = connection.execute(
            "INSERT INTO agent_chat.messages "
            "(conversation_id, seq, role, content, input_tokens, output_tokens) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                conversation_id,
                seq,
                role,
                Jsonb([{"type": "text", "text": text}]),
                input_tokens,
                output_tokens,
            ),
        ).fetchone()
        return Message.model_validate(row)
