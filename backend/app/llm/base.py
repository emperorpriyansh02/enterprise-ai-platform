"""The contract every LLM provider adapter implements.

Adapters turn an `LLMRequest` into a vendor SDK call and turn the vendor's response back into
`LLMResult` / `StreamChunk`. Nothing outside `app/llm/providers/` touches a vendor SDK.

Tool use (Layer 4): a request can offer `tools`. When the model wants to use some, the result has
`tool_calls` and an opaque `provider_state` (the model's turn in the vendor's own format). The caller
runs the tools and sends the request again with a `ToolTurn` added to `turns`; the adapter replays
`provider_state` exactly as the vendor sent it (needed e.g. for Claude's thinking blocks and Gemini's
thought signatures), followed by the tool results.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

from app.schemas.chat import FinishReason, Message, Usage


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. `name` is what the model sees (letters, digits, _ and -)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str  # the vendor's call id, or one we made up when the vendor has none
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class ToolTurn:
    """One completed round: what the model asked for, and what the tools answered."""

    text: str  # any text the model wrote alongside its tool calls
    calls: list[ToolCall]
    provider_state: Any  # the model's turn in the vendor's format, replayed unchanged
    results: list[ToolResult]


@dataclass(frozen=True)
class LLMRequest:
    model: str  # bare provider model ID, e.g. "claude-opus-5-5" (no "anthropic/" prefix)
    messages: list[Message]
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)
    tools: list[ToolSpec] = field(default_factory=list)
    turns: list[ToolTurn] = field(default_factory=list)  # tool rounds so far, after `messages`

    def with_turn(self, turn: ToolTurn) -> "LLMRequest":
        return replace(self, turns=[*self.turns, turn])


@dataclass(frozen=True)
class LLMResult:
    content: str
    finish_reason: FinishReason
    usage: Usage
    model: str  # model that actually served the request
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider_state: Any = None  # set when tool_calls is not empty


@dataclass(frozen=True)
class StreamChunk:
    """Either a text delta, or (when `done`) the final metadata for the stream."""

    delta: str = ""
    done: bool = False
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    model: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)  # final chunk only
    provider_state: Any = None  # final chunk only, when tool_calls is not empty


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
