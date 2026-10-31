from pydantic import Field

from .base import StrictModel


class EmbeddingConfig(StrictModel):
    endpoint: str = Field(min_length=1, max_length=8192)
    model: str = Field(default="mxbai-embed-large", min_length=1, max_length=200)
