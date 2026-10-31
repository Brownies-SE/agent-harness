from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBearer

from agent_harness.models.execution import (
    ApprovalDecision,
    CreateToolCall,
    PermissionUpdate,
    ToolCall,
    ToolDescription,
    ToolPermission,
)
from agent_harness.services.execution import ExecutionService

from .common import require_service

router = APIRouter(
    prefix="/api", tags=["Tools and approvals"], dependencies=[Depends(HTTPBearer())]
)


def execution_service(request: Request) -> ExecutionService:
    return require_service(request, "execution_service")


Service = Annotated[ExecutionService, Depends(execution_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]


@router.get("/tools", response_model=list[ToolDescription])
def list_tools(service: Service):
    return service.list_tools()


@router.get("/tools/search", response_model=list[ToolDescription])
def search_tools(
    service: Service, query: Annotated[str, Query(min_length=1, max_length=200)], limit: Limit = 20
):
    return [tool for tool in service.list_tools() if query.lower() in tool.name.lower()][:limit]


@router.get("/tool-permissions", response_model=list[ToolPermission])
def list_permissions(service: Service):
    return service.permissions()


@router.put("/tool-permissions/{tool_key:path}", response_model=ToolPermission)
def set_permission(tool_key: str, body: PermissionUpdate, service: Service):
    return service.set_permission(tool_key, body.policy)


@router.delete("/tool-permissions/{tool_key:path}", status_code=204)
def delete_permission(tool_key: str, service: Service):
    service.delete_permission(tool_key)
    return Response(status_code=204)


@router.post("/tool-calls", response_model=ToolCall, status_code=201)
def create_call(body: CreateToolCall, service: Service):
    return service.call(body)


@router.get("/tool-calls", response_model=list[ToolCall])
def list_calls(
    service: Service, conversation_id: UUID | None = None, limit: Limit = 50, offset: Offset = 0
):
    return service.list(conversation_id=conversation_id, limit=limit, offset=offset)


@router.get("/tool-calls/{call_id}", response_model=ToolCall)
def get_call(call_id: UUID, service: Service):
    return service.get(call_id)


@router.post("/tool-calls/{call_id}/approval", response_model=ToolCall)
def approve_call(call_id: UUID, body: ApprovalDecision, service: Service):
    return service.approve(call_id, body)
