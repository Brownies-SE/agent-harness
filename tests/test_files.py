import base64
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_harness.models.files import FileUpload
from agent_harness.services.database import ChatError
from agent_harness.services.files import FileService


@pytest.mark.parametrize("name", ["../secret", "a/b", "a\\b", "bad\r\nHeader", "\x00", ".."])
def test_display_filename_traversal_and_headers_rejected(name):
    with pytest.raises(ValidationError):
        FileUpload(filename=name, content_base64="aGk=")


def test_client_cannot_choose_storage_key_or_origin():
    with pytest.raises(ValidationError):
        FileUpload(filename="ok", content_base64="aGk=", storage_key="../secret")
    with pytest.raises(ValidationError):
        FileUpload(filename="ok", content_base64="aGk=", origin="generated")


def test_blob_root_must_be_outside_agent_tool_roots(tmp_path):
    with pytest.raises(ValueError):
        FileService(None, uuid4(), tmp_path / "blobs", tool_roots=(tmp_path,))


def test_file_real_disk_roundtrip_owner_isolation_and_symlink(chat_database, tmp_path):
    database, owner = chat_database
    files = FileService(database, owner, tmp_path / "blobs")
    foreign = FileService(database, uuid4(), tmp_path / "blobs")
    payload = b"\x00binary\xff data"
    uploaded = files.upload(
        FileUpload(filename="sample.bin", content_base64=base64.b64encode(payload).decode())
    )
    assert files.download(uploaded.id)[1] == payload
    assert files.get(uploaded.id) == uploaded
    assert foreign.list() == []
    for operation in (
        lambda: foreign.get(uploaded.id),
        lambda: foreign.download(uploaded.id),
        lambda: foreign.delete(uploaded.id),
    ):
        with pytest.raises(ChatError) as error:
            operation()
        assert error.value.status_code == 404
    blob = tmp_path / "blobs" / uploaded.id.hex
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    blob.unlink()
    blob.symlink_to(outside)
    with pytest.raises(ChatError):
        files.download(uploaded.id)
    files.delete(uploaded.id)
    assert outside.read_bytes() == b"secret"
    assert files.list() == []


def test_foreign_conversation_rejected_before_disk_write(chat_database, tmp_path):
    database, owner = chat_database
    files = FileService(database, owner, tmp_path / "blobs")
    with pytest.raises(ChatError) as error:
        files.upload(FileUpload(filename="sample", content_base64="aGk=", conversation_id=uuid4()))
    assert error.value.status_code == 404
    assert list((tmp_path / "blobs").iterdir()) == []


def test_storage_insert_failure_cleans_up_blob(tmp_path):
    class BrokenDatabase:
        def connect(self):
            raise ChatError("chat_storage_unavailable")

    files = FileService(BrokenDatabase(), uuid4(), tmp_path / "blobs")
    with pytest.raises(ChatError):
        files.upload(FileUpload(filename="sample", content_base64="aGk="))
    assert list((tmp_path / "blobs").iterdir()) == []


def test_existing_public_blob_directory_is_rejected(tmp_path):
    blob_root = tmp_path / "blobs"
    blob_root.mkdir(mode=0o755)
    blob_root.chmod(0o755)
    with pytest.raises(ValueError):
        FileService(None, uuid4(), blob_root)


def test_database_commit_failure_cleans_created_blob(chat_database, tmp_path):
    from contextlib import contextmanager

    database, owner = chat_database

    class FailingCommit:
        @contextmanager
        def connect(self):
            with database.connect() as connection:
                yield connection
                raise ChatError("chat_storage_unavailable")

    files = FileService(FailingCommit(), owner, tmp_path / "blobs")
    with pytest.raises(ChatError):
        files.upload(FileUpload(filename="sample", content_base64="aGk="))
    assert list((tmp_path / "blobs").iterdir()) == []
    assert FileService(database, owner, tmp_path / "blobs").list() == []


def test_tampered_storage_key_cannot_read_or_delete_other_paths(chat_database, tmp_path):
    database, owner = chat_database
    files = FileService(database, owner, tmp_path / "blobs")
    uploaded = files.upload(FileUpload(filename="sample", content_base64="aGk="))
    other = files.upload(FileUpload(filename="other", content_base64="c2FmZQ=="))
    with database.connect() as connection:
        connection.execute(
            "UPDATE agent_chat.files SET storage_key=%s WHERE id=%s", (other.id.hex, uploaded.id)
        )
    for operation in (lambda: files.download(uploaded.id), lambda: files.delete(uploaded.id)):
        with pytest.raises(ChatError):
            operation()
    assert files.download(other.id)[1] == b"safe"


def test_delete_commit_failure_preserves_referenced_blob(chat_database, tmp_path):
    from contextlib import contextmanager

    database, owner = chat_database
    files = FileService(database, owner, tmp_path / "blobs")
    uploaded = files.upload(FileUpload(filename="sample", content_base64="aGk="))

    class FailingCommit:
        @contextmanager
        def connect(self):
            with database.connect() as connection:
                yield connection
                raise ChatError("chat_storage_unavailable")

    failing = FileService(FailingCommit(), owner, tmp_path / "blobs")
    with pytest.raises(ChatError):
        failing.delete(uploaded.id)
    assert files.get(uploaded.id) == uploaded
    assert files.download(uploaded.id)[1] == b"hi"
