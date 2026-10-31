"""Host-configured, local-only Ollama embeddings with pinned network transport."""

import json
import math
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from ..core.errors import SecurityError
from ..model import strict_json
from ..models.memory import EmbeddingRequest
from ..tools.network import check_url, transport
from .database import ChatError

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def validate_vectors(value, count):
    if type(value) is not list or len(value) != count:
        raise ValueError("invalid embeddings")
    result = []
    for vector in value:
        if type(vector) is not list or len(vector) != 1024:
            raise ValueError("invalid embedding dimension")
        if any(type(item) not in (int, float) or not math.isfinite(item) for item in vector):
            raise ValueError("invalid embedding value")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("invalid embedding norm")
        result.append([float(item) for item in vector])
    return result


class LocalEmbeddings:
    def __init__(self, policy, endpoint, model="mxbai-embed-large"):
        if not policy.local_only:
            raise ValueError("embeddings require local-only policy")
        parsed = urlsplit(endpoint)
        if parsed.path not in ("", "/", "/api/embed") or parsed.query or parsed.fragment:
            raise ValueError("invalid embedding endpoint")
        if type(model) is not str or not model or len(model) > 200:
            raise ValueError("invalid embedding model")
        self.policy = policy
        self.endpoint = urlunsplit((parsed.scheme, parsed.netloc, "/api/embed", "", ""))
        self.model = model
        try:
            check_url(self.policy, self.endpoint, inference=True)
        except SecurityError as exc:
            raise ChatError("embedding_unavailable") from exc

    def embed(self, texts: list[str], *, query=False) -> list[list[float]]:
        response_headers = {}
        try:
            request = EmbeddingRequest(texts=texts, query=query)
            inputs = [QUERY_PREFIX + text if request.query else text for text in request.texts]
            target = check_url(self.policy, self.endpoint, inference=True)
            body = json.dumps({"model": self.model, "input": inputs, "truncate": False}).encode()
            response = strict_json(
                transport(
                    target,
                    method="POST",
                    body=body,
                    limit=1024 * 1024,
                    timeout=120,
                    response_headers=response_headers,
                )
            )
            if type(response) is not dict:
                raise ValueError("invalid embedding response")
            return validate_vectors(response.get("embeddings"), len(inputs))
        except SecurityError as exc:
            if response_headers.get("__status_code__") == "400":
                raise ChatError("embedding_input_rejected", 422) from exc
            raise ChatError("embedding_unavailable") from exc
        except (
            ValueError,
            TypeError,
            OverflowError,
            RecursionError,
            ValidationError,
        ) as exc:
            raise ChatError("embedding_unavailable") from exc
