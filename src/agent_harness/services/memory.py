"""Owner-scoped durable memories and explicit local history summarization."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from ..model import strict_json
from ..models.chat import Conversation
from ..models.memory import Memory, MemoryCreate, MemoryHit, MemorySearch, MemoryUpdate, valid_text
from .database import ChatError, Database
from .embeddings import validate_vectors

SummaryFunction = Callable[[list[dict[str, str]], Literal["extract", "compact"]], str]


class MemoryService:
    def __init__(
        self, database: Database, owner: UUID, embedder, summarize: SummaryFunction | None = None
    ):
        self.database = database
        self.owner = owner
        self.embedder = embedder
        self.summarize = summarize

    def _conversation(self, connection, conversation_id, *, lock=False):
        query = (
            "SELECT * FROM agent_chat.conversations WHERE id=%s AND user_id=%s "
            "AND deleted_at IS NULL"
        )
        if lock:
            query += " FOR NO KEY UPDATE NOWAIT"
        row = connection.execute(query, (conversation_id, self.owner)).fetchone()
        if row is None:
            raise ChatError("conversation_not_found", 404)
        return row

    def _memory(self, connection, memory_id, *, lock=False):
        query = (
            "SELECT id, content, source, source_conversation_id, created_at, updated_at "
            "FROM agent_chat.memories WHERE id=%s AND user_id=%s"
        )
        if lock:
            query += " FOR NO KEY UPDATE NOWAIT"
        row = connection.execute(query, (memory_id, self.owner)).fetchone()
        if row is None:
            raise ChatError("memory_not_found", 404)
        return Memory.model_validate(row)

    def _vector(self, content, *, query=False):
        try:
            vector = validate_vectors(self.embedder.embed([content], query=query), 1)[0]
            return json.dumps(vector, allow_nan=False, separators=(",", ":"))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ChatError("embedding_unavailable") from exc

    def _insert(self, connection, request, vector):
        row = connection.execute(
            "INSERT INTO agent_chat.memories "
            "(user_id,content,source,source_conversation_id,embedding,embedding_model) "
            "VALUES (%s,%s,%s,%s,%s::public.vector,%s) RETURNING "
            "id, content, source, source_conversation_id, created_at, updated_at",
            (
                self.owner,
                request.content,
                request.source,
                request.source_conversation_id,
                vector,
                self.embedder.model,
            ),
        ).fetchone()
        return Memory.model_validate(row)

    def create(self, request: MemoryCreate) -> Memory:
        with self.database.connect() as connection:
            if request.source_conversation_id is not None:
                self._conversation(connection, request.source_conversation_id, lock=True)
            return self._insert(connection, request, self._vector(request.content))

    def get(self, memory_id: UUID) -> Memory:
        with self.database.connect() as connection:
            return self._memory(connection, memory_id)

    def list(self, *, limit=50, offset=0) -> list[Memory]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid limit")
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise ValueError("invalid offset")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, content, source, source_conversation_id, created_at, updated_at "
                "FROM agent_chat.memories WHERE user_id=%s "
                "ORDER BY updated_at DESC,id DESC LIMIT %s OFFSET %s",
                (self.owner, limit, offset),
            ).fetchall()
            return [Memory.model_validate(row) for row in rows]

    def update(self, memory_id: UUID, request: MemoryUpdate) -> Memory:
        with self.database.connect() as connection:
            self._memory(connection, memory_id, lock=True)
            row = connection.execute(
                "UPDATE agent_chat.memories SET content=%s,embedding=%s::public.vector, "
                "embedding_model=%s, "
                "updated_at=clock_timestamp() WHERE id=%s AND user_id=%s RETURNING "
                "id, content, source, source_conversation_id, created_at, updated_at",
                (
                    request.content,
                    self._vector(request.content),
                    self.embedder.model,
                    memory_id,
                    self.owner,
                ),
            ).fetchone()
            return Memory.model_validate(row)

    def delete(self, memory_id: UUID):
        with self.database.connect() as connection:
            self._memory(connection, memory_id, lock=True)
            connection.execute(
                "DELETE FROM agent_chat.memories WHERE id=%s AND user_id=%s",
                (memory_id, self.owner),
            )

    def search(self, request: MemorySearch) -> list[MemoryHit]:
        with self.database.connect() as connection:
            eligible = connection.execute(
                "SELECT 1 FROM agent_chat.memories WHERE user_id=%s AND embedding IS NOT NULL "
                "AND embedding_model=%s LIMIT 1",
                (self.owner, self.embedder.model),
            ).fetchone()
            if eligible is None:
                return []
            vector = self._vector(request.query, query=True)
            rows = connection.execute(
                "SELECT id, content, source, source_conversation_id, created_at, updated_at, "
                "1-(embedding <=> %s::public.vector) AS score "
                "FROM agent_chat.memories WHERE user_id=%s AND embedding IS NOT NULL "
                "AND embedding_model=%s AND 1-(embedding <=> %s::public.vector)>=%s "
                "ORDER BY embedding <=> %s::public.vector, id LIMIT %s",
                (
                    vector,
                    self.owner,
                    self.embedder.model,
                    vector,
                    request.min_score,
                    vector,
                    request.limit,
                ),
            ).fetchall()
            return [
                MemoryHit(
                    memory=Memory.model_validate({k: v for k, v in row.items() if k != "score"}),
                    score=max(-1.0, min(1.0, row["score"])),
                )
                for row in rows
            ]

    def _history(self, connection, conversation_id):
        rows = connection.execute(
            "SELECT id,role,content FROM agent_chat.messages WHERE conversation_id=%s "
            "ORDER BY seq LIMIT 1001",
            (conversation_id,),
        ).fetchall()
        if not rows:
            raise ChatError("conversation_empty", 409)
        messages = [{"role": row["role"], "content": row["content"][0]["text"]} for row in rows]
        if len(rows) > 1000 or sum(len(m["content"]) for m in messages) > 48000:
            raise ChatError("conversation_too_large", 413)
        return messages, rows[-1]["id"]

    def _summary(self, messages, purpose):
        if self.summarize is None:
            raise ChatError("summarization_unavailable")
        try:
            result = self.summarize(messages, purpose)
            if type(result) is not str or len(result) > 16000:
                raise ValueError("invalid summary")
            valid_text(result)
            return result
        except ChatError:
            raise
        except Exception as exc:
            raise ChatError("summarization_unavailable") from exc

    def extract(self, conversation_id: UUID) -> list[Memory]:
        with self.database.connect() as connection:
            conversation = self._conversation(connection, conversation_id, lock=True)
            marker = conversation["memory_processed_at"]
            rows = connection.execute(
                "SELECT role,content,created_at FROM agent_chat.messages "
                "WHERE conversation_id=%s AND (%s::timestamptz IS NULL OR created_at>%s) "
                "ORDER BY seq LIMIT 1001",
                (conversation_id, marker, marker),
            ).fetchall()
            if not rows:
                return []
            messages = [{"role": row["role"], "content": row["content"][0]["text"]} for row in rows]
            if len(rows) > 1000 or sum(len(message["content"]) for message in messages) > 48000:
                raise ChatError("conversation_too_large", 413)
            messages = [message for message in messages if message["content"].strip()]
            if not messages:
                return []
            processed_through = max(row["created_at"] for row in rows)
            result = self._summary(messages, "extract")
            try:
                values = strict_json(result)
                if type(values) is not list or len(values) > 10:
                    raise ValueError("invalid memories")
                requests = [
                    MemoryCreate(
                        content=value, source="inferred", source_conversation_id=conversation_id
                    )
                    for value in values
                ]
            except (ValueError, TypeError, RecursionError, ValidationError) as exc:
                raise ChatError("invalid_memory_extraction", 502) from exc
            memories = [
                self._insert(connection, request, self._vector(request.content))
                for request in requests
            ]
            connection.execute(
                "UPDATE agent_chat.conversations SET memory_processed_at=%s, "
                "updated_at=clock_timestamp() WHERE id=%s AND user_id=%s",
                (processed_through, conversation_id, self.owner),
            )
            return memories

    def compact(self, conversation_id: UUID) -> Conversation:
        with self.database.connect() as connection:
            self._conversation(connection, conversation_id, lock=True)
            messages, through_id = self._history(connection, conversation_id)
            result = self._summary(messages, "compact")
            if len(result) > 8000:
                raise ChatError("invalid_conversation_summary", 502)
            row = connection.execute(
                "UPDATE agent_chat.conversations SET summary=%s,compacted_through_message_id=%s, "
                "updated_at=clock_timestamp() WHERE id=%s AND user_id=%s "
                "RETURNING id,title,summary,compacted_through_message_id,memory_processed_at, "
                "created_at,updated_at",
                (result, through_id, conversation_id, self.owner),
            ).fetchone()
            return Conversation.model_validate(row)
