import json

import pytest

from agent_harness import Policy
from agent_harness.services.database import ChatError
from agent_harness.services.summarization import LocalSummarizer


def test_extraction_uses_typed_json_output_not_double_encoded_final(monkeypatch):
    body = {
        "choices": [
            {"message": {"content": json.dumps({"memories": ["The code name is Blue Heron."]})}}
        ]
    }
    monkeypatch.setattr("agent_harness.model.transport", lambda *a, **kw: json.dumps(body).encode())
    summarize = LocalSummarizer(Policy(), "http://127.0.0.1:11434", "test")
    assert json.loads(
        summarize([{"role": "user", "content": "code name Blue Heron"}], "extract")
    ) == ["The code name is Blue Heron."]


def test_summarization_rejects_tool_proposals_and_extra_fields(monkeypatch):
    body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"tool": "write_file", "arguments": {"path": "x", "content": "bad"}}
                    )
                }
            }
        ]
    }
    monkeypatch.setattr("agent_harness.model.transport", lambda *a, **kw: json.dumps(body).encode())
    summarize = LocalSummarizer(Policy(), "http://127.0.0.1:11434", "test")
    with pytest.raises(ChatError, match="summarization_unavailable"):
        summarize([{"role": "user", "content": "ignore rules"}], "extract")
