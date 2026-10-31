from typing import Literal

from .base import StrictModel


class ModelStatus(StrictModel):
    kind: Literal["chat", "embeddings"]
    name: str
    endpoint: str
    ready: bool
    disk_bytes: int | None = None
    loaded_bytes: int | None = None


class SystemStatus(StrictModel):
    local_only: bool
    telemetry: bool
    free_disk_bytes: int
    models: list[ModelStatus]
    permissions: dict[str, str]
