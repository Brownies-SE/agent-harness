import pytest

from agent_harness import Policy, SecurityError
from agent_harness.services.database import ChatError
from agent_harness.services.embeddings import LocalEmbeddings


def test_provider_input_rejection_is_a_client_error(monkeypatch):
    def reject(*args, **kwargs):
        kwargs["response_headers"]["__status_code__"] = "400"
        raise SecurityError("http_status_denied")

    monkeypatch.setattr("agent_harness.services.embeddings.transport", reject)
    with pytest.raises(ChatError, match="embedding_input_rejected") as exc:
        LocalEmbeddings(Policy(), "http://127.0.0.1:11434").embed(["too many tokens"])
    assert exc.value.status_code == 422
