from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, Field

from .base import StrictModel, WireUUID


class CreateToolCall(StrictModel):
    conversation_id: WireUUID
    tool: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,95}$")
    arguments: dict[str, Any] = Field(validation_alias=AliasChoices("arguments", "args"))


class ApprovalDecision(StrictModel):
    decision: Literal["approve", "deny"]


class ToolPermission(StrictModel):
    tool_key: str
    policy: Literal["allow", "ask", "deny"]


class PermissionUpdate(StrictModel):
    policy: Literal["allow", "ask", "deny"]


class ToolDescription(StrictModel):
    name: str
    parameters: dict[str, Any]


class ToolCall(StrictModel):
    id: UUID
    conversation_id: UUID | None
    message_id: UUID | None
    run_id: UUID | None
    tool_key: str
    connection_id: UUID | None
    input: dict[str, Any]
    output: Any = None
    status: Literal["pending", "awaiting_approval", "running", "succeeded", "failed", "denied"]
    error: str | None
    approved_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
