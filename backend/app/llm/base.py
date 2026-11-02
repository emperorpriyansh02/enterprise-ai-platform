"""The contract every LLM provider adapter implements.

Adapters turn an `LLMRequest` into a vendor SDK call and turn the vendor's response back into
`LLMResult` / `StreamChunk`. Nothing outside `app/llm/providers/` touches a vendor SDK.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.schemas.chat import FinishReason, Message, Usage


@dataclass(frozen=True)
class LLMRequest:
    model: str  # bare provider model ID, e.g. "claude-opus-5-5" (no "anthropic/" prefix)
    messages: list[Message]
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    content: str
    finish_reason: FinishReason
    usage: Usage
    model: str  # model that actually served the request


@dataclass(frozen=True)
class StreamChunk:
    """Either a text delta, or (when `done`) the final metadata for the stream."""

    delta: str = ""
    done: bool = False
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    model: str | None = None


class LLMProvider(ABC):
    name: str

    def __init__(self, models: list[str]) -> None:
        self.models = list(models)

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResult:
        """Run a request and return the full response."""

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Yield text deltas, then exactly one final chunk with `done=True`."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook, no-op by default
        """Release network clients. Called on app shutdown."""
