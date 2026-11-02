"""Mock provider for tests and keyless local demos.

Models:
- echo         replies "Echo: <last user message>"
- echo-system  replies with the system prompt it received (to check what prompt reached the model)

Disable it in production with ENABLE_MOCK_PROVIDER=false.
"""

import asyncio
from collections.abc import AsyncIterator

from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk
from app.schemas.chat import Usage

NAME = "mock"


class MockProvider(LLMProvider):
    name = NAME

    def __init__(self, *, stream_delay_s: float = 0.0) -> None:
        super().__init__(["echo", "echo-system"])
        self._delay = stream_delay_s

    @staticmethod
    def _reply(request: LLMRequest) -> str:
        if request.model == "echo-system":
            return request.system or "(no system prompt)"
        last_user = next(m.content for m in reversed(request.messages) if m.role == "user")
        return f"Echo: {last_user}"

    @staticmethod
    def _usage(request: LLMRequest, reply: str) -> Usage:
        prompt = " ".join([request.system or "", *(m.content for m in request.messages)])
        return Usage(input_tokens=len(prompt.split()), output_tokens=len(reply.split()))

    async def complete(self, request: LLMRequest) -> LLMResult:
        reply = self._reply(request)
        return LLMResult(
            content=reply, finish_reason="stop", usage=self._usage(request, reply), model=request.model
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        reply = self._reply(request)
        words = reply.split(" ")
        for i, word in enumerate(words):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield StreamChunk(delta=word if i == 0 else f" {word}")
        yield StreamChunk(
            done=True, finish_reason="stop", usage=self._usage(request, reply), model=request.model
        )
