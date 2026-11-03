"""HTTP routes for Layer 1: health, model catalog, chat (blocking and streaming, with tools from Layer 4)."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sse_starlette import EventSourceResponse

from app.api.deps import get_chat_service, get_registry
from app.llm.registry import ProviderRegistry
from app.schemas.chat import ChatRequest, ChatResponse, ModelList, StreamEvent
from app.services.chat_service import ChatService

health_router = APIRouter(tags=["health"])
router = APIRouter(prefix="/v1", tags=["chat"])

Registry = Annotated[ProviderRegistry, Depends(get_registry)]
Service = Annotated[ChatService, Depends(get_chat_service)]


@health_router.get("/health")
async def health(request: Request, registry: Registry) -> dict:
    database = request.app.state.database
    if database is None:
        db_status = "disabled"
    else:
        db_status = "ok" if await database.ping() else "unavailable"
    rag_status = await request.app.state.rag.status() if db_status == "ok" else "disabled"
    return {
        "status": "ok",
        "providers": registry.provider_names,
        "database": db_status,
        "rag": rag_status,
        "tools": db_status,  # the MCP server registry lives in the database
    }


@router.get("/models", response_model=ModelList)
async def list_models(registry: Registry) -> ModelList:
    return ModelList(default=registry.default_model, data=registry.catalog())


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, service: Service) -> ChatResponse:
    return await service.complete(body)


@router.post(
    "/chat/stream",
    response_class=EventSourceResponse,
    responses={200: {"description": "Server-sent events; each `data` is a StreamEvent JSON object."}},
)
async def chat_stream(body: ChatRequest, service: Service) -> EventSourceResponse:
    events = service.stream(body)
    # Pull the first event now, so errors before the first token become normal HTTP errors.
    first = await anext(events)
    return EventSourceResponse(_sse(first, events), ping=15)


async def _sse(first: StreamEvent, rest: AsyncIterator[StreamEvent]) -> AsyncIterator[dict]:
    yield {"data": first.model_dump_json(exclude_none=True)}
    async for event in rest:
        yield {"data": event.model_dump_json(exclude_none=True)}
