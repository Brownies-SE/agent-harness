"""Private local blobs addressed only by host-generated identifiers."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path
from uuid import UUID, uuid4

from ..models.files import MAX_FILE_BYTES, MIME_PATTERN, FileUpload, StoredFile
from .database import ChatError, Database


class FileService:
    def __init__(self, database: Database, owner: UUID, root, *, tool_roots=()):
        root = Path(root)
        if not root.is_absolute() or root.is_symlink():
            raise ValueError("private file root must be an absolute directory")
        resolved = root.resolve()
        if any(resolved.is_relative_to(Path(tool_root).resolve()) for tool_root in tool_roots):
            raise ValueError("private file root overlaps tool filesystem roots")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(self._root_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            self.close()
            raise ValueError("private file root must be owned by host user with mode 0700")
        self.database = database
        self.owner = owner

    def close(self):
        descriptor = getattr(self, "_root_fd", None)
        if descriptor is not None:
            self._root_fd = None
            os.close(descriptor)

    def __del__(self):
        self.close()

    @staticmethod
    def _metadata(row):
        return StoredFile.model_validate(
            {key: value for key, value in row.items() if key not in ("storage_key", "user_id")}
        )

    def _row(self, connection, file_id, *, lock=False):
        query = (
            "SELECT id,conversation_id,filename,mime_type,size_bytes,origin,created_at,storage_key "
            "FROM agent_chat.files WHERE id=%s AND user_id=%s"
        )
        if lock:
            query += " FOR UPDATE NOWAIT"
        row = connection.execute(query, (file_id, self.owner)).fetchone()
        if row is None:
            raise ChatError("file_not_found", 404)
        # A corrupted row must never turn into an arbitrary path or another blob identifier.
        if row["storage_key"] != file_id.hex:
            raise ChatError("file_storage_unavailable")
        return row

    def upload(self, request: FileUpload) -> StoredFile:
        file_id = uuid4()
        key = file_id.hex
        created = False
        try:
            with self.database.connect() as connection:
                if request.conversation_id is not None:
                    conversation = connection.execute(
                        "SELECT id FROM agent_chat.conversations WHERE id=%s AND user_id=%s "
                        "FOR NO KEY UPDATE NOWAIT",
                        (request.conversation_id, self.owner),
                    ).fetchone()
                    if conversation is None:
                        raise ChatError("conversation_not_found", 404)
                content = base64.b64decode(request.content_base64, validate=True)
                descriptor = os.open(
                    key,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._root_fd,
                )
                created = True
                with os.fdopen(descriptor, "wb") as blob:
                    blob.write(content)
                    blob.flush()
                    os.fsync(blob.fileno())
                row = connection.execute(
                    "INSERT INTO agent_chat.files "
                    "(id,user_id,conversation_id,storage_key,filename,mime_type,size_bytes,origin) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'upload') RETURNING "
                    "id,conversation_id,filename,mime_type,size_bytes,origin,created_at",
                    (
                        file_id,
                        self.owner,
                        request.conversation_id,
                        key,
                        request.filename,
                        request.mime_type,
                        len(content),
                    ),
                ).fetchone()
                result = self._metadata(row)
            return result
        except BaseException as exc:
            if created:
                try:
                    os.unlink(key, dir_fd=self._root_fd)
                except FileNotFoundError:
                    pass
            if isinstance(exc, OSError):
                raise ChatError("file_storage_unavailable") from exc
            raise

    def get(self, file_id: UUID) -> StoredFile:
        with self.database.connect() as connection:
            return self._metadata(self._row(connection, file_id))

    def list(self, *, limit=50, offset=0) -> list[StoredFile]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid limit")
        if type(offset) is not int or not 0 <= offset <= 2147483647:
            raise ValueError("invalid offset")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id,conversation_id,filename,mime_type,size_bytes,origin,created_at "
                "FROM agent_chat.files WHERE user_id=%s ORDER BY created_at DESC,id DESC "
                "LIMIT %s OFFSET %s",
                (self.owner, limit, offset),
            ).fetchall()
            return [self._metadata(row) for row in rows]

    def download(self, file_id: UUID) -> tuple[StoredFile, bytes]:
        with self.database.connect() as connection:
            row = self._row(connection, file_id, lock=True)
            metadata = self._metadata(row)
            try:
                descriptor = os.open(
                    row["storage_key"],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=self._root_fd,
                )
                with os.fdopen(descriptor, "rb") as blob:
                    info = os.fstat(blob.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_size > MAX_FILE_BYTES
                        or info.st_size != metadata.size_bytes
                    ):
                        raise ChatError("file_storage_unavailable")
                    content = blob.read(MAX_FILE_BYTES + 1)
                if len(content) != metadata.size_bytes:
                    raise ChatError("file_storage_unavailable")
            except OSError as exc:
                raise ChatError("file_storage_unavailable") from exc
            if metadata.mime_type is not None and not MIME_PATTERN.fullmatch(metadata.mime_type):
                metadata = metadata.model_copy(update={"mime_type": None})
            return metadata, content

    def delete(self, file_id: UUID):
        with self.database.connect() as connection:
            row = self._row(connection, file_id, lock=True)
            connection.execute(
                "DELETE FROM agent_chat.files WHERE id=%s AND user_id=%s",
                (file_id, self.owner),
            )
        # Only unlink after a successful database commit. A failed unlink may leave a
        # private orphan, but a failed commit must preserve still-referenced content.
        try:
            os.unlink(row["storage_key"], dir_fd=self._root_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ChatError("file_storage_unavailable") from exc
