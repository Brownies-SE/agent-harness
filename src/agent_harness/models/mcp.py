from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator

from .base import StrictModel, WireDatetime, WireUUID


class ServerCreate(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
    url: str = Field(min_length=1, max_length=8192)
    transport: Literal["streamable_http", "sse", "stdio"] = "streamable_http"
    auth_type: Literal["none", "oauth", "api_key"] = "none"


class Server(ServerCreate):
    id: UUID
    created_at: datetime


class ConnectionCreate(StrictModel):
    server_id: WireUUID
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    token_expires_at: WireDatetime | None = None
    scopes: list[str] = Field(default_factory=list, max_length=64)
    status: Literal["active", "needs_reauth", "disabled"] = "active"

    @field_validator("access_token", "refresh_token")
    @classmethod
    def bounded_token(cls, value):
        if value is not None:
            raw = value.get_secret_value()
            if not raw or len(raw) > 16384 or any(ord(c) < 32 or ord(c) > 126 for c in raw):
                raise ValueError("invalid credential")
        return value

    @field_validator("token_expires_at")
    @classmethod
    def aware_expiry(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("expiry requires timezone")
        return value

    @field_validator("scopes")
    @classmethod
    def bounded_scopes(cls, value):
        if any(not scope or len(scope) > 256 for scope in value):
            raise ValueError("invalid scope")
        return value


class Connection(StrictModel):
    id: UUID
    server_id: UUID
    status: Literal["active", "needs_reauth", "disabled"]
    token_expires_at: datetime | None
    scopes: list[str]
    created_at: datetime
    has_access_token: bool
    has_refresh_token: bool


class MCPTool(StrictModel):
    id: UUID
    server_id: UUID
    name: str
    description: str | None
    input_schema: dict[str, Any]
    deferred: bool
    fetched_at: datetime


class ConnectionCheck(StrictModel):
    connected: bool
    tools: int
