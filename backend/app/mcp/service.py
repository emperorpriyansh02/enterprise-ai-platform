"""MCP tool rules: the server registry, refreshing tool lists, the admin allowlist, and chat toolboxes.

Safety rules kept here:
- New tools start switched off. An admin enables each one; only enabled tools reach a model.
- A tool that starts reporting itself as destructive on a refresh is switched off again.
- Secret values (stdio env, HTTP headers) are encrypted in the database and never returned.
"""

import logging
import sys
import time
import uuid
from datetime import UTC, datetime

from mcp_types import Tool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    AppError,
    MCPServerConflictError,
    MCPServerNotFoundError,
    MCPStdioDisabledError,
    MCPToolNotFoundError,
)
from app.llm.base import ToolSpec
from app.mcp.manager import MCPManager, ServerTarget
from app.mcp.models import MCPServerRow, MCPToolRow
from app.mcp.schemas import (
    AvailableServer,
    AvailableTool,
    AvailableTools,
    PresetList,
    ServerCreate,
    ServerList,
    ServerOut,
    ServerUpdate,
    ToolCallOutcome,
    ToolOut,
    ToolsRef,
)
from app.mcp.secrets import decrypt_secrets, encrypt_secrets
from app.mcp.toolbox import Toolbox, ToolEntry, format_result, model_tool_name, tool_schema

logger = logging.getLogger(__name__)


class MCPService:
    def __init__(self, session: AsyncSession, manager: MCPManager, settings: Settings) -> None:
        self._session = session
        self._manager = manager
        self._settings = settings

    @property
    def _secret_key(self) -> str | None:
        key = self._settings.secret_key
        return key.get_secret_value() if key else None

    # --- Registry ---

    async def list_servers(self) -> ServerList:
        servers = list((await self._session.scalars(select(MCPServerRow).order_by(MCPServerRow.name))).all())
        tools = await self._tools_by_server([s.id for s in servers])
        return ServerList(data=[_server_out(s, tools.get(s.id, [])) for s in servers])

    async def get_server(self, name: str) -> ServerOut:
        return await self._out(await self._require_server(name))

    async def create_server(self, body: ServerCreate, *, created_by: str | None = None) -> ServerOut:
        """Save the server, then try to connect and list its tools (a failure is kept in `last_error`)."""
        if body.transport == "stdio" and not self._settings.mcp_allow_stdio:
            raise MCPStdioDisabledError("Local (stdio) MCP servers are switched off (MCP_ALLOW_STDIO=false).")
        if await self._find_server(body.name) is not None:
            raise MCPServerConflictError(f'An MCP server named "{body.name}" already exists.')
        stdio = body.transport == "stdio"
        server = MCPServerRow(
            name=body.name,
            description=body.description,
            transport=body.transport,
            command=body.command if stdio else None,
            args=body.args if stdio else [],
            env_names=sorted(body.env) if stdio else [],
            env_secret=encrypt_secrets(body.env, self._secret_key) if stdio else None,
            url=None if stdio else body.url,
            header_names=[] if stdio else sorted(body.headers),
            header_secret=None if stdio else encrypt_secrets(body.headers, self._secret_key),
            enabled=True,
            allowed_roles=[],
            created_by=created_by,
        )
        self._session.add(server)
        await self._commit()
        await self._refresh(server, raise_errors=False)
        return await self._out(server)

    async def update_server(self, name: str, body: ServerUpdate) -> ServerOut:
        server = await self._require_server(name)
        if body.description is not None:
            server.description = body.description
        if body.enabled is not None:
            server.enabled = body.enabled
        await self._commit()
        if not server.enabled:
            await self._manager.disconnect(server.name)
        return await self._out(server)

    async def delete_server(self, name: str) -> None:
        server = await self._require_server(name)
        await self._session.delete(server)
        await self._commit()
        await self._manager.disconnect(name)

    async def refresh_server(self, name: str) -> ServerOut:
        """Connect, read the server's current tool list, and update the stored tools."""
        server = await self._require_server(name)
        await self._refresh(server, raise_errors=True)
        return await self._out(server)

    async def set_tool_enabled(self, server_name: str, tool_name: str, enabled: bool) -> ToolOut:
        server = await self._require_server(server_name)
        tool = await self._require_tool(server, tool_name)
        tool.enabled = enabled
        await self._commit()
        logger.info(
            "mcp tool %s",
            "enabled" if enabled else "disabled",
            extra={"server": server.name, "tool": tool.name},
        )
        return _tool_out(tool)

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> ToolCallOutcome:
        """Admin "try it" call. Works on switched-off tools too, so a tool can be checked before enabling."""
        server = await self._require_server(server_name)
        tool = await self._require_tool(server, tool_name)
        target = self._target(server)
        await self._end_read()
        started = time.perf_counter()
        result = await self._manager.call_tool(target, tool.name, arguments, retry_safe=not tool.destructive)
        return ToolCallOutcome(
            content=format_result(result, self._settings.mcp_max_result_chars),
            is_error=bool(result.is_error),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # --- Chat ---

    async def available_tools(self) -> AvailableTools:
        """What the chat page can offer: enabled servers with at least one enabled tool."""
        servers = list(
            (
                await self._session.scalars(
                    select(MCPServerRow).where(MCPServerRow.enabled.is_(True)).order_by(MCPServerRow.name)
                )
            ).all()
        )
        tools = await self._tools_by_server([s.id for s in servers], enabled_only=True)
        return AvailableTools(
            data=[
                AvailableServer(
                    name=s.name,
                    description=s.description,
                    tools=[
                        AvailableTool(name=t.name, description=t.description, destructive=t.destructive)
                        for t in tools[s.id]
                    ],
                )
                for s in servers
                if tools.get(s.id)
            ]
        )

    async def toolbox_for_chat(self, ref: ToolsRef) -> Toolbox:
        """The enabled tools of the servers a chat request names. Unknown or disabled server → 404."""
        names = list(dict.fromkeys(ref.servers))
        rows = (await self._session.scalars(select(MCPServerRow).where(MCPServerRow.name.in_(names)))).all()
        servers = {s.name: s for s in rows}
        for name in names:
            if name not in servers or not servers[name].enabled:
                raise MCPServerNotFoundError(f'No enabled MCP server named "{name}".')
        tools = await self._tools_by_server([s.id for s in servers.values()], enabled_only=True)
        entries: list[ToolEntry] = []
        taken: set[str] = set()
        for name in names:
            server = servers[name]
            target = self._target(server)
            for tool in tools.get(server.id, []):
                model_name = model_tool_name(server.name, tool.name, taken)
                taken.add(model_name)
                entries.append(
                    ToolEntry(
                        spec=ToolSpec(
                            name=model_name,
                            description=tool.description or tool.name,
                            input_schema=tool_schema(tool.input_schema),
                        ),
                        server=server.name,
                        tool=tool.name,
                        destructive=tool.destructive,
                        target=target,
                    )
                )
        return Toolbox(self._manager, entries, max_result_chars=self._settings.mcp_max_result_chars)

    # --- Helpers ---

    async def _refresh(self, server: MCPServerRow, *, raise_errors: bool) -> None:
        """Connect, list tools, upsert them; record success or the error on the server row."""
        try:
            target = self._target(server)
            await self._end_read()
            listed = await self._manager.list_tools(target)
        except AppError as exc:
            server.last_error = exc.message
            await self._commit()
            logger.warning("mcp refresh failed", extra={"server": server.name, "error_code": exc.code})
            if raise_errors:
                raise
            return
        await self._upsert_tools(server, listed)
        server.last_error = None
        server.last_connected_at = datetime.now(UTC)
        await self._commit()
        logger.info("mcp refreshed", extra={"server": server.name, "tools": len(listed)})

    async def _upsert_tools(self, server: MCPServerRow, listed: list[Tool]) -> None:
        existing = {t.name: t for t in (await self._tools_by_server([server.id])).get(server.id, [])}
        seen: set[str] = set()
        for tool in listed:
            seen.add(tool.name)
            read_only, destructive = _hints(tool)
            row = existing.get(tool.name)
            if row is None:
                self._session.add(
                    MCPToolRow(
                        server_id=server.id,
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.input_schema or {},
                        read_only=read_only,
                        destructive=destructive,
                        enabled=False,  # admin allowlist: off until switched on
                    )
                )
                continue
            if destructive and not row.destructive and row.enabled:
                row.enabled = False  # it now says it can delete/overwrite: needs a fresh admin decision
                logger.warning(
                    "mcp tool became destructive; disabled", extra={"server": server.name, "tool": row.name}
                )
            row.description = tool.description or ""
            row.input_schema = tool.input_schema or {}
            row.read_only, row.destructive = read_only, destructive
        for name, row in existing.items():
            if name not in seen:
                await self._session.delete(row)  # the server no longer offers it

    def _target(self, server: MCPServerRow) -> ServerTarget:
        """How to connect, with secrets decrypted. Checks that stdio is allowed."""
        if server.transport == "stdio" and not self._settings.mcp_allow_stdio:
            raise MCPStdioDisabledError("Local (stdio) MCP servers are switched off (MCP_ALLOW_STDIO=false).")
        return ServerTarget(
            name=server.name,
            transport=server.transport,
            command=server.command,
            args=list(server.args or []),
            env=decrypt_secrets(server.env_secret, self._secret_key),
            url=server.url,
            headers=decrypt_secrets(server.header_secret, self._secret_key),
        )

    async def _end_read(self) -> None:
        """Finish the read transaction before a slow network call, so no database connection waits on it."""
        await self._session.commit()

    async def _find_server(self, name: str) -> MCPServerRow | None:
        return await self._session.scalar(select(MCPServerRow).where(MCPServerRow.name == name))

    async def _require_server(self, name: str) -> MCPServerRow:
        server = await self._find_server(name)
        if server is None:
            raise MCPServerNotFoundError(f'No MCP server named "{name}".')
        return server

    async def _require_tool(self, server: MCPServerRow, name: str) -> MCPToolRow:
        tool = await self._session.scalar(
            select(MCPToolRow).where(MCPToolRow.server_id == server.id, MCPToolRow.name == name)
        )
        if tool is None:
            raise MCPToolNotFoundError(
                f'MCP server "{server.name}" has no tool named "{name}". Try a refresh.'
            )
        return tool

    async def _tools_by_server(
        self, server_ids: list[uuid.UUID], *, enabled_only: bool = False
    ) -> dict[uuid.UUID, list[MCPToolRow]]:
        if not server_ids:
            return {}
        query = select(MCPToolRow).where(MCPToolRow.server_id.in_(server_ids)).order_by(MCPToolRow.name)
        if enabled_only:
            query = query.where(MCPToolRow.enabled.is_(True))
        grouped: dict[uuid.UUID, list[MCPToolRow]] = {}
        for tool in (await self._session.scalars(query)).all():
            grouped.setdefault(tool.server_id, []).append(tool)
        return grouped

    async def _out(self, server: MCPServerRow) -> ServerOut:
        tools = (await self._tools_by_server([server.id])).get(server.id, [])
        return _server_out(server, tools)

    async def _commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise MCPServerConflictError("An MCP server with this name already exists.") from exc


def presets() -> PresetList:
    """Server definitions that work on this backend as-is: the demo server, run with the backend's Python."""
    demo = ServerCreate(
        name="demo",
        description="Demo tools: current time, calculator, sample orders",
        transport="stdio",
        command=sys.executable,
        args=["-m", "app.mcp.demo_server"],
    )
    return PresetList(data=[demo])


def _hints(tool: Tool) -> tuple[bool, bool]:
    """(read_only, destructive) from the tool's annotations, with the MCP spec's defaults.

    The spec's default for `destructiveHint` is true for any tool that isn't read-only, so a tool
    without annotations counts as destructive. That only affects labels and retries; every new tool
    is switched off until an admin enables it anyway.
    """
    hints = tool.annotations
    read_only = bool(hints and hints.read_only_hint)
    if read_only:
        return True, False
    destructive = True if hints is None or hints.destructive_hint is None else bool(hints.destructive_hint)
    return False, destructive


def _tool_out(tool: MCPToolRow) -> ToolOut:
    return ToolOut(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema or {},
        read_only=tool.read_only,
        destructive=tool.destructive,
        enabled=tool.enabled,
    )


def _server_out(server: MCPServerRow, tools: list[MCPToolRow]) -> ServerOut:
    return ServerOut(
        name=server.name,
        description=server.description,
        transport=server.transport,  # type: ignore[arg-type]
        command=server.command,
        args=list(server.args or []),
        env_names=list(server.env_names or []),
        url=server.url,
        header_names=list(server.header_names or []),
        enabled=server.enabled,
        allowed_roles=list(server.allowed_roles or []),
        last_error=server.last_error,
        last_connected_at=server.last_connected_at,
        tools=[_tool_out(t) for t in tools],
        created_at=server.created_at,
        updated_at=server.updated_at,
    )
