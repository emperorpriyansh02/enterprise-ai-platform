"""Public API shapes for prompts (admin API and the `prompt` field on chat requests)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.prompts.template import Scalar

NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"  # lowercase words joined by hyphens: "support-agent"

Name = Field(min_length=2, max_length=64, pattern=NAME_PATTERN, examples=["support-agent"])
Template = Field(min_length=1, max_length=100_000, examples=["You help customers of {{ company_name }}."])
Note = Field(default="", max_length=500, description="What changed in this version.")


# --- Requests ---


class PromptCreate(BaseModel):
    name: str = Name
    description: str = Field(default="", max_length=500)
    template: str = Template
    note: str = Note


class PromptUpdate(BaseModel):
    description: str = Field(max_length=500)


class PromptVersionCreate(BaseModel):
    template: str = Template
    note: str = Note
    activate: bool = Field(default=False, description="Make this the active version right away.")


class ActivateRequest(BaseModel):
    version: int = Field(ge=1)


class RenderRequest(BaseModel):
    version: int | None = Field(default=None, ge=1, description="Omit to use the active version.")
    variables: dict[str, Scalar] = Field(default_factory=dict)


class PromptRef(BaseModel):
    """The `prompt` field on a chat request: which managed prompt to use as the system prompt."""

    name: str = Field(min_length=1, max_length=64, examples=["support-agent"])
    version: int | None = Field(default=None, ge=1, description="Omit to use the active version.")
    variables: dict[str, Scalar] = Field(default_factory=dict, examples=[{"company_name": "Acme"}])


# --- Responses ---


class PromptUsed(BaseModel):
    """Which prompt version a chat reply used. Recorded on every reply for evals and audits."""

    name: str
    version: int


class PromptVersionOut(BaseModel):
    version: int
    template: str
    variables: list[str]
    note: str
    created_by: str | None
    created_at: datetime
    is_active: bool


class PromptSummary(BaseModel):
    name: str
    description: str
    active_version: int | None
    latest_version: int
    active_variables: list[str] = Field(description="Variables the active version needs.")
    archived: bool
    created_at: datetime
    updated_at: datetime


class PromptDetail(PromptSummary):
    versions: list[PromptVersionOut] = Field(description="Newest first.")


class PromptList(BaseModel):
    data: list[PromptSummary]


class RenderResponse(BaseModel):
    name: str
    version: int
    text: str
    variables: list[str]
