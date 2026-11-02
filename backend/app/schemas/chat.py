"""Public API schemas: one request/response shape for every LLM provider."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.prompts.schemas import PromptRef, PromptUsed
from app.rag.schemas import Citation, RagRef

Role = Literal["user", "assistant"]
FinishReason = Literal["stop", "length", "refusal", "content_filter", "other"]


class Message(BaseModel):
    role: Role
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    model: str | None = Field(
        default=None,
        description='"provider/model", e.g. "anthropic/claude-opus-5-5". Omit to use the default.',
        examples=["anthropic/claude-opus-5-5"],
    )
    messages: list[Message] = Field(min_length=1)
    prompt: PromptRef | None = Field(
        default=None,
        description="Managed prompt to use as the system prompt (Layer 2). `system` is appended after it.",
    )
    system: str | None = Field(
        default=None, description="System prompt, or extra instructions with `prompt`."
    )
    rag: RagRef | None = Field(
        default=None, description="Knowledge bases to search for this message (Layer 3). Adds cited sources."
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Ignored by providers whose models don't accept it (e.g. current Claude models).",
    )
    max_tokens: int | None = Field(default=None, ge=1, le=128_000)
    provider_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra provider-specific parameters, passed through to the SDK call as-is.",
    )

    @field_validator("messages")
    @classmethod
    def _first_message_from_user(cls, messages: list[Message]) -> list[Message]:
        if messages[0].role != "user":
            raise ValueError("the first message must have role 'user'")
        return messages


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    id: str
    provider: str
    model: str = Field(description="Model that actually served the request (may differ after a fallback).")
    content: str
    finish_reason: FinishReason
    usage: Usage
    latency_ms: int
    prompt: PromptUsed | None = Field(default=None, description="Managed prompt version used, if any.")
    citations: list[Citation] | None = Field(
        default=None, description="Sources given to the model when `rag` was used ([] = none found)."
    )


class StreamEvent(BaseModel):
    """One server-sent event on /v1/chat/stream.

    Order: one `start`, any number of `delta`, then exactly one `done` or `error`.
    """

    type: Literal["start", "delta", "done", "error"]
    id: str
    provider: str | None = None
    model: str | None = None
    delta: str | None = None
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    latency_ms: int | None = None
    prompt: PromptUsed | None = None
    citations: list[Citation] | None = None  # on `start`, when `rag` was used
    error: dict | None = None


class ModelInfo(BaseModel):
    id: str = Field(description='Value to send as "model", e.g. "openai/gpt-5".')
    provider: str
    model: str


class ModelList(BaseModel):
    default: str | None
    data: list[ModelInfo]
