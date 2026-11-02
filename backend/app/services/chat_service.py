"""Chat orchestration: the one place every chat request passes through.

Later layers plug in here, in this order:
    prompts -> auth/RBAC -> input guardrails -> cache -> router -> RAG / MCP tools
    -> LLM call -> output guardrails -> observability / evals logging
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator

from app.core.errors import AppError, ProviderError
from app.llm.base import LLMRequest
from app.llm.registry import ProviderRegistry
from app.schemas.chat import ChatRequest, ChatResponse, StreamEvent

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def complete(self, request: ChatRequest) -> ChatResponse:
        provider, model = self._registry.resolve(request.model)
        request_id = _new_id()
        started = time.perf_counter()
        result = await provider.complete(_to_llm_request(request, model))
        latency_ms = _ms_since(started)
        logger.info(
            "chat completed",
            extra={
                "request_id": request_id,
                "provider": provider.name,
                "model": result.model,
                "latency_ms": latency_ms,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "finish_reason": result.finish_reason,
            },
        )
        return ChatResponse(
            id=request_id,
            provider=provider.name,
            model=result.model,
            content=result.content,
            finish_reason=result.finish_reason,
            usage=result.usage,
            latency_ms=latency_ms,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Yield `start`, `delta`..., then `done` (or `error` if the provider fails mid-stream).

        Errors before the first token (bad model, auth, rate limit) are raised instead, so the
        route can still return a proper HTTP status: the provider is called before `start`.
        """
        provider, model = self._registry.resolve(request.model)
        request_id = _new_id()
        started = time.perf_counter()
        chunks = provider.stream(_to_llm_request(request, model))

        try:
            first = await anext(chunks)  # raises ProviderError before any bytes are sent
        except StopAsyncIteration:
            raise ProviderError("Provider returned an empty stream", provider=provider.name) from None
        yield StreamEvent(type="start", id=request_id, provider=provider.name, model=first.model or model)

        try:
            chunk = first
            while True:
                if chunk.done:
                    latency_ms = _ms_since(started)
                    logger.info(
                        "chat stream completed",
                        extra={
                            "request_id": request_id,
                            "provider": provider.name,
                            "model": chunk.model or model,
                            "latency_ms": latency_ms,
                            "input_tokens": chunk.usage.input_tokens if chunk.usage else 0,
                            "output_tokens": chunk.usage.output_tokens if chunk.usage else 0,
                            "finish_reason": chunk.finish_reason,
                        },
                    )
                    yield StreamEvent(
                        type="done",
                        id=request_id,
                        provider=provider.name,
                        model=chunk.model or model,
                        finish_reason=chunk.finish_reason or "other",
                        usage=chunk.usage,
                        latency_ms=latency_ms,
                    )
                    return
                if chunk.delta:
                    yield StreamEvent(type="delta", id=request_id, delta=chunk.delta)
                chunk = await anext(chunks)
        except StopAsyncIteration:
            error: AppError = ProviderError("Stream ended without a final event", provider=provider.name)
        except AppError as exc:
            error = exc
        logger.warning(
            "chat stream failed",
            extra={"request_id": request_id, "provider": provider.name, "error_code": error.code},
        )
        yield StreamEvent(type="error", id=request_id, error=error.to_dict())


def _to_llm_request(request: ChatRequest, model: str) -> LLMRequest:
    return LLMRequest(
        model=model,
        messages=request.messages,
        system=request.system,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        provider_options=request.provider_options,
    )


def _new_id() -> str:
    return f"chat_{uuid.uuid4().hex}"


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
