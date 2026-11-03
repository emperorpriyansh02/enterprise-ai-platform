"""Mock provider for tests and keyless local demos.

Models:
- echo         replies "Echo: <last user message>"
- echo-system  replies with the system prompt it received (to check what prompt reached the model)

Tools (Layer 4), on either model: a last message like `/tool get_time {"timezone": "UTC"}` makes it call
that tool (short names match `server__tool`). After the tool runs, it replies with the results.

Disable it in production with ENABLE_MOCK_PROVIDER=false.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator

from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk, ToolCall
from app.schemas.chat import Usage

NAME = "mock"
_TOOL_COMMAND = re.compile(r"^/tool\s+(\S+)\s*(.*)$", re.S)


class MockProvider(LLMProvider):
    name = NAME

    def __init__(self, *, stream_delay_s: float = 0.0) -> None:
        super().__init__(["echo", "echo-system"])
        self._delay = stream_delay_s

    @staticmethod
    def _reply(request: LLMRequest) -> str:
        if request.turns:
            lines = [f"- {r.name}: {r.content}" for r in request.turns[-1].results]
            return "Tool results:\n" + "\n".join(lines)
        if request.model == "echo-system":
            return request.system or "(no system prompt)"
        last_user = next(m.content for m in reversed(request.messages) if m.role == "user")
        return f"Echo: {last_user}"

    @staticmethod
    def _tool_call(request: LLMRequest) -> ToolCall | None:
        """The tool call asked for with `/tool name {json}`, if tools are offered and none ran yet."""
        if not request.tools or request.turns:
            return None
        last_user = next(m.content for m in reversed(request.messages) if m.role == "user")
        match = _TOOL_COMMAND.match(last_user.strip())
        if not match:
            return None
        wanted, raw_args = match.groups()
        name = next(
            (t.name for t in request.tools if t.name == wanted or t.name.endswith(f"__{wanted}")), None
        )
        if name is None:
            return None
        try:
            arguments = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            arguments = {}
        return ToolCall(
            id="mock_call_1", name=name, arguments=arguments if isinstance(arguments, dict) else {}
        )

    @staticmethod
    def _usage(request: LLMRequest, reply: str) -> Usage:
        prompt = " ".join([request.system or "", *(m.content for m in request.messages)])
        return Usage(input_tokens=len(prompt.split()), output_tokens=len(reply.split()))

    async def complete(self, request: LLMRequest) -> LLMResult:
        call = self._tool_call(request)
        if call is not None:
            return LLMResult(
                content="",
                finish_reason="tool_calls",
                usage=self._usage(request, ""),
                model=request.model,
                tool_calls=[call],
                provider_state={"calls": [call]},
            )
        reply = self._reply(request)
        return LLMResult(
            content=reply, finish_reason="stop", usage=self._usage(request, reply), model=request.model
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        call = self._tool_call(request)
        if call is not None:
            yield StreamChunk(
                done=True,
                finish_reason="tool_calls",
                usage=self._usage(request, ""),
                model=request.model,
                tool_calls=[call],
                provider_state={"calls": [call]},
            )
            return
        reply = self._reply(request)
        words = reply.split(" ")
        for i, word in enumerate(words):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield StreamChunk(delta=word if i == 0 else f" {word}")
        yield StreamChunk(
            done=True, finish_reason="stop", usage=self._usage(request, reply), model=request.model
        )
