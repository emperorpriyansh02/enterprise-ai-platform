"""Chat orchestration: the one place every chat request passes through.

Steps today:  (route checked the caller's token and `chat:use`, Layer 5) -> resolve model + check
              the caller may use it -> input guardrails (Layer 6) -> build system prompt (Layer 2)
              -> add knowledge-base sources the caller may see (Layer 3), screened by context guardrails
              -> load MCP tools the caller may use (Layer 4) -> LLM call, with a model <-> tool loop
              (tool results screened too) -> output guardrails -> log
Later layers plug in here, in this order:
    cache -> router -> ... -> observability / evals logging
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace

from app.auth.permissions import TOOLS_USE
from app.auth.principal import Principal
from app.core.errors import (
    AppError,
    ForbiddenError,
    PromptStoreUnavailableError,
    ProviderError,
    RagUnavailableError,
    ToolsUnavailableError,
)
from app.guardrails.engine import GuardrailEngine, OutputStreamGuard
from app.guardrails.schemas import GuardrailTrace
from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk, ToolCall, ToolResult, ToolTurn
from app.llm.registry import ProviderRegistry
from app.mcp.schemas import ToolCallTrace
from app.mcp.service import MCPService
from app.mcp.toolbox import TRACE_PREVIEW_CHARS, Toolbox
from app.prompts.schemas import PromptUsed
from app.prompts.service import PromptService
from app.rag.retrieval import build_context
from app.rag.runtime import RagRuntime
from app.rag.schemas import Citation
from app.rag.service import SNIPPET_CHARS, RagService
from app.schemas.chat import ChatRequest, ChatResponse, Message, StreamEvent, Usage

logger = logging.getLogger(__name__)

ROUND_SEPARATOR = "\n\n"  # between the text the model writes before and after using tools


@dataclass(frozen=True)
class _Prepared:
    """Everything worked out before the first LLM call."""

    provider: LLMProvider
    model: str
    llm_request: LLMRequest | None  # None when the input was blocked
    prompt: PromptUsed | None
    citations: list[Citation] | None
    toolbox: Toolbox | None
    blocked: GuardrailTrace | None = None  # an input guardrail stopped the request


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
        principal: Principal | None = None,
        model_roles: dict[str, list[str]] | None = None,
        guardrails: GuardrailEngine | None = None,
    ) -> None:
        self._registry = registry
        self._prompts = prompts  # None when no database is configured
        self._rag = rag  # None when there's no database or embedding provider
        self._rag_runtime = rag_runtime
        self._tools = tools  # None when no database is configured
        self._max_tool_rounds = max_tool_rounds
        self._release_db = release_db  # frees the request's database connection during LLM calls
        self._principal = principal  # the caller (Layer 5); None = no per-model or per-tool checks
        self._model_roles = model_roles or {}  # MODEL_ROLES: {"provider/model": [roles]}
        self._guard = guardrails  # Layer 6; None = guardrails off
        self._guard_traces: list[GuardrailTrace] = []  # every firing for this request, in order

    async def complete(self, request: ChatRequest) -> ChatResponse:
        request_id = self._start()
        started = time.perf_counter()
        prep = await self._prepare(request)
        if prep.blocked is not None:
            return self._blocked_response(request_id, prep, started)
        assert prep.llm_request is not None
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
            outcomes = await self._screen_tools(await prep.toolbox.run(result.tool_calls))
            traces.extend(trace for _, trace in outcomes)
            llm_request = llm_request.with_turn(_turn(result.content, result, [r for r, _ in outcomes]))
            rounds += 1
        content, finish = ROUND_SEPARATOR.join(texts), result.finish_reason
        if self._guard is not None:
            checked = await self._guard.check_output(content)
            self._guard_traces.extend(checked.traces)
            if checked.blocked is not None:
                content, finish = checked.blocked.message or "", "content_filter"
            else:
                content = checked.text
        latency_ms = _ms_since(started)
        self._log("chat completed", request_id, prep, result.model, latency_ms, usage, finish, traces, rounds)
        return ChatResponse(
            id=request_id,
            provider=prep.provider.name,
            model=result.model,
            content=content,
            finish_reason=finish,
            usage=usage,
            latency_ms=latency_ms,
            prompt=prep.prompt,
            citations=prep.citations,
            tool_calls=traces if prep.toolbox is not None else None,
            guardrails=self._guard_traces if self._guard is not None else None,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Yield `start`, then `delta` / `tool_call` / `tool_result` / `guardrail` events, then `done`
        (or `error`).

        Errors before the first token (bad model or prompt, auth, rate limit) are raised instead,
        so the route can still return a proper HTTP status: the provider is called before `start`.
        """
        request_id = self._start()
        started = time.perf_counter()
        prep = await self._prepare(request)
        if prep.blocked is not None:
            for event in self._blocked_events(request_id, prep, started):
                yield event
            return
        assert prep.llm_request is not None
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
        for trace in self._guard_traces:  # input and passage checks so far
            yield StreamEvent(type="guardrail", id=request_id, guardrail=trace)
        reported = len(self._guard_traces)
        out: OutputStreamGuard | None = self._guard.stream_guard() if self._guard is not None else None

        usage = Usage()
        traces: list[ToolCallTrace] = []
        rounds = 0
        round_text = ""  # what the model wrote in the current round (unmasked: replayed to the model)
        wrote_text = False  # any text sent yet (to put a separator before the next round's text)
        try:
            chunk = first
            while True:
                if chunk.done:
                    usage = _add_usage(usage, chunk.usage or Usage())
                    if self._wants_tools(chunk.tool_calls, prep.toolbox, rounds):
                        assert prep.toolbox is not None
                        if out is not None and (held := out.flush()):
                            yield StreamEvent(type="delta", id=request_id, delta=held)
                        for call in chunk.tool_calls:
                            yield StreamEvent(
                                type="tool_call", id=request_id, tool=prep.toolbox.trace_for(call)
                            )
                        outcomes = await self._screen_tools(await prep.toolbox.run(chunk.tool_calls))
                        for _, trace in outcomes:
                            traces.append(trace)
                            yield StreamEvent(type="tool_result", id=request_id, tool=trace)
                        for trace in self._guard_traces[reported:]:
                            yield StreamEvent(type="guardrail", id=request_id, guardrail=trace)
                        reported = len(self._guard_traces)
                        llm_request = llm_request.with_turn(
                            _turn(round_text, chunk, [r for r, _ in outcomes])
                        )
                        rounds += 1
                        round_text = ""
                        chunks = prep.provider.stream(llm_request)
                        chunk = await anext(chunks)
                        continue
                    finish = chunk.finish_reason or "other"
                    if out is not None:
                        tail, out_traces = await out.finish()
                        if tail:
                            yield StreamEvent(type="delta", id=request_id, delta=tail)
                        self._guard_traces.extend(out_traces)
                        for trace in out_traces:
                            yield StreamEvent(type="guardrail", id=request_id, guardrail=trace)
                        if out.blocked is not None:
                            finish = "content_filter"  # the `guardrail` event tells the client to withdraw
                    latency_ms = _ms_since(started)
                    model = chunk.model or prep.model
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
                    if out is not None:
                        delta = out.feed(delta)
                        if out.blocked is not None:  # e.g. a blocked topic appeared: stop the model
                            await chunks.aclose()
                            for event in self._stopped_events(request_id, prep, out.blocked, usage, started):
                                yield event
                            return
                    if delta:
                        yield StreamEvent(type="delta", id=request_id, delta=delta)
                chunk = await anext(chunks)
        except StopAsyncIteration:
            error: AppError = ProviderError("Stream ended without a final event", provider=prep.provider.name)
        except AppError as exc:
            error = exc
        if out is not None and (held := out.flush()):
            yield StreamEvent(type="delta", id=request_id, delta=held)  # what arrived before the failure
        logger.warning(
            "chat stream failed",
            extra={"request_id": request_id, "provider": prep.provider.name, "error_code": error.code},
        )
        yield StreamEvent(type="error", id=request_id, error=error.to_dict())

    def _start(self) -> str:
        """A new request id, shared with the guardrails (their events carry it)."""
        request_id = _new_id()
        if self._guard is not None:
            self._guard.request_id = request_id
        return request_id

    async def _prepare(self, request: ChatRequest) -> _Prepared:
        """Resolve the model, check the input, build the system prompt, add sources, load tools; then free
        the database. A blocked input stops here, before any model is called."""
        provider, model = self._registry.resolve(request.model)
        self._check_model(f"{provider.name}/{model}")
        messages = request.messages
        if self._guard is not None:
            checked = await self._guard.check_input(messages)
            self._guard_traces.extend(checked.traces)
            if checked.blocked is not None:
                if self._release_db is not None:
                    await self._release_db()
                return _Prepared(provider, model, None, None, None, None, blocked=checked.blocked)
            messages = checked.messages
        system, prompt_used = await self._build_system(request)
        system, citations = await self._retrieve(request, system, messages)
        toolbox = await self._toolbox(request)
        if self._release_db is not None:
            await self._release_db()  # everything is loaded; LLM and tool calls don't need it
        return _Prepared(
            provider=provider,
            model=model,
            llm_request=_to_llm_request(request, messages, model, system, toolbox),
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
        self, request: ChatRequest, system: str | None, messages: list[Message]
    ) -> tuple[str | None, list[Citation] | None]:
        """Add the best knowledge-base passages to the system prompt, when the request asks (`rag`).
        Context guardrails screen each passage first (masked, or removed)."""
        if request.rag is None:
            return system, None
        if self._rag_runtime is not None:
            await self._rag_runtime.check()  # 503 with the exact reason
        if self._rag is None:
            raise RagUnavailableError("Knowledge bases aren't available: no database or embedding provider.")
        query = next(m.content for m in reversed(messages) if m.role == "user")
        retrieved = await self._rag.retrieve_for_chat(request.rag, query)
        if not retrieved.hits:
            return system, []
        context, citations = retrieved.context, retrieved.citations
        if self._guard is not None:
            results = await self._guard.check_context(
                [(f"passage {i}", hit.content) for i, hit in enumerate(retrieved.hits, start=1)]
            )
            for result in results:
                self._guard_traces.extend(result.traces)
            if any(r.text != hit.content for r, hit in zip(results, retrieved.hits, strict=True)):
                hits = [
                    h.model_copy(update={"content": r.text})
                    for h, r in zip(retrieved.hits, results, strict=True)
                ]
                context = build_context(hits)
                citations = [
                    c.model_copy(update={"snippet": h.content[:SNIPPET_CHARS]})
                    for c, h in zip(citations, hits, strict=True)
                ]
        return (f"{system}\n\n{context}" if system else context), citations

    async def _toolbox(self, request: ChatRequest) -> Toolbox | None:
        """The enabled tools of the MCP servers the request names (`tools`), or None."""
        if request.tools is None:
            return None
        if self._principal is not None and not self._principal.can(TOOLS_USE):
            raise ForbiddenError("Your roles don't allow using tools (tools:use).")
        if self._tools is None:
            raise ToolsUnavailableError("MCP tools need a database (DATABASE_URL) for the server registry.")
        return await self._tools.toolbox_for_chat(request.tools)

    async def _screen_tools(
        self, outcomes: list[tuple[ToolResult, ToolCallTrace]]
    ) -> list[tuple[ToolResult, ToolCallTrace]]:
        """Context guardrails on tool results, before the model reads them (and in the shown trace)."""
        if self._guard is None or not outcomes:
            return outcomes
        results = await self._guard.check_context([(f"tool {r.name}", r.content) for r, _ in outcomes])
        screened = []
        for (tool_result, trace), checked in zip(outcomes, results, strict=True):
            self._guard_traces.extend(checked.traces)
            if checked.text != tool_result.content:
                tool_result = replace(tool_result, content=checked.text)
                trace = trace.model_copy(update={"result": checked.text[:TRACE_PREVIEW_CHARS]})
            screened.append((tool_result, trace))
        return screened

    def _blocked_response(self, request_id: str, prep: _Prepared, started: float) -> ChatResponse:
        """The reply when an input guardrail blocked the request: the policy's message, no model call."""
        assert prep.blocked is not None
        latency_ms = _ms_since(started)
        self._log("chat blocked", request_id, prep, prep.model, latency_ms, Usage(), "content_filter", [], 0)
        return ChatResponse(
            id=request_id,
            provider=prep.provider.name,
            model=prep.model,
            content=prep.blocked.message or "",
            finish_reason="content_filter",
            usage=Usage(),
            latency_ms=latency_ms,
            guardrails=self._guard_traces,
        )

    def _blocked_events(self, request_id: str, prep: _Prepared, started: float) -> list[StreamEvent]:
        """The same, as a stream: start, the guardrail events, the message as one delta, done."""
        response = self._blocked_response(request_id, prep, started)
        return [
            StreamEvent(type="start", id=request_id, provider=response.provider, model=response.model),
            *(StreamEvent(type="guardrail", id=request_id, guardrail=t) for t in self._guard_traces),
            StreamEvent(type="delta", id=request_id, delta=response.content),
            StreamEvent(
                type="done",
                id=request_id,
                provider=response.provider,
                model=response.model,
                finish_reason="content_filter",
                usage=Usage(),
                latency_ms=response.latency_ms,
            ),
        ]

    def _stopped_events(
        self, request_id: str, prep: _Prepared, blocked: GuardrailTrace, usage: Usage, started: float
    ) -> list[StreamEvent]:
        """An output guardrail blocked mid-stream: say so (the client withdraws the text), then done."""
        self._guard_traces.append(blocked)
        latency_ms = _ms_since(started)
        self._log(
            "chat stream blocked", request_id, prep, prep.model, latency_ms, usage, "content_filter", [], 0
        )
        return [
            StreamEvent(type="guardrail", id=request_id, guardrail=blocked),
            StreamEvent(
                type="done",
                id=request_id,
                provider=prep.provider.name,
                model=prep.model,
                finish_reason="content_filter",
                usage=usage,
                latency_ms=latency_ms,
                prompt=prep.prompt,
            ),
        ]

    def _check_model(self, model_id: str) -> None:
        """403 unless the caller's roles may use this model (MODEL_ROLES; unlisted models are open)."""
        if self._principal is not None and not self._principal.may_use(self._model_roles.get(model_id)):
            raise ForbiddenError(f'Your roles can\'t use the model "{model_id}".')

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
                "guardrails": len(self._guard_traces) if self._guard is not None else None,
                "latency_ms": latency_ms,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "finish_reason": finish_reason,
            },
        )


def _to_llm_request(
    request: ChatRequest, messages: list[Message], model: str, system: str | None, toolbox: Toolbox | None
) -> LLMRequest:
    return LLMRequest(
        model=model,
        messages=messages,
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
