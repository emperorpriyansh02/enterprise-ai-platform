"""Google Gemini adapter (official `google-genai` SDK).

Tools (Layer 4): offered as function declarations (JSON schema), with the SDK's automatic calling off.
The model's turn is replayed as Gemini sent it, so `thought_signature`s survive, followed by one user
turn with a `function_response` per call.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

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

NAME = "gemini"

_FINISH_REASONS: dict[str, FinishReason] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
}

_TIMEOUT_ERRORS = (httpx.TimeoutException, httpx2.TimeoutException)
_NETWORK_ERRORS = (httpx.TransportError, httpx2.TransportError)


class GeminiProvider(LLMProvider):
    name = NAME

    def __init__(
        self,
        *,
        api_key: str,
        models: list[str],
        timeout_s: float,
        max_retries: int,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(models)
        http_options = types.HttpOptions(
            timeout=int(timeout_s * 1000),  # milliseconds
            retry_options=types.HttpRetryOptions(attempts=max_retries + 1),
            httpx_async_client=http_client,
        )
        self._client = genai.Client(api_key=api_key, http_options=http_options)

    def _params(self, request: LLMRequest) -> dict[str, Any]:
        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in request.messages
        ]
        for turn in request.turns:
            contents.append(turn.provider_state)
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=None if r.call_id.startswith(_MADE_UP_ID) else r.call_id,
                                name=r.name,
                                response={"error": r.content} if r.is_error else {"result": r.content},
                            )
                        )
                        for r in turn.results
                    ],
                )
            )
        config: dict[str, Any] = {}
        if request.tools:
            config["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name, description=t.description, parameters_json_schema=t.input_schema
                        )
                        for t in request.tools
                    ]
                )
            ]
            config["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if request.system:
            config["system_instruction"] = request.system
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens
        config.update(request.provider_options)
        return {
            "model": request.model,
            "contents": contents,
            "config": types.GenerateContentConfig(**config),
        }

    async def complete(self, request: LLMRequest) -> LLMResult:
        try:
            response = await self._client.aio.models.generate_content(**self._params(request))
        except Exception as exc:
            raise _map_error(exc) from exc
        content = response.candidates[0].content if response.candidates else None
        calls = _tool_calls(content.parts if content else [])
        return LLMResult(
            content=_text(response),
            finish_reason="tool_calls" if calls else (_finish_reason(response) or "other"),
            usage=_usage(response),
            model=response.model_version or request.model,
            tool_calls=calls,
            provider_state=content if calls else None,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        finish_reason: FinishReason = "other"
        usage = Usage()
        model = request.model
        parts: list[types.Part] = []  # every part of the model's turn, to replay it if it calls tools
        try:
            stream = await self._client.aio.models.generate_content_stream(**self._params(request))
            async for chunk in stream:
                if chunk.candidates and chunk.candidates[0].content:
                    parts.extend(chunk.candidates[0].content.parts or [])
                text = _text(chunk)
                if text:
                    yield StreamChunk(delta=text)
                finish_reason = _finish_reason(chunk) or finish_reason
                if chunk.usage_metadata:
                    usage = _usage(chunk)
                model = chunk.model_version or model
        except Exception as exc:
            raise _map_error(exc) from exc
        calls = _tool_calls(parts)
        yield StreamChunk(
            done=True,
            finish_reason="tool_calls" if calls else finish_reason,
            usage=usage,
            model=model,
            tool_calls=calls,
            provider_state=types.Content(role="model", parts=parts) if calls else None,
        )

    async def aclose(self) -> None:
        await self._client.aio.aclose()


_MADE_UP_ID = "gemini_"  # prefix for ids we invent when Gemini gives none (not sent back to Gemini)


def _tool_calls(parts: list[types.Part]) -> list[ToolCall]:
    """The function calls in a model turn."""
    return [
        ToolCall(
            id=p.function_call.id or f"{_MADE_UP_ID}{uuid.uuid4().hex[:12]}",
            name=p.function_call.name or "",
            arguments=dict(p.function_call.args or {}),
        )
        for p in parts
        if p.function_call
    ]


def _text(response: types.GenerateContentResponse) -> str:
    """Concatenate visible text parts (skips thought summaries)."""
    if not response.candidates or not response.candidates[0].content:
        return ""
    parts = response.candidates[0].content.parts or []
    return "".join(p.text for p in parts if p.text and not p.thought)


def _finish_reason(response: types.GenerateContentResponse) -> FinishReason | None:
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        return "content_filter"
    if response.candidates and response.candidates[0].finish_reason:
        return _FINISH_REASONS.get(response.candidates[0].finish_reason.name, "other")
    return None


def _usage(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    if meta is None:
        return Usage()
    output = (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
    return Usage(input_tokens=meta.prompt_token_count or 0, output_tokens=output)


def _map_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, _TIMEOUT_ERRORS):
        return ProviderTimeoutError("Gemini request timed out", provider=NAME)
    if isinstance(exc, _NETWORK_ERRORS):
        return ProviderUnavailableError("Could not reach Gemini", provider=NAME)
    if isinstance(exc, genai_errors.APIError):
        code = exc.code or 0
        if code in (401, 403):
            return ProviderAuthError("Gemini rejected the API key", provider=NAME)
        if code == 429:
            return ProviderRateLimitError("Gemini rate limit hit", provider=NAME)
        if code >= 500:
            return ProviderUnavailableError(f"Gemini error {code}", provider=NAME)
        return ProviderBadRequestError(f"Gemini rejected the request: {exc.message}", provider=NAME)
    return ProviderError(f"Gemini error: {exc}", provider=NAME)
