"""Public API shapes for MCP tools, and the `tools` field / tool trace on chat."""

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.prompts.schemas import NAME_PATTERN

# --- Requests ---


class ServerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=40, pattern=NAME_PATTERN, examples=["orders"])
    description: str = Field(default="", max_length=500)
    transport: Literal["stdio", "http"]
    command: str | None = Field(default=None, max_length=500, examples=["uvx"], description="stdio only")
    args: list[str] = Field(default_factory=list, max_length=50, examples=[["mcp-server-time"]])
    env: dict[str, str] = Field(default_factory=dict, description="stdio only. Stored encrypted.")
    url: str | None = Field(default=None, max_length=2000, examples=["https://mcp.example.com/mcp"])
    headers: dict[str, str] = Field(default_factory=dict, description="http only. Stored encrypted.")

    @model_validator(mode="after")
    def _check_transport(self) -> Self:
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio servers need a `command`")
        if self.transport == "http" and not (self.url or "").startswith(("http://", "https://")):
            raise ValueError("http servers need a `url` starting with http:// or https://")
        return self


class ServerUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class ToolUpdate(BaseModel):
    enabled: bool


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolsRef(BaseModel):
    """The `tools` field on a chat request: which MCP servers' enabled tools the model may use."""

    servers: list[str] = Field(min_length=1, max_length=10, examples=[["demo"]])


# --- Responses ---


class ToolOut(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    destructive: bool
    enabled: bool


class ServerOut(BaseModel):
    name: str
    description: str
    transport: Literal["stdio", "http"]
    command: str | None
    args: list[str]
    env_names: list[str] = Field(description="Names only; values are never returned.")
    url: str | None
    header_names: list[str] = Field(description="Names only; values are never returned.")
    enabled: bool
    allowed_roles: list[str]
    last_error: str | None
    last_connected_at: datetime | None
    tools: list[ToolOut]
    created_at: datetime
    updated_at: datetime


class ServerList(BaseModel):
    data: list[ServerOut]


class PresetList(BaseModel):
    """Ready-made server definitions the Tools page can fill in (e.g. the built-in demo server)."""

    data: list[ServerCreate]


class ToolCallOutcome(BaseModel):
    content: str
    is_error: bool
    duration_ms: int


class AvailableTool(BaseModel):
    name: str
    description: str
    destructive: bool


class AvailableServer(BaseModel):
    name: str
    description: str
    tools: list[AvailableTool]


class AvailableTools(BaseModel):
    """What a chat may use: enabled servers that have at least one enabled tool."""

    data: list[AvailableServer]


class ToolCallTrace(BaseModel):
    """One tool call made while answering, shown under the reply and logged."""

    id: str
    server: str
    tool: str
    arguments: dict[str, Any]
    result: str | None = Field(default=None, description="First 500 characters of what the tool returned.")
    is_error: bool = False
    duration_ms: int | None = None
