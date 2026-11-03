"""A tiny MCP server for tests: tools that crash, hang, report their process id, or read env vars.

Run over stdio: python -m tests.mcp_test_server
"""

import asyncio
import os

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

server = MCPServer("test")
_READ_ONLY = ToolAnnotations(read_only_hint=True)


@server.tool(annotations=_READ_ONLY)
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool(annotations=_READ_ONLY)
def pid() -> str:
    """The server's process id (a new process means a new connection)."""
    return str(os.getpid())


@server.tool(annotations=_READ_ONLY)
def env(name: str) -> str:
    """The value of an environment variable in the server process."""
    return os.environ.get(name, "(unset)")


@server.tool(annotations=_READ_ONLY)
async def slow(seconds: float) -> str:
    """Wait, then answer (to test timeouts)."""
    await asyncio.sleep(seconds)
    return "done"


@server.tool()
def crash() -> str:
    """Kill the server process mid-call. No annotations, so it counts as destructive."""
    os._exit(1)


if __name__ == "__main__":
    server.run()
