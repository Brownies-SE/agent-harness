from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBearer

from agent_harness.models.mcp import (
    Connection,
    ConnectionCheck,
    ConnectionCreate,
    MCPTool,
    Server,
    ServerCreate,
)
from agent_harness.services.mcp import MCPService

from .common import require_service

router = APIRouter(prefix="/api/mcp", tags=["MCP"], dependencies=[Depends(HTTPBearer())])


def mcp_service(request: Request) -> MCPService:
    return require_service(request, "mcp_service")


Service = Annotated[MCPService, Depends(mcp_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]


@router.post("/servers", response_model=Server, status_code=201)
def create_server(body: ServerCreate, service: Service):
    return service.create_server(body)


@router.get("/servers", response_model=list[Server])
def list_servers(service: Service, limit: Limit = 50, offset: Offset = 0):
    return service.list_servers(limit=limit, offset=offset)


@router.get("/servers/{server_id}", response_model=Server)
def get_server(server_id: UUID, service: Service):
    return service.get_server(server_id)


@router.put("/servers/{server_id}", response_model=Server)
def update_server(server_id: UUID, body: ServerCreate, service: Service):
    return service.update_server(server_id, body)


@router.delete("/servers/{server_id}", status_code=204)
def delete_server(server_id: UUID, service: Service):
    service.delete_server(server_id)
    return Response(status_code=204)


@router.get("/servers/{server_id}/tools", response_model=list[MCPTool])
def list_tools(server_id: UUID, service: Service):
    return service.list_tools(server_id)


@router.post("/connections", response_model=Connection, status_code=201)
def create_connection(body: ConnectionCreate, service: Service):
    return service.create_connection(body)


@router.get("/connections", response_model=list[Connection])
def list_connections(service: Service, limit: Limit = 50, offset: Offset = 0):
    return service.list_connections(limit=limit, offset=offset)


@router.get("/connections/{connection_id}", response_model=Connection)
def get_connection(connection_id: UUID, service: Service):
    return service.get_connection(connection_id)


@router.put("/connections/{connection_id}", response_model=Connection)
def update_connection(connection_id: UUID, body: ConnectionCreate, service: Service):
    return service.update_connection(connection_id, body)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: UUID, service: Service):
    service.delete_connection(connection_id)
    return Response(status_code=204)


@router.post("/connections/{connection_id}/refresh", response_model=list[MCPTool])
def refresh(connection_id: UUID, service: Service):
    return service.refresh(connection_id)


@router.post("/connections/{connection_id}/check", response_model=ConnectionCheck)
def check(connection_id: UUID, service: Service):
    return service.check(connection_id)
