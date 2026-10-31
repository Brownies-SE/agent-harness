from __future__ import annotations

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from ..models.chat import (
    Conversation,
    ConversationCreate,
    ConversationRename,
    ConversationSearchHit,
    CurrentUser,
    Exchange,
    Message,
    SendMessage,
)
from .database import ChatError, Database


class ChatService:
    def __init__(
        self,
        database: Database,
        owner: UUID,
        agent_factory,
        *,
        memory_service=None,
        execution_service=None,
    ):
        self.database = database
        self.owner = owner
        self.agent_factory = agent_factory
        self.memory_service = memory_service
        self.execution_service = execution_service

    def profile(self):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id,email,created_at FROM agent_chat.users WHERE id=%s", (self.owner,)
            ).fetchone()
            if row is None:
                raise ChatError("local_owner_unavailable")
            return CurrentUser.model_validate(row)

    def _conversation(self, connection, conversation_id, *, lock=False):
        query = (
            "SELECT id, title, summary, compacted_through_message_id, memory_processed_at, "
            "created_at, updated_at FROM agent_chat.conversations "
            "WHERE id=%s AND user_id=%s AND deleted_at IS NULL"
        )
        if lock:
            query += " FOR NO KEY UPDATE NOWAIT"
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
                "WHERE user_id=%s AND deleted_at IS NULL "
                "ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
                (self.owner, limit, offset),
            ).fetchall()
            return [Conversation.model_validate(row) for row in rows]

    def search(self, request):
        query = request.query.lower()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id,c.title,c.summary,c.compacted_through_message_id,"
                "c.memory_processed_at,c.created_at,c.updated_at,"
                "hit.id AS matched_message_id,hit.seq AS matched_seq,hit.text "
                "FROM agent_chat.conversations c LEFT JOIN LATERAL ("
                "SELECT m.id,m.seq,m.content #>> '{0,text}' AS text "
                "FROM agent_chat.messages m WHERE m.conversation_id=c.id "
                "AND strpos(lower(COALESCE(m.content #>> '{0,text}','')),%s)>0 "
                "ORDER BY CASE WHEN %s='' THEN -m.seq ELSE m.seq END LIMIT 1) hit ON true "
                "WHERE c.user_id=%s AND c.deleted_at IS NULL "
                "AND (%s='' OR strpos(lower(c.title),%s)>0 "
                "OR hit.id IS NOT NULL) ORDER BY c.updated_at DESC,c.id DESC LIMIT %s OFFSET %s",
                (query, query, self.owner, query, query, request.limit, request.offset),
            ).fetchall()
        result = []
        for row in rows:
            content = row["text"] or ""
            start = max(0, content.lower().find(query) - 80) if query else 0
            result.append(
                ConversationSearchHit(
                    conversation=Conversation.model_validate(
                        {key: row[key] for key in Conversation.model_fields}
                    ),
                    snippet=content[start : start + 400],
                    matched_message_id=row["matched_message_id"],
                    matched_seq=row["matched_seq"],
                )
            )
        return result

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
                "UPDATE agent_chat.conversations SET deleted_at=clock_timestamp(), "
                "updated_at=clock_timestamp() WHERE id=%s AND user_id=%s",
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
            return [self._message(connection, row) for row in rows]

    def send(self, conversation_id: UUID, request: SendMessage) -> Exchange:
        with self.database.connect() as connection:
            conversation = self._conversation(connection, conversation_id, lock=True)
            rows = connection.execute(
                "SELECT * FROM agent_chat.messages WHERE conversation_id=%s "
                "AND seq>COALESCE((SELECT seq FROM agent_chat.messages WHERE id=%s "
                "AND conversation_id=%s),0) ORDER BY seq DESC LIMIT 16",
                (conversation_id, conversation.compacted_through_message_id, conversation_id),
            ).fetchall()
            previous = [Message.model_validate(row) for row in reversed(rows)]
            agent, answer = self._answer(conversation, request.content, previous)
            seq = connection.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS seq FROM agent_chat.messages "
                "WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchone()["seq"]
            user = self._insert(connection, conversation_id, seq, "user", request.content)
            assistant = self._insert(
                connection,
                conversation_id,
                seq + 1,
                "assistant",
                answer,
                agent.last_run.input_tokens,
                agent.last_run.output_tokens,
                agent.last_run.cache_read_tokens,
            )
            connection.execute(
                "UPDATE agent_chat.conversations SET updated_at=clock_timestamp() "
                "WHERE id=%s AND user_id=%s",
                (conversation_id, self.owner),
            )
            exchange = Exchange(user=user, assistant=assistant, run_id=agent.last_run.id)
        if self.execution_service is not None:
            self.execution_service.link_run(exchange.run_id, exchange.assistant.id)
        return exchange

    def _answer(self, conversation, prompt, previous, *, include_recalled_context=True):
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
        if self.execution_service is not None:
            observe, approve = self.execution_service.observer(
                conversation.id, conversation_locked=True
            )
            agent.executor._observe = observe
            agent.executor._approve = approve
        observations = []
        if include_recalled_context and conversation.summary:
            observations.append("Conversation summary (untrusted): " + conversation.summary[:7900])
        if include_recalled_context and self.memory_service is not None:
            from ..models.memory import MemorySearch

            hits = self.memory_service.search(
                MemorySearch(query=prompt[:400], limit=3, min_score=0.3)
            )
            observations.extend(
                "Recalled memory (untrusted): " + hit.memory.content[:2000] for hit in hits
            )
        answer = agent.run(prompt, context=context, observations=observations)
        return agent, answer

    @staticmethod
    def _insert(
        connection,
        conversation_id,
        seq,
        role,
        text,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
    ):
        row = connection.execute(
            "INSERT INTO agent_chat.messages "
            "(conversation_id, seq, role, content, input_tokens, output_tokens,cache_read_tokens) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                conversation_id,
                seq,
                role,
                Jsonb([{"type": "text", "text": text}]),
                input_tokens,
                output_tokens,
                cache_read_tokens,
            ),
        ).fetchone()
        if role == "assistant":
            connection.execute(
                "INSERT INTO agent_chat.message_variants "
                "(id,message_id,ordinal,content,input_tokens,output_tokens,cache_read_tokens) "
                "VALUES (%s,%s,0,%s,%s,%s,%s)",
                (
                    row["id"],
                    row["id"],
                    Jsonb(row["content"]),
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                ),
            )
        return ChatService._message(connection, row)

    @staticmethod
    def _message(connection, row):
        values = dict(row)
        if row["role"] == "assistant":
            values["variants"] = connection.execute(
                "SELECT "
                "id,ordinal,content,rating,input_tokens,output_tokens,cache_read_tokens,created_at "
                "FROM agent_chat.message_variants WHERE message_id=%s ORDER BY ordinal",
                (row["id"],),
            ).fetchall()
        return Message.model_validate(values)

    @staticmethod
    def _reply(connection, conversation_id, message_id):
        row = connection.execute(
            "SELECT * FROM agent_chat.messages WHERE id=%s AND conversation_id=%s "
            "AND role='assistant'",
            (message_id, conversation_id),
        ).fetchone()
        if row is None:
            raise ChatError("reply_not_found", 404)
        return row

    def rate(self, conversation_id, message_id, variant_id, rating):
        if rating not in ("up", "down", None):
            raise ChatError("invalid_rating", 422)
        with self.database.connect() as connection:
            self._conversation(connection, conversation_id, lock=True)
            row = self._reply(connection, conversation_id, message_id)
            changed = connection.execute(
                "UPDATE agent_chat.message_variants SET rating=%s "
                "WHERE id=%s AND message_id=%s RETURNING id",
                (rating, variant_id, message_id),
            ).fetchone()
            if changed is None:
                raise ChatError("variant_not_found", 404)
            return self._message(connection, row)

    def _select(self, connection, conversation_id, message_id, variant_id):
        variant = connection.execute(
            "SELECT * FROM agent_chat.message_variants WHERE id=%s AND message_id=%s",
            (variant_id, message_id),
        ).fetchone()
        if variant is None:
            raise ChatError("variant_not_found", 404)
        row = connection.execute(
            "UPDATE agent_chat.messages SET "
            "content=%s,selected_variant=%s,input_tokens=%s,"
            "output_tokens=%s,cache_read_tokens=%s "
            "WHERE id=%s AND conversation_id=%s RETURNING *",
            (
                Jsonb(variant["content"]),
                variant["ordinal"],
                variant["input_tokens"],
                variant["output_tokens"],
                variant["cache_read_tokens"],
                message_id,
                conversation_id,
            ),
        ).fetchone()
        connection.execute(
            "UPDATE agent_chat.conversations SET "
            "summary=NULL,compacted_through_message_id=NULL,updated_at=clock_timestamp() "
            "WHERE id=%s AND user_id=%s",
            (conversation_id, self.owner),
        )
        return self._message(connection, row)

    def select_variant(self, conversation_id, message_id, variant_id):
        with self.database.connect() as connection:
            self._conversation(connection, conversation_id, lock=True)
            self._reply(connection, conversation_id, message_id)
            return self._select(connection, conversation_id, message_id, variant_id)

    def regenerate(self, conversation_id, message_id):
        with self.database.connect() as connection:
            conversation = self._conversation(connection, conversation_id, lock=True)
            reply = self._reply(connection, conversation_id, message_id)
            user = connection.execute(
                "SELECT * FROM agent_chat.messages WHERE conversation_id=%s "
                "AND seq=%s AND role='user'",
                (conversation_id, reply["seq"] - 1),
            ).fetchone()
            if user is None:
                raise ChatError("source_turn_not_found", 404)
            rows = connection.execute(
                "SELECT * FROM agent_chat.messages WHERE conversation_id=%s AND seq<%s "
                "ORDER BY seq DESC LIMIT 16",
                (conversation_id, user["seq"]),
            ).fetchall()
            previous = [Message.model_validate(row) for row in reversed(rows)]
            agent, answer = self._answer(
                conversation, user["content"][0]["text"], previous, include_recalled_context=False
            )
            variant = connection.execute(
                "INSERT INTO agent_chat.message_variants "
                "(message_id,ordinal,content,input_tokens,output_tokens,cache_read_tokens) "
                "SELECT %s,COALESCE(MAX(ordinal),-1)+1,%s,%s,%s,%s "
                "FROM agent_chat.message_variants WHERE message_id=%s RETURNING id",
                (
                    message_id,
                    Jsonb([{"type": "text", "text": answer}]),
                    agent.last_run.input_tokens,
                    agent.last_run.output_tokens,
                    agent.last_run.cache_read_tokens,
                    message_id,
                ),
            ).fetchone()
            result = self._select(connection, conversation_id, message_id, variant["id"])
        if self.execution_service is not None:
            self.execution_service.link_run(agent.last_run.id, message_id)
        return result
