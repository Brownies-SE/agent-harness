import base64

import pytest
from fastapi.testclient import TestClient

from agent_harness import Executor, Policy
from agent_harness.api import create_app
from agent_harness.core.agents import LangGraphAgent
from agent_harness.services.chat import ChatService
from agent_harness.services.execution import ExecutionService
from agent_harness.services.files import FileService
from agent_harness.services.mcp import MCPService
from agent_harness.services.memory import MemoryService
from agent_harness.tools import builtin_tools

AUTH = {"Authorization": "Bearer synthetic-functional-token-at-least-32-characters"}


class Embeddings:
    model = "fixture-space"

    def embed(self, texts, query=False):
        return [[1.0, *([0.0] * 1023)] for text in texts]


class Model:
    def decide(self, messages, tools):
        return {"final": "hello"}


@pytest.fixture
def functional_api(chat_database, tmp_path):
    database, owner = chat_database
    workspace = tmp_path / "project"
    workspace.mkdir()
    policy = Policy(
        roots=(workspace,), rules={"filesystem.read": "allow", "filesystem.write": "ask"}
    )

    def executor_factory(**kwargs):
        executor = Executor(policy, **kwargs)
        for tool in builtin_tools(policy):
            executor.register(tool)
        return executor

    execution = ExecutionService(database, owner, executor_factory)
    memory = MemoryService(
        database,
        owner,
        Embeddings(),
        lambda messages, purpose: (
            '["A synthetic fact"]' if purpose == "extract" else "A safe summary"
        ),
    )
    files = FileService(database, owner, tmp_path / "blobs", tool_roots=policy.roots)
    mcp = MCPService(database, owner, policy, None)
    chat = ChatService(
        database,
        owner,
        lambda: LangGraphAgent(Model(), executor_factory(tool_rules=execution.rules())),
        memory_service=memory,
        execution_service=execution,
    )
    app = create_app(
        chat,
        token=AUTH["Authorization"][7:],
        model_name="fixture-chat",
        memory_service=memory,
        file_service=files,
        mcp_service=mcp,
        execution_service=execution,
    )
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        yield client, workspace
    files.close()


def test_resource_apis_accept_serialized_uuids_and_roundtrip(functional_api):
    client, _ = functional_api
    assert client.get("/api/me").json()["email"].endswith("@localhost")
    assert "read_file" in {
        tool["name"] for tool in client.get("/api/tools/search?query=file").json()
    }
    conversation = client.post("/api/conversations", json={}).json()
    cid = conversation["id"]
    response = client.post(
        "/api/memories", json={"content": "A pear is a fruit", "source_conversation_id": cid}
    )
    assert response.status_code == 201, response.text
    memory = response.json()
    assert (
        client.post("/api/memories/search", json={"query": "fruit"}).json()[0]["memory"]["id"]
        == memory["id"]
    )
    uploaded = client.post(
        "/api/files",
        json={
            "filename": "note.txt",
            "conversation_id": cid,
            "content_base64": base64.b64encode(b"local content").decode(),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    fid = uploaded.json()["id"]
    assert client.get(f"/api/files/{fid}/content").content == b"local content"
    assert client.delete(f"/api/files/{fid}").status_code == 204
    assert client.delete("/api/memories/" + memory["id"]).status_code == 204


def test_api_approval_executes_once_and_cannot_be_replayed(functional_api):
    client, workspace = functional_api
    cid = client.post("/api/conversations", json={}).json()["id"]
    response = client.post(
        "/api/tool-calls",
        json={
            "conversation_id": cid,
            "tool": "write_file",
            "arguments": {"path": "note.txt", "content": "approved"},
        },
    )
    assert response.status_code == 201, response.text
    call = response.json()
    assert call["status"] == "awaiting_approval"
    assert not (workspace / "note.txt").exists()
    path = f"/api/tool-calls/{call['id']}/approval"
    assert client.post(path, json={"decision": "approve"}).json()["status"] == "succeeded"
    assert (workspace / "note.txt").read_text() == "approved"
    assert client.post(path, json={"decision": "approve"}).status_code == 409
    assert client.get("/api/tool-calls?conversation_id=" + cid).json()[0]["id"] == call["id"]


def test_compaction_is_used_without_reusing_message_sequences(functional_api):
    client, _ = functional_api
    cid = client.post("/api/conversations", json={}).json()["id"]
    path = "/api/conversations/" + cid
    assert client.post(path + "/messages", json={"content": "hello"}).status_code == 200
    assert client.post(path + "/compact").status_code == 200
    response = client.post(path + "/messages", json={"content": "next"})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["seq"] == 3
    assert client.post(path + "/memories/extract").status_code == 200
    assert len(client.get(path + "/messages").json()) == 4
