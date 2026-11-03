"""API shapes for guardrail policies, test runs and events, and the trace on chat replies.

Each policy kind has its own `config` shape, checked when the policy is saved:
  pii        {"entities": ["email", "phone", ...]}
  injection  {"use_llm": true}
  harmful    {"use_llm": true, "categories": ["self_harm", ...]}
  topic      {"keywords": [...], "patterns": [regex...], "topics": ["legal advice"], "use_llm": true}
Every kind also takes `message`: what the user sees when the policy blocks.
"""

import re
import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.guardrails.pii import ENTITIES
from app.guardrails.rules import HARMFUL_CATEGORIES
from app.prompts.schemas import NAME_PATTERN

Kind = Literal["pii", "injection", "harmful", "topic"]
Stage = Literal["input", "context", "output"]  # user messages / retrieved passages + tool results / the reply
Action = Literal["block", "redact", "log"]
TraceAction = Literal["blocked", "redacted", "removed", "logged"]
Detector = Literal["rules", "llm"]
EntityName = Literal[
    "secret", "email", "credit_card", "iban", "us_ssn", "aadhaar", "pan", "phone", "ip_address"
]
HarmCategory = Literal[
    "self_harm", "violence", "weapons", "drugs", "hate", "harassment", "sexual_minors", "extremism"
]
assert set(EntityName.__args__) == set(ENTITIES)  # keep the API names and the detector in step
assert set(HarmCategory.__args__) == set(HARMFUL_CATEGORIES)

Message = Field(default=None, max_length=500, description="Shown to the user when this policy blocks.")


class PiiConfig(BaseModel):
    entities: list[EntityName] = Field(default_factory=lambda: list(ENTITIES), min_length=1)
    message: str | None = Message


class InjectionConfig(BaseModel):
    use_llm: bool = Field(default=True, description="Also ask the judge model (when one is configured).")
    message: str | None = Message


class HarmfulConfig(BaseModel):
    use_llm: bool = True
    categories: list[HarmCategory] = Field(default_factory=lambda: list(HARMFUL_CATEGORIES), min_length=1)
    message: str | None = Message


class TopicConfig(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=200, description="Whole words, any case.")
    patterns: list[str] = Field(default_factory=list, max_length=50, description="Regular expressions.")
    topics: list[str] = Field(
        default_factory=list, max_length=20, description="Plain-language topics for the judge model."
    )
    use_llm: bool = True
    message: str | None = Message

    @field_validator("keywords", "topics")
    @classmethod
    def _short_items(cls, items: list[str]) -> list[str]:
        cleaned = [item.strip() for item in items if item.strip()]
        if any(len(item) > 200 for item in cleaned):
            raise ValueError("each entry must be at most 200 characters")
        return list(dict.fromkeys(cleaned))

    @field_validator("patterns")
    @classmethod
    def _valid_patterns(cls, patterns: list[str]) -> list[str]:
        cleaned = [p for p in patterns if p.strip()]
        for pattern in cleaned:
            if len(pattern) > 200:
                raise ValueError("each pattern must be at most 200 characters")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc
        return cleaned

    @model_validator(mode="after")
    def _something_to_check(self) -> Self:
        if not (self.keywords or self.patterns or self.topics):
            raise ValueError("a topic policy needs at least one keyword, pattern or topic")
        return self


CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "pii": PiiConfig,
    "injection": InjectionConfig,
    "harmful": HarmfulConfig,
    "topic": TopicConfig,
}


def validate_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Check a policy's config for its kind and fill in defaults. Raises ValueError (→ 422)."""
    try:
        return CONFIG_MODELS[kind].model_validate(config).model_dump()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'config'}: {e['msg']}" for e in exc.errors()
        )
        raise ValueError(f"invalid config for a {kind} policy: {problems}") from None


def _unique_stages(stages: list[Stage]) -> list[Stage]:
    return list(dict.fromkeys(stages))


# --- Requests ---


class PolicyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=NAME_PATTERN, examples=["no-competitors"])
    description: str = Field(default="", max_length=500)
    kind: Kind
    stages: list[Stage] = Field(min_length=1, examples=[["input", "output"]])
    action: Action
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        self.stages = _unique_stages(self.stages)
        self.config = validate_config(self.kind, self.config)
        return self


class PolicyUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    stages: list[Stage] | None = Field(default=None, min_length=1)
    action: Action | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = Field(default=None, description="Replaces the whole config.")

    @field_validator("stages")
    @classmethod
    def _dedupe(cls, stages: list[Stage] | None) -> list[Stage] | None:
        return _unique_stages(stages) if stages is not None else None


class TestRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    stage: Stage = "input"
    policy: str | None = Field(
        default=None, description="Only this policy (it may be disabled). Blank = all enabled."
    )


# --- Responses ---


class PolicyOut(BaseModel):
    name: str
    description: str
    kind: Kind
    stages: list[Stage]
    action: Action
    enabled: bool
    config: dict[str, Any]
    builtin: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class PolicyList(BaseModel):
    data: list[PolicyOut]


class GuardrailTrace(BaseModel):
    """One policy firing: on a chat reply (`guardrails`), in a `guardrail` stream event, in a test run."""

    policy: str
    kind: Kind
    stage: Stage
    action: TraceAction  # what was done: blocked the text, masked spans, removed a passage, only logged
    detector: Detector
    source: str = Field(description='What was checked: "user message", "passage 2", "tool demo__x", "reply".')
    findings: dict[str, int] = Field(description='What was found, e.g. {"email": 2}.')
    reason: str | None = Field(default=None, description="The judge model's reason, if it decided.")
    message: str | None = Field(default=None, description="The message shown instead, when blocked.")


class TestResponse(BaseModel):
    blocked: bool
    text: str = Field(description="The text after redaction, or the block message.")
    traces: list[GuardrailTrace]


class EventOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    request_id: str | None
    username: str | None
    policy: str
    kind: str
    stage: str
    action: str
    detector: str
    source: str
    findings: dict[str, int]
    reason: str | None
    excerpt: str | None


class EventList(BaseModel):
    data: list[EventOut]
