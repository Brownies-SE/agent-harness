import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..core.errors import SecurityError
from ..model import strict_json
from ..models.status import ModelStatus, SystemStatus
from ..tools.network import check_url, transport


class StatusService:
    def __init__(self, config, embedding_config):
        self.config = config
        self.embedding_config = embedding_config

    def get(self):
        models = []
        for kind, name, endpoint in (
            ("chat", self.config.model, self.config.endpoint),
            ("embeddings", self.embedding_config.model, self.embedding_config.endpoint),
        ):
            parsed = urlsplit(endpoint)
            base = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            ready = False
            size = None
            loaded = None
            try:
                payload = strict_json(
                    transport(
                        check_url(self.config.policy, base + "/api/tags", inference=True), timeout=5
                    )
                )
                for model in payload.get("models", []):
                    if model.get("name") in {name, name + ":latest"}:
                        ready = True
                        value = model.get("size")
                        size = value if type(value) is int and value >= 0 else None
                active = strict_json(
                    transport(
                        check_url(self.config.policy, base + "/api/ps", inference=True), timeout=5
                    )
                )
                for model in active.get("models", []):
                    if model.get("name") in {name, name + ":latest"}:
                        value = model.get("size")
                        loaded = value if type(value) is int and value >= 0 else None
            except (SecurityError, ValueError, TypeError, AttributeError, RecursionError):
                ready = False
            models.append(
                ModelStatus(
                    kind=kind,
                    name=name,
                    endpoint=endpoint,
                    ready=ready,
                    disk_bytes=size,
                    loaded_bytes=loaded,
                )
            )
        return SystemStatus(
            local_only=self.config.policy.local_only,
            telemetry=self.config.policy.telemetry,
            free_disk_bytes=shutil.disk_usage(Path.home()).free,
            models=models,
            permissions=dict(self.config.policy.rules),
        )
