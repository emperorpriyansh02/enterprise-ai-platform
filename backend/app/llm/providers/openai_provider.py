"""OpenAI adapter (official `openai` SDK, Chat Completions API).

`base_url` lets the same adapter talk to Azure OpenAI or any OpenAI-compatible server.
Note: some OpenAI reasoning models reject `temperature`; it is only sent when the caller sets it.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import openai

from app.core.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk
from app.schemas.chat import FinishReason, Usage

NAME = "openai"

_FINISH_REASONS: dict[str, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
}


class OpenAIProvider(LLMProvider):
    name = NAME

    def __init__(
        self,
        *,
        api_key: str,
        models: list[str],
        timeout_s: float,
        max_retries: int,
        base_url: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(models)
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_s, "max_retries": max_retries}
        if base_url:
            kwargs["base_url"] = base_url
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = openai.AsyncOpenAI(**kwargs)

    def _params(self, request: LLMRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)
        params: dict[str, Any] = {"model": request.model, "messages": messages}
        if request.max_tokens is not None:
            params["max_completion_tokens"] = request.max_tokens
        if request.temperature is not None:
            params["temperature"] = request.temperature
        params.update(request.provider_options)
        return params

    async def complete(self, request: LLMRequest) -> LLMResult:
        try:
            completion = await self._client.chat.completions.create(**self._params(request))
        except openai.OpenAIError as exc:
            raise _map_error(exc) from exc
        choice = completion.choices[0]
        refused = bool(getattr(choice.message, "refusal", None))
        return LLMResult(
            content=choice.message.content or choice.message.refusal or "",
            finish_reason="refusal" if refused else _FINISH_REASONS.get(choice.finish_reason or "", "other"),
            usage=_usage(completion.usage),
            model=completion.model,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        params = self._params(request) | {"stream": True, "stream_options": {"include_usage": True}}
        finish_reason: FinishReason = "other"
        usage = Usage()
        model = request.model
        try:
            stream = await self._client.chat.completions.create(**params)
            async for chunk in stream:
                model = chunk.model or model
                if chunk.usage:  # final chunk (no choices) carries token usage
                    usage = _usage(chunk.usage)
                for choice in chunk.choices:
                    if choice.delta.content:
                        yield StreamChunk(delta=choice.delta.content)
                    if getattr(choice.delta, "refusal", None):
                        finish_reason = "refusal"
                        yield StreamChunk(delta=choice.delta.refusal)
                    if choice.finish_reason and finish_reason != "refusal":
                        finish_reason = _FINISH_REASONS.get(choice.finish_reason, "other")
        except openai.OpenAIError as exc:
            raise _map_error(exc) from exc
        yield StreamChunk(done=True, finish_reason=finish_reason, usage=usage, model=model)

    async def aclose(self) -> None:
        await self._client.close()


def _usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    return Usage(input_tokens=usage.prompt_tokens or 0, output_tokens=usage.completion_tokens or 0)


def _map_error(exc: openai.OpenAIError) -> ProviderError:
    # Most specific first: APITimeoutError subclasses APIConnectionError.
    if isinstance(exc, openai.APITimeoutError):
        return ProviderTimeoutError("OpenAI request timed out", provider=NAME)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderUnavailableError("Could not reach OpenAI", provider=NAME)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ProviderAuthError("OpenAI rejected the API key", provider=NAME)
    if isinstance(exc, openai.RateLimitError):
        return ProviderRateLimitError("OpenAI rate limit hit", provider=NAME)
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return ProviderUnavailableError(f"OpenAI error {exc.status_code}", provider=NAME)
        return ProviderBadRequestError(f"OpenAI rejected the request: {exc.message}", provider=NAME)
    return ProviderError(f"OpenAI error: {exc}", provider=NAME)
