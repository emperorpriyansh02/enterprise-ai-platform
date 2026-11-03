"""Connections to MCP servers: one long-lived client per server, shared by every request.

Each connection runs in its own background task. The MCP client is an anyio context manager that
must be opened and closed by the same task, so `_Connection._run` opens it, hands it out, and keeps
it open until `close()` is called. A server that crashes or drops the connection is reconnected on the
next use.

Created once in the app's lifespan (`app.state.mcp`) and closed on shutdown.
"""

import asyncio
import hashlib
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED, REQUEST_TIMEOUT, CallToolResult, Implementation, TextContent, Tool

from app.core.config import Settings
from app.core.errors import MCPConnectionError, MCPStdioDisabledError

logger = logging.getLogger(__name__)

_CLIENT_INFO = Implementation(name="enterprise-ai-platform", version="0.1.0")
_MAX_TOOL_PAGES = 20  # tools/list pages to read before giving up (stops a server that never ends)
_CLOSE_WAIT_S = 5.0
# Errors that mean "the connection is gone", not "the tool failed".
_LOST = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    anyio.EndOfStream,
    httpx2.TransportError,
    OSError,
)


@dataclass(frozen=True)
class ServerTarget:
    """How to reach one MCP server. Secrets are already decrypted here; never log this object."""

    name: str
    transport: str  # "stdio" or "http"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict, repr=False)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict, repr=False)

    def fingerprint(self) -> str:
        """Changes whenever any setting changes, so an edited server gets a fresh connection."""
        raw = json.dumps(
            [self.transport, self.command, self.args, self.env, self.url, self.headers], sort_keys=True
        )
        return hashlib.sha256(raw.encode()).hexdigest()


class MCPManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connections: dict[str, _Connection] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def list_tools(self, target: ServerTarget) -> list[Tool]:
        """Every tool the server offers right now (all pages). Retries once on a dropped connection."""
        timeout = self._settings.mcp_call_timeout_s
        for attempt in (1, 2):
            client = await self._client(target)
            try:
                return await asyncio.wait_for(_all_tools(client), timeout)
            except TimeoutError:
                await self.disconnect(target.name)
                raise MCPConnectionError(
                    f"MCP server '{target.name}' didn't list its tools within {timeout:g}s."
                ) from None
            except Exception as exc:
                lost = _is_lost(exc)
                if lost:
                    await self.disconnect(target.name)
                if lost and attempt == 1:
                    continue  # listing is safe to repeat
                raise MCPConnectionError(
                    f"MCP server '{target.name}' couldn't list its tools: {_describe(exc)}"
                ) from exc
        raise AssertionError("unreachable")

    async def call_tool(
        self, target: ServerTarget, tool: str, arguments: dict[str, Any], *, retry_safe: bool
    ) -> CallToolResult:
        """Run one tool. A tool's own failure comes back as a result with `is_error=True`.

        Raises MCPConnectionError when the server can't be reached, drops the connection, or doesn't
        answer within MCP_CALL_TIMEOUT_S. With `retry_safe` (not a destructive tool), a call that hit
        a dropped connection is sent once more on a fresh one.
        """
        timeout = self._settings.mcp_call_timeout_s
        for attempt in (1, 2):
            client = await self._client(target)
            try:
                return await asyncio.wait_for(
                    client.call_tool(tool, arguments, read_timeout_seconds=timeout), timeout + 1
                )
            except TimeoutError:
                raise MCPConnectionError(
                    f"Tool '{tool}' on MCP server '{target.name}' didn't finish within {timeout:g}s."
                ) from None
            except MCPError as exc:
                if exc.code == REQUEST_TIMEOUT:
                    raise MCPConnectionError(
                        f"Tool '{tool}' on MCP server '{target.name}' didn't finish within {timeout:g}s."
                    ) from exc
                if exc.code != CONNECTION_CLOSED:
                    # The server answered with a protocol error (unknown tool, bad arguments...).
                    return CallToolResult(
                        content=[TextContent(text=f"MCP error: {exc.message}")], is_error=True
                    )
                error: Exception = exc
            except Exception as exc:
                if not _is_lost(exc):
                    raise MCPConnectionError(
                        f"Tool '{tool}' on MCP server '{target.name}' failed: {_describe(exc)}"
                    ) from exc
                error = exc
            # The connection is gone.
            await self.disconnect(target.name)
            if retry_safe and attempt == 1:
                logger.info("mcp: connection to %s lost, retrying %s once", target.name, tool)
                continue
            unsure = "" if retry_safe else " The tool may or may not have run."
            raise MCPConnectionError(
                f"Lost the connection to MCP server '{target.name}' during '{tool}'.{unsure}"
            ) from error
        raise AssertionError("unreachable")

    async def disconnect(self, name: str) -> None:
        """Close the connection to one server, if open (e.g. after it was edited, disabled or deleted)."""
        connection = self._connections.pop(name, None)
        if connection is not None:
            await connection.close()

    def is_connected(self, name: str) -> bool:
        connection = self._connections.get(name)
        return connection is not None and connection.alive

    async def aclose(self) -> None:
        """Close every connection (stops stdio server processes). Called on app shutdown."""
        await asyncio.gather(*(self.disconnect(name) for name in list(self._connections)))

    async def _client(self, target: ServerTarget) -> Client:
        """The open client for this server: reuses a live connection, otherwise connects."""
        if target.transport == "stdio" and not self._settings.mcp_allow_stdio:
            raise MCPStdioDisabledError("Local (stdio) MCP servers are switched off on this platform.")
        lock = self._locks.setdefault(target.name, asyncio.Lock())
        async with lock:  # one connect at a time per server
            connection = self._connections.get(target.name)
            if connection is not None and (
                not connection.alive or connection.fingerprint != target.fingerprint()
            ):
                await self.disconnect(target.name)
                connection = None
            if connection is None:
                connection = _Connection(target, self._settings)
                await connection.open()
                self._connections[target.name] = connection
            return connection.client


class _Connection:
    """One open MCP client, owned by a background task."""

    def __init__(self, target: ServerTarget, settings: Settings) -> None:
        self.target = target
        self.fingerprint = target.fingerprint()
        self._settings = settings
        self._client: Client | None = None
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Future[None] | None = None
        self._stop = asyncio.Event()

    @property
    def alive(self) -> bool:
        return self._client is not None and self._task is not None and not self._task.done()

    @property
    def client(self) -> Client:
        assert self._client is not None, "connection is not open"
        return self._client

    async def open(self) -> None:
        """Start the background task and wait until the MCP handshake is done (or fails / times out)."""
        name, timeout = self.target.name, self._settings.mcp_connect_timeout_s
        self._ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._run(), name=f"mcp:{name}")
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout)
        except TimeoutError:
            await self.close()
            raise MCPConnectionError(f"MCP server '{name}' didn't answer within {timeout:g}s.") from None
        except Exception as exc:
            await self.close()
            raise MCPConnectionError(f"Couldn't connect to MCP server '{name}': {_describe(exc)}") from exc
        logger.info("mcp: connected to %s (%s)", name, self.target.transport)

    async def _run(self) -> None:
        assert self._ready is not None
        try:
            async with AsyncExitStack() as stack:
                transport = await self._transport(stack)
                self._client = await stack.enter_async_context(
                    Client(
                        transport,
                        read_timeout_seconds=self._settings.mcp_call_timeout_s,
                        client_info=_CLIENT_INFO,
                    )
                )
                self._ready.set_result(None)
                await self._stop.wait()
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.cancel()
            raise
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(_leaf(exc))
            else:
                logger.warning("mcp: connection to %s ended: %s", self.target.name, _describe(exc))
        finally:
            self._client = None

    async def _transport(self, stack: AsyncExitStack) -> Any:
        """What `Client` connects through: a subprocess (stdio) or a streamable-HTTP transport."""
        target = self.target
        if target.transport == "stdio":
            assert target.command
            return StdioServerParameters(command=target.command, args=list(target.args), env=dict(target.env))
        assert target.url
        http = await stack.enter_async_context(
            create_mcp_http_client(
                headers=dict(target.headers) or None,
                timeout=httpx2.Timeout(self._settings.mcp_connect_timeout_s, read=300),
            )
        )
        return streamable_http_client(target.url, http_client=http)

    async def close(self) -> None:
        """Ask the task to close the client (stops a stdio process); cancel it if it doesn't stop in time."""
        self._stop.set()
        task = self._task
        if task is None or task.done():
            return
        _, pending = await asyncio.wait({task}, timeout=_CLOSE_WAIT_S)
        if pending:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _all_tools(client: Client) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    for _ in range(_MAX_TOOL_PAGES):
        page = await client.list_tools(cursor=cursor, cache_mode="refresh")
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            break
    return tools


def _leaf(exc: BaseException) -> BaseException:
    """The first real error inside (nested) exception groups, which anyio task groups raise."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _is_lost(exc: BaseException) -> bool:
    exc = _leaf(exc)
    if isinstance(exc, MCPError):
        return exc.code == CONNECTION_CLOSED
    return isinstance(exc, _LOST)


def _describe(exc: BaseException) -> str:
    """A short, readable reason for an error: `ConnectError: [Errno 111] Connection refused`."""
    exc = _leaf(exc)
    if isinstance(exc, MCPConnectionError):
        return exc.message
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
