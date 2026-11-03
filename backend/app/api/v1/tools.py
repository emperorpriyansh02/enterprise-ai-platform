"""MCP tools API (Layer 4): the server registry, the tool allowlist, and a "try a tool" call.

`GET /v1/tools` (what a chat may use) needs `tools:use` (every role) and lists only servers the
caller's roles may use. Everything under /servers and /presets shows how servers are started or
reached, so it needs `tools:admin` (admins only).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import require, require_mcp_service
from app.auth.permissions import TOOLS_ADMIN, TOOLS_USE
from app.mcp.schemas import (
    AvailableTools,
    PresetList,
    ServerCreate,
    ServerList,
    ServerOut,
    ServerUpdate,
    ToolCallOutcome,
    ToolCallRequest,
    ToolOut,
    ToolUpdate,
)
from app.mcp.service import MCPService, presets

router = APIRouter(prefix="/v1/tools", tags=["tools"])

Tools = Annotated[MCPService, Depends(require_mcp_service)]
Admin = Depends(require(TOOLS_ADMIN))


@router.get("", response_model=AvailableTools, dependencies=[Depends(require(TOOLS_USE))])
async def list_available_tools(service: Tools) -> AvailableTools:
    """Enabled servers with their enabled tools: what a chat request can name in `tools.servers`."""
    return await service.available_tools()


@router.get("/presets", response_model=PresetList, dependencies=[Admin])
async def list_presets() -> PresetList:
    """Ready-made server definitions, e.g. the built-in demo server with this backend's Python path."""
    return presets()


@router.get("/servers", response_model=ServerList, dependencies=[Admin])
async def list_servers(service: Tools) -> ServerList:
    return await service.list_servers()


@router.post("/servers", response_model=ServerOut, status_code=201, dependencies=[Admin])
async def create_server(body: ServerCreate, service: Tools) -> ServerOut:
    """Register a server, then connect and list its tools. New tools start switched off."""
    return await service.create_server(body)


@router.get("/servers/{name}", response_model=ServerOut, dependencies=[Admin])
async def get_server(name: str, service: Tools) -> ServerOut:
    return await service.get_server(name)


@router.patch("/servers/{name}", response_model=ServerOut, dependencies=[Admin])
async def update_server(name: str, body: ServerUpdate, service: Tools) -> ServerOut:
    return await service.update_server(name, body)


@router.delete("/servers/{name}", status_code=204, dependencies=[Admin])
async def delete_server(name: str, service: Tools) -> Response:
    await service.delete_server(name)
    return Response(status_code=204)


@router.post("/servers/{name}/refresh", response_model=ServerOut, dependencies=[Admin])
async def refresh_server(name: str, service: Tools) -> ServerOut:
    """Reconnect and re-read the server's tool list (new tools start switched off)."""
    return await service.refresh_server(name)


@router.patch("/servers/{name}/tools/{tool}", response_model=ToolOut, dependencies=[Admin])
async def update_tool(name: str, tool: str, body: ToolUpdate, service: Tools) -> ToolOut:
    """Switch a tool on or off. Only switched-on tools are ever offered to a model."""
    return await service.set_tool_enabled(name, tool, body.enabled)


@router.post("/servers/{name}/tools/{tool}/call", response_model=ToolCallOutcome, dependencies=[Admin])
async def call_tool(name: str, tool: str, body: ToolCallRequest, service: Tools) -> ToolCallOutcome:
    """Run a tool directly with the given arguments, to check it works (also when switched off)."""
    return await service.call_tool(name, tool, body.arguments)
