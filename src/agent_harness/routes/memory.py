from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBearer

from ..models.chat import Conversation
from ..models.memory import (
    EmbeddingRequest,
    EmbeddingResponse,
    Memory,
    MemoryCreate,
    MemoryHit,
    MemorySearch,
    MemoryUpdate,
)
from ..services.memory import MemoryService
from .common import require_service

router = APIRouter(
    prefix="/api", tags=["Memory and embeddings"], dependencies=[Depends(HTTPBearer())]
)


def memory_service(request: Request) -> MemoryService:
    return require_service(request, "memory_service")


Service = Annotated[MemoryService, Depends(memory_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]


@router.post("/embeddings", response_model=EmbeddingResponse)
def embeddings(body: EmbeddingRequest, service: Service):
    return EmbeddingResponse(
        model=service.embedder.model,
        embeddings=service.embedder.embed(body.texts, query=body.query),
    )


@router.post("/memories/search", response_model=list[MemoryHit])
def search_memories(body: MemorySearch, service: Service):
    return service.search(body)


@router.post("/memories", response_model=Memory, status_code=201)
def create_memory(body: MemoryCreate, service: Service):
    return service.create(body)


@router.get("/memories", response_model=list[Memory])
def list_memories(service: Service, limit: Limit = 50, offset: Offset = 0):
    return service.list(limit=limit, offset=offset)


@router.get("/memories/{memory_id}", response_model=Memory)
def get_memory(memory_id: UUID, service: Service):
    return service.get(memory_id)


@router.patch("/memories/{memory_id}", response_model=Memory)
def update_memory(memory_id: UUID, body: MemoryUpdate, service: Service):
    return service.update(memory_id, body)


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: UUID, service: Service):
    service.delete(memory_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/memories/extract", response_model=list[Memory])
def extract_memories(conversation_id: UUID, service: Service):
    return service.extract(conversation_id)


@router.post("/conversations/{conversation_id}/compact", response_model=Conversation)
def compact_conversation(conversation_id: UUID, service: Service):
    return service.compact(conversation_id)
