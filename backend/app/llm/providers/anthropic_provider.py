"""Anthropic Claude adapter (official `anthropic` SDK).

Notes for current Claude models:
- `temperature` is not accepted, so it is dropped.
- Reasoning depth is set with `output_config.effort` (see ANTHROPIC_EFFORT).
- Thinking runs by default; only text blocks are returned to callers.
- Server-side refusal fallback is enabled for models that support it (see ANTHROPIC_REFUSAL_FALLBACK).
- Tools (Layer 4): `tool_use` blocks become ToolCalls. The assistant content is replayed unchanged on the
  next round (thinking blocks must come back exactly as sent), followed by one user message holding
  every `tool_result`.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx2

from app.core.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk, ToolCall
from app.schemas.chat import FinishReason, Usage

logger = logging.getLogger(__name__)

NAME = "anthropic"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS = 16_000  # non-streaming: keeps responses well inside HTTP timeouts
DEFAULT_STREAM_MAX_TOKENS = 64_000

_STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "refusal": "refusal",
    "tool_use": "tool_calls",
}


class AnthropicProvider(LLMProvider):
    name = NAME

    def __init__(
        self,
        *,
        api_key: str,
        models: list[str],
        timeout_s: float,
        max_retries: int,
        effort: str | None = None,
        refusal_fallback: bool = True,
        fallback_models: list[str] | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(models)
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_s, "max_retries": max_retries}
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = anthropic.AsyncAnthropic(**kwargs)
        self._effort = effort
        self._refusal_fallback = refusal_fallback
        self._fallback_models = set(fallback_models or [])

    def _params(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        default_max = DEFAULT_STREAM_MAX_TOKENS if stream else DEFAULT_MAX_TOKENS
        messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in request.messages]
        for turn in request.turns:
            messages.append({"role": "assistant", "content": _replayable(turn.provider_state)})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.call_id,
                            "content": r.content,
                            "is_error": r.is_error,
                        }
                        for r in turn.results
                    ],
                }
            )
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or default_max,
            "messages": messages,
        }
        if request.tools:
            params["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    # Streaming: send tool inputs as they're generated. The MCP server validates them.
                    **({"eager_input_streaming": True} if stream else {}),
                }
                for t in request.tools
            ]
        if request.system:
            params["system"] = request.system
        if self._effort:
            params["output_config"] = {"effort": self._effort}
        if self._refusal_fallback and request.model in self._fallback_models:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        if request.temperature is not None:
            logger.debug("dropping temperature: not supported by current Claude models")
        params.update(request.provider_options)
        return params

    async def complete(self, request: LLMRequest) -> LLMResult:
        try:
            message = await self._client.beta.messages.create(**self._params(request, stream=False))
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
        text = "".join(block.text for block in message.content if block.type == "text")
        calls = _tool_calls(message)
        return LLMResult(
            content=text,
            finish_reason=_STOP_REASONS.get(message.stop_reason or "", "other"),
            usage=Usage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
            model=message.model,
            tool_calls=calls,
            provider_state=message.content if calls else None,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.beta.messages.stream(**self._params(request, stream=True)) as stream:
                async for text in stream.text_stream:
                    yield StreamChunk(delta=text)
                message = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
        calls = _tool_calls(message)
        yield StreamChunk(
            done=True,
            finish_reason=_STOP_REASONS.get(message.stop_reason or "", "other"),
            usage=Usage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
            model=message.model,
            tool_calls=calls,
            provider_state=message.content if calls else None,
        )

    async def aclose(self) -> None:
        await self._client.close()


def _tool_calls(message: Any) -> list[ToolCall]:
    """The tool calls Claude asked for. None if it stopped at max_tokens: a cut-off input isn't safe."""
    if message.stop_reason != "tool_use":
        return []
    return [
        ToolCall(id=block.id, name=block.name, arguments=block.input if isinstance(block.input, dict) else {})
        for block in message.content
        if block.type == "tool_use"
    ]


def _replayable(content: list[Any]) -> list[Any]:
    """The assistant content to send back. After a refusal fallback inside the turn, only text blocks from
    before the last `fallback` marker are kept (the declined model's thinking/tool blocks must not return)."""
    last = max((i for i, block in enumerate(content) if block.type == "fallback"), default=None)
    if last is None:
        return list(content)
    return [b for b in content[:last] if b.type == "text"] + list(content[last + 1 :])


def _map_error(exc: anthropic.AnthropicError) -> ProviderError:
    # Most specific first: APITimeoutError subclasses APIConnectionError.
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.DeadlineExceededError)):
        return ProviderTimeoutError("Anthropic request timed out", provider=NAME)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderUnavailableError("Could not reach Anthropic", provider=NAME)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderAuthError("Anthropic rejected the API key", provider=NAME)
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderRateLimitError("Anthropic rate limit hit", provider=NAME)
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return ProviderUnavailableError(f"Anthropic error {exc.status_code}", provider=NAME)
        return ProviderBadRequestError(f"Anthropic rejected the request: {exc.message}", provider=NAME)
    return ProviderError(f"Anthropic error: {exc}", provider=NAME)
