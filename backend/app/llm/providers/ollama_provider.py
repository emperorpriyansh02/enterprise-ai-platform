"""Ollama adapter for self-hosted open models, via Ollama's REST API (/api/chat).

Ollama has no official async Python SDK we need here; its chat API is a small JSON/NDJSON
interface, so a plain HTTP client keeps the dependency list short.

Tools (Layer 4): OpenAI-style function tools; calls arrive in `message.tool_calls` (arguments as an
object). The next round replays the assistant message, then one `tool` message per result.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import (
    ProviderBadRequestError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk, ToolCall
from app.schemas.chat import FinishReason, Usage

NAME = "ollama"

_FINISH_REASONS: dict[str, FinishReason] = {"stop": "stop", "length": "length"}


class OllamaProvider(LLMProvider):
    name = NAME

    def __init__(
        self,
        *,
        base_url: str,
        models: list[str],
        timeout_s: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(models)
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    def _payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)
        for turn in request.turns:
            messages.append(turn.provider_state)
            messages.extend({"role": "tool", "tool_name": r.name, "content": r.content} for r in turn.results)
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        payload: dict[str, Any] = {"model": request.model, "messages": messages, "stream": stream}
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in request.tools
            ]
        if options:
            payload["options"] = options
        payload.update(request.provider_options)
        return payload

    async def complete(self, request: LLMRequest) -> LLMResult:
        try:
            response = await self._client.post("/api/chat", json=self._payload(request, stream=False))
            _raise_for_status(response)
        except httpx.HTTPError as exc:
            raise _map_error(exc) from exc
        data = response.json()
        message = data.get("message", {})
        calls = _tool_calls(message.get("tool_calls") or [])
        return LLMResult(
            content=message.get("content", ""),
            finish_reason="tool_calls"
            if calls
            else _FINISH_REASONS.get(data.get("done_reason", ""), "other"),
            usage=_usage(data),
            model=data.get("model", request.model),
            tool_calls=calls,
            provider_state=_assistant_message(message.get("content", ""), calls) if calls else None,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        payload = self._payload(request, stream=True)
        text_parts: list[str] = []
        raw_calls: list[dict[str, Any]] = []
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                    _raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise ProviderError(f"Ollama error: {data['error']}", provider=NAME)
                    text = data.get("message", {}).get("content", "")
                    raw_calls.extend(data.get("message", {}).get("tool_calls") or [])
                    if text:
                        text_parts.append(text)
                        yield StreamChunk(delta=text)
                    if data.get("done"):
                        calls = _tool_calls(raw_calls)
                        done_reason = _FINISH_REASONS.get(data.get("done_reason", ""), "other")
                        yield StreamChunk(
                            done=True,
                            finish_reason="tool_calls" if calls else done_reason,
                            usage=_usage(data),
                            model=data.get("model", request.model),
                            tool_calls=calls,
                            provider_state=_assistant_message("".join(text_parts), calls) if calls else None,
                        )
                        return
        except httpx.HTTPError as exc:
            raise _map_error(exc) from exc
        raise ProviderUnavailableError("Ollama stream ended unexpectedly", provider=NAME)

    async def aclose(self) -> None:
        await self._client.aclose()


def _tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    """Ollama's tool calls → ToolCalls. Ollama gives no ids, so we make them up."""
    calls = []
    for item in raw:
        function = item.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):  # some models send JSON text instead of an object
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append(
            ToolCall(
                id=item.get("id") or f"ollama_{uuid.uuid4().hex[:12]}",
                name=function.get("name", ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return calls


def _assistant_message(content: str, calls: list[ToolCall]) -> dict[str, Any]:
    """The assistant turn to send back before the tool results."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in calls],
    }


def _usage(data: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=data.get("prompt_eval_count", 0) or 0,
        output_tokens=data.get("eval_count", 0) or 0,
    )


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.json().get("error", response.text)
    except ValueError:
        detail = response.text
    if response.status_code == 404:
        raise ProviderBadRequestError(
            f"Ollama model not found ({detail}). Pull it first with `ollama pull <model>`.",
            provider=NAME,
        )
    if response.status_code >= 500:
        raise ProviderUnavailableError(f"Ollama error {response.status_code}: {detail}", provider=NAME)
    raise ProviderBadRequestError(f"Ollama rejected the request: {detail}", provider=NAME)


def _map_error(exc: httpx.HTTPError) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError("Ollama request timed out", provider=NAME)
    return ProviderUnavailableError(f"Could not reach Ollama: {exc}", provider=NAME)
