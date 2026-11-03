"""The tools one chat message may use, and running the calls a model asks for.

Built by `MCPService.toolbox_for_chat` from the database, so it holds plain data and no database
session: the tool loop can run for a while without keeping a database connection busy.

Names: the model sees `server__tool` (e.g. `demo__get_time`), so two servers can both have a tool
called `search`. Names are limited to letters, digits, `_` and `-`, at most 64 characters (the
strictest of the providers).
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from mcp_types import (
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from app.core.errors import AppError
from app.llm.base import ToolCall, ToolResult, ToolSpec
from app.mcp.manager import MCPManager, ServerTarget
from app.mcp.schemas import ToolCallTrace

logger = logging.getLogger(__name__)

MAX_NAME = 64
TRACE_PREVIEW_CHARS = 500
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ToolEntry:
    """One tool offered to the model, and where to run it."""

    spec: ToolSpec  # what the model sees (spec.name is the namespaced name)
    server: str
    tool: str  # the name on the MCP server
    destructive: bool
    target: ServerTarget


class Toolbox:
    def __init__(self, manager: MCPManager, entries: list[ToolEntry], *, max_result_chars: int) -> None:
        self._manager = manager
        self._entries = {entry.spec.name: entry for entry in entries}
        self._max_result_chars = max_result_chars

    @property
    def specs(self) -> list[ToolSpec]:
        return [entry.spec for entry in self._entries.values()]

    @property
    def servers(self) -> list[str]:
        return sorted({entry.server for entry in self._entries.values()})

    def __len__(self) -> int:
        return len(self._entries)

    def trace_for(self, call: ToolCall) -> ToolCallTrace:
        """The trace entry for a call that's about to run (no result yet)."""
        entry = self._entries.get(call.name)
        return ToolCallTrace(
            id=call.id,
            server=entry.server if entry else "",
            tool=entry.tool if entry else call.name,
            arguments=call.arguments,
        )

    async def run(self, calls: list[ToolCall]) -> list[tuple[ToolResult, ToolCallTrace]]:
        """Run every call at once and return each result with its trace entry, in the same order."""
        return list(await asyncio.gather(*(self._run_one(call) for call in calls)))

    async def _run_one(self, call: ToolCall) -> tuple[ToolResult, ToolCallTrace]:
        trace = self.trace_for(call)
        entry = self._entries.get(call.name)
        started = time.perf_counter()
        if entry is None:
            text, is_error = f"Unknown tool '{call.name}'. Use only the tools you were given.", True
        else:
            try:
                result = await self._manager.call_tool(
                    entry.target, entry.tool, call.arguments, retry_safe=not entry.destructive
                )
                text, is_error = format_result(result, self._max_result_chars), bool(result.is_error)
            except AppError as exc:  # can't reach the server, timeout...: tell the model, keep going
                text, is_error = exc.message, True
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "tool call",
            extra={
                "server": trace.server,
                "tool": trace.tool,
                "is_error": is_error,
                "latency_ms": duration_ms,
                "result_chars": len(text),
            },
        )
        trace = trace.model_copy(
            update={"result": text[:TRACE_PREVIEW_CHARS], "is_error": is_error, "duration_ms": duration_ms}
        )
        return ToolResult(call_id=call.id, name=call.name, content=text, is_error=is_error), trace


def model_tool_name(server: str, tool: str, taken: set[str]) -> str:
    """`server__tool`, made safe for every provider and unique among `taken`."""
    name = _UNSAFE.sub("_", f"{server}__{tool}")
    if not name[0].isalpha():
        name = f"t_{name}"
    if len(name) > MAX_NAME or name in taken:
        digest = hashlib.sha256(f"{server}/{tool}".encode()).hexdigest()[:8]
        name = f"{name[: MAX_NAME - 9]}_{digest}"
    return name


def tool_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """The tool's JSON Schema for its arguments; providers need an object schema, so default to one."""
    if not schema or schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    return schema


def format_result(result: CallToolResult, max_chars: int) -> str:
    """Turn an MCP tool result into the text the model reads, cut to `max_chars`."""
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            parts.append(f"[image: {block.mime_type}]")
        elif isinstance(block, AudioContent):
            parts.append(f"[audio: {block.mime_type}]")
        elif isinstance(block, EmbeddedResource):
            resource = block.resource
            if isinstance(resource, TextResourceContents):
                parts.append(resource.text)
            else:
                parts.append(f"[binary resource: {resource.uri}]")
        elif isinstance(block, ResourceLink):
            parts.append(f"[resource: {block.uri}]")
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False, default=str))
    text = "\n".join(parts).strip() or "(the tool returned no output)"
    if len(text) > max_chars:
        cut = len(text) - max_chars
        text = f"{text[:max_chars]}\n[... {cut} more characters cut]"
    return text
