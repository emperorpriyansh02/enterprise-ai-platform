"""Chat orchestration: the one place every chat request passes through.

Steps today:  resolve model -> build system prompt (Layer 2) -> add knowledge-base sources (Layer 3)
              -> load MCP tools (Layer 4) -> LLM call, with a model <-> tool loop -> log
Later layers plug in here, in this order:
    auth/RBAC -> input guardrails -> cache -> router -> MCP tools
    -> LLM call -> output guardrails -> observability / evals logging
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.core.errors import (
    AppError,
    PromptStoreUnavailableError,
    ProviderError,
    RagUnavailableError,
    ToolsUnavailableError,
)
from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk, ToolCall, ToolResult, ToolTurn
from app.llm.registry import ProviderRegistry
from app.mcp.schemas import ToolCallTrace
from app.mcp.service import MCPService
from app.mcp.toolbox import Toolbox
from app.prompts.schemas import PromptUsed
from app.prompts.service import PromptService
from app.rag.runtime import RagRuntime
from app.rag.schemas import Citation
from app.rag.service import RagService
from app.schemas.chat import ChatRequest, ChatResponse, StreamEvent, Usage

logger = logging.getLogger(__name__)

ROUND_SEPARATOR = "\n\n"  # between the text the model writes before and after using tools


@dataclass(frozen=True)
class _Prepared:
    """Everything worked out before the first LLM call."""

    provider: LLMProvider
    model: str
    llm_request: LLMRequest
    prompt: PromptUsed | None
    citations: list[Citation] | None
    toolbox: Toolbox | None


class ChatService:
    def __init__(
        self,
        registry: ProviderRegistry,
        prompts: PromptService | None = None,
        rag: RagService | None = None,
        rag_runtime: RagRuntime | None = None,
        tools: MCPService | None = None,
        *,
        max_tool_rounds: int = 5,
        release_db: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._registry = registry
        self._prompts = prompts  # None when no database is configured
        self._rag = rag  # None when there's no database or embedding provider
        self._rag_runtime = rag_runtime
        self._tools = tools  # None when no database is configured
        self._max_tool_rounds = max_tool_rounds
        self._release_db = release_db  # frees the request's database connection during LLM calls

    async def complete(self, request: ChatRequest) -> ChatResponse:
        prep = await self._prepare(request)
        request_id = _new_id()
        started = time.perf_counter()
        llm_request, usage, texts = prep.llm_request, Usage(), []
        traces: list[ToolCallTrace] = []
        rounds = 0
        while True:
            result = await prep.provider.complete(llm_request)
            usage = _add_usage(usage, result.usage)
            if result.content:
                texts.append(result.content)
            if not self._wants_tools(result.tool_calls, prep.toolbox, rounds):
                break
            assert prep.toolbox is not None
            outcomes = await prep.toolbox.run(result.tool_calls)
            traces.extend(trace for _, trace in outcomes)
            llm_request = llm_request.with_turn(_turn(result.content, result, [r for r, _ in outcomes]))
            rounds += 1
        latency_ms = _ms_since(started)
        self._log(
            "chat completed",
            request_id,
            prep,
            result.model,
            latency_ms,
            usage,
            result.finish_reason,
            traces,
            rounds,
        )
        return ChatResponse(
            id=request_id,
            provider=prep.provider.name,
            model=result.model,
            content=ROUND_SEPARATOR.join(texts),
            finish_reason=result.finish_reason,
            usage=usage,
            latency_ms=latency_ms,
            prompt=prep.prompt,
            citations=prep.citations,
            tool_calls=traces if prep.toolbox is not None else None,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Yield `start`, then `delta` / `tool_call` / `tool_result` events, then `done` (or `error`).

        Errors before the first token (bad model or prompt, auth, rate limit) are raised instead,
        so the route can still return a proper HTTP status: the provider is called before `start`.
        """
        prep = await self._prepare(request)
        request_id = _new_id()
        started = time.perf_counter()
        llm_request = prep.llm_request
        chunks = prep.provider.stream(llm_request)

        try:
            first = await anext(chunks)  # raises ProviderError before any bytes are sent
        except StopAsyncIteration:
            raise ProviderError("Provider returned an empty stream", provider=prep.provider.name) from None
        yield StreamEvent(
            type="start",
            id=request_id,
            provider=prep.provider.name,
            model=first.model or prep.model,
            prompt=prep.prompt,
            citations=prep.citations,
        )

        usage = Usage()
        traces: list[ToolCallTrace] = []
        rounds = 0
        round_text = ""  # what the model wrote in the current round
        wrote_text = False  # any text sent yet (to put a separator before the next round's text)
        try:
            chunk = first
            while True:
                if chunk.done:
                    usage = _add_usage(usage, chunk.usage or Usage())
                    if self._wants_tools(chunk.tool_calls, prep.toolbox, rounds):
                        assert prep.toolbox is not None
                        for call in chunk.tool_calls:
                            yield StreamEvent(
                                type="tool_call", id=request_id, tool=prep.toolbox.trace_for(call)
                            )
                        outcomes = await prep.toolbox.run(chunk.tool_calls)
                        for _, trace in outcomes:
                            traces.append(trace)
                            yield StreamEvent(type="tool_result", id=request_id, tool=trace)
                        llm_request = llm_request.with_turn(
                            _turn(round_text, chunk, [r for r, _ in outcomes])
                        )
                        rounds += 1
                        round_text = ""
                        chunks = prep.provider.stream(llm_request)
                        chunk = await anext(chunks)
                        continue
                    latency_ms = _ms_since(started)
                    model = chunk.model or prep.model
                    finish = chunk.finish_reason or "other"
                    self._log(
                        "chat stream completed",
                        request_id,
                        prep,
                        model,
                        latency_ms,
                        usage,
                        finish,
                        traces,
                        rounds,
                    )
                    yield StreamEvent(
                        type="done",
                        id=request_id,
                        provider=prep.provider.name,
                        model=model,
                        finish_reason=finish,
                        usage=usage,
                        latency_ms=latency_ms,
                        prompt=prep.prompt,
                    )
                    return
                if chunk.delta:
                    delta = chunk.delta
                    if not round_text and wrote_text:
                        delta = ROUND_SEPARATOR + delta
                    round_text += chunk.delta
                    wrote_text = True
                    yield StreamEvent(type="delta", id=request_id, delta=delta)
                chunk = await anext(chunks)
        except StopAsyncIteration:
            error: AppError = ProviderError("Stream ended without a final event", provider=prep.provider.name)
        except AppError as exc:
            error = exc
        logger.warning(
            "chat stream failed",
            extra={"request_id": request_id, "provider": prep.provider.name, "error_code": error.code},
        )
        yield StreamEvent(type="error", id=request_id, error=error.to_dict())

    async def _prepare(self, request: ChatRequest) -> _Prepared:
        """Resolve the model, build the system prompt, add sources, load tools; then free the database."""
        provider, model = self._registry.resolve(request.model)
        system, prompt_used = await self._build_system(request)
        system, citations = await self._retrieve(request, system)
        toolbox = await self._toolbox(request)
        if self._release_db is not None:
            await self._release_db()  # everything is loaded; LLM and tool calls don't need it
        return _Prepared(
            provider=provider,
            model=model,
            llm_request=_to_llm_request(request, model, system, toolbox),
            prompt=prompt_used,
            citations=citations,
            toolbox=toolbox,
        )

    async def _build_system(self, request: ChatRequest) -> tuple[str | None, PromptUsed | None]:
        """Final system prompt: the managed prompt (if any), then the request's own `system` text."""
        if self._prompts is None:
            if request.prompt is not None:
                raise PromptStoreUnavailableError(
                    "This request names a prompt, but no prompt store is configured (DATABASE_URL)."
                )
            return request.system, None
        rendered = await self._prompts.resolve_for_chat(request.prompt, has_system=bool(request.system))
        if rendered is None:
            return request.system, None
        system = f"{rendered.text}\n\n{request.system}" if request.system else rendered.text
        return system, PromptUsed(name=rendered.name, version=rendered.version)

    async def _retrieve(
        self, request: ChatRequest, system: str | None
    ) -> tuple[str | None, list[Citation] | None]:
        """Add the best knowledge-base passages to the system prompt, when the request asks (`rag`)."""
        if request.rag is None:
            return system, None
        if self._rag_runtime is not None:
            await self._rag_runtime.check()  # 503 with the exact reason
        if self._rag is None:
            raise RagUnavailableError("Knowledge bases aren't available: no database or embedding provider.")
        query = next(m.content for m in reversed(request.messages) if m.role == "user")
        retrieved = await self._rag.retrieve_for_chat(request.rag, query)
        if not retrieved.hits:
            return system, []
        return (f"{system}\n\n{retrieved.context}" if system else retrieved.context), retrieved.citations

    async def _toolbox(self, request: ChatRequest) -> Toolbox | None:
        """The enabled tools of the MCP servers the request names (`tools`), or None."""
        if request.tools is None:
            return None
        if self._tools is None:
            raise ToolsUnavailableError("MCP tools need a database (DATABASE_URL) for the server registry.")
        return await self._tools.toolbox_for_chat(request.tools)

    def _wants_tools(self, calls: list[ToolCall], toolbox: Toolbox | None, rounds: int) -> bool:
        """Run another tool round? Only if the model asked, tools were offered, and the limit isn't hit."""
        if not calls or toolbox is None:
            return False
        if rounds >= self._max_tool_rounds:
            logger.warning("tool round limit reached", extra={"tool_rounds": rounds})
            return False
        return True

    def _log(
        self,
        message: str,
        request_id: str,
        prep: _Prepared,
        model: str,
        latency_ms: int,
        usage: Usage,
        finish_reason: str | None,
        traces: list[ToolCallTrace],
        rounds: int,
    ) -> None:
        logger.info(
            message,
            extra={
                "request_id": request_id,
                "provider": prep.provider.name,
                "model": model,
                "prompt": _prompt_label(prep.prompt),
                "sources": len(prep.citations) if prep.citations is not None else None,
                "tool_calls": len(traces) if prep.toolbox is not None else None,
                "tool_rounds": rounds if prep.toolbox is not None else None,
                "latency_ms": latency_ms,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "finish_reason": finish_reason,
            },
        )


def _to_llm_request(
    request: ChatRequest, model: str, system: str | None, toolbox: Toolbox | None
) -> LLMRequest:
    return LLMRequest(
        model=model,
        messages=request.messages,
        system=system,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        provider_options=request.provider_options,
        tools=toolbox.specs if toolbox is not None else [],
    )


def _turn(text: str, result: LLMResult | StreamChunk, results: list[ToolResult]) -> ToolTurn:
    """One finished tool round, to send back to the model."""
    return ToolTurn(text=text, calls=result.tool_calls, provider_state=result.provider_state, results=results)


def _add_usage(total: Usage, more: Usage) -> Usage:
    return Usage(
        input_tokens=total.input_tokens + more.input_tokens,
        output_tokens=total.output_tokens + more.output_tokens,
    )


def _prompt_label(prompt: PromptUsed | None) -> str | None:
    """`support-agent@v3` for logs, or None."""
    return f"{prompt.name}@v{prompt.version}" if prompt else None


def _new_id() -> str:
    return f"chat_{uuid.uuid4().hex}"


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
