from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

CAPABILITIES = frozenset(
    {
        "filesystem.read",
        "filesystem.write",
        "network",
        "subprocess",
        "process.read",
        "process.write",
        "docker.read",
        "docker.write",
        "git.read",
        "git.write",
    }
)


@dataclass(frozen=True)
class Policy:
    local_only: bool = True
    telemetry: bool = False
    roots: tuple[Path, ...] = ()
    rules: Mapping[str, str] = field(default_factory=dict)
    network_allow: tuple[str, ...] = ()
    network_ports: tuple[int, ...] = ()
    sensitive_allow: tuple[Path, ...] = ()

    def __post_init__(self):
        if type(self.local_only) is not bool or type(self.telemetry) is not bool:
            raise ValueError("security flags must be boolean")
        if any(
            k not in CAPABILITIES or v not in {"allow", "deny", "ask"}
            for k, v in self.rules.items()
        ):
            raise ValueError("unknown capability or decision")
        if any(type(p) is not int or not 1 <= p <= 65535 for p in self.network_ports):
            raise ValueError("invalid port")
        if any(
            not isinstance(h, str) or not h or h != h.lower() or any(c in h for c in "/@*\\")
            for h in self.network_allow
        ):
            raise ValueError("network allow entries must be exact lowercase hosts")
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))
        object.__setattr__(
            self, "roots", tuple(Path(p).expanduser().resolve(strict=True) for p in self.roots)
        )
        if any(not p.is_dir() for p in self.roots):
            raise ValueError("filesystem root must be a directory")
        object.__setattr__(
            self,
            "sensitive_allow",
            tuple(Path(p).expanduser().absolute() for p in self.sensitive_allow),
        )
        object.__setattr__(self, "network_allow", tuple(self.network_allow))
        object.__setattr__(self, "network_ports", tuple(self.network_ports))

    def decision(self, capability: str) -> str:
        return self.rules.get(capability, "deny") if capability in CAPABILITIES else "deny"
