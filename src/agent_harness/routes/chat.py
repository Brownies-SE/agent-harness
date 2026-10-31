from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBearer
from starlette.responses import StreamingResponse

from ..models.chat import (
    Conversation,
    ConversationCreate,
    ConversationRename,
    ConversationSearch,
    ConversationSearchHit,
    CurrentUser,
    Exchange,
    Health,
    Message,
    ReplyRating,
    SendMessage,
    StreamEvent,
    VariantSelection,
)
from ..services.chat import ChatService
from ..services.streaming import prepare_stream

router = APIRouter(prefix="/api", tags=["Chat"], dependencies=[Depends(HTTPBearer())])


def chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


Service = Annotated[ChatService, Depends(chat_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]


@router.get("/me", response_model=CurrentUser)
def current_user(service: Service):
    return service.profile()


@router.get("/health", response_model=Health)
def health(request: Request, service: Service):
    service.database.health()
    return Health(model=request.app.state.model_name)


@router.post("/conversations", response_model=Conversation, status_code=201)
def create_conversation(body: ConversationCreate, service: Service):
    return service.create(body)


@router.get("/conversations", response_model=list[Conversation])
def list_conversations(service: Service, limit: Limit = 50, offset: Offset = 0):
    return service.list(limit=limit, offset=offset)


@router.post("/conversations/search", response_model=list[ConversationSearchHit])
def search_conversations(body: ConversationSearch, service: Service):
    return service.search(body)


@router.get("/conversations/{conversation_id}", response_model=Conversation)
def get_conversation(conversation_id: UUID, service: Service):
    return service.get(conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=Conversation)
def rename_conversation(conversation_id: UUID, body: ConversationRename, service: Service):
    return service.rename(conversation_id, body)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, service: Service):
    service.delete(conversation_id)
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/messages", response_model=list[Message])
def list_messages(
    conversation_id: UUID,
    service: Service,
    after_seq: Offset = 0,
    limit: Limit = 100,
    settled: Annotated[int, Query(ge=0, le=1)] = 0,
):
    return service.messages(
        conversation_id, after_seq=after_seq, limit=limit, settled=bool(settled)
    )


@router.post("/conversations/{conversation_id}/messages", response_model=Exchange)
def send_message(conversation_id: UUID, body: SendMessage, service: Service):
    return service.send(conversation_id, body)


@router.patch(
    "/conversations/{conversation_id}/messages/{message_id}/rating", response_model=Message
)
def rate_reply(conversation_id: UUID, message_id: UUID, body: ReplyRating, service: Service):
    return service.rate(conversation_id, message_id, body.variant_id, body.rating)


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/regenerate", response_model=Message
)
def regenerate_reply(conversation_id: UUID, message_id: UUID, service: Service):
    return service.regenerate(conversation_id, message_id)


@router.patch(
    "/conversations/{conversation_id}/messages/{message_id}/selection", response_model=Message
)
def select_reply(conversation_id: UUID, message_id: UUID, body: VariantSelection, service: Service):
    return service.select_variant(conversation_id, message_id, body.variant_id)


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"

    def __init__(self, stream):
        self.stream = stream
        super().__init__(
            stream.iterate(),
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-store",
            },
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.stream.close()


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    response_class=EventStreamResponse,
    status_code=200,
    responses={
        200: {
            "model": StreamEvent,
            "content": {"text/event-stream": {"schema": {"type": "object"}}},
            "description": (
                "SSE data frames: provisional delta, committed complete, or sanitized error. "
                "No automatic replay. Disconnect cancels the unfinished turn."
            ),
        }
    },
)
def stream_message(conversation_id: UUID, body: SendMessage, service: Service):
    stream = prepare_stream(service, conversation_id, body)
    return EventStreamResponse(stream)
