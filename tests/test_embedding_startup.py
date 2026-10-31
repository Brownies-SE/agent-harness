import pytest

from agent_harness import Policy
from agent_harness.services.database import ChatError
from agent_harness.services.embeddings import LocalEmbeddings


def test_embedding_endpoint_must_be_local_before_startup():
    with pytest.raises(ChatError, match="embedding_unavailable"):
        LocalEmbeddings(Policy(), "http://8.8.8.8")
