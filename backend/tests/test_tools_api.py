"""MCP tools end to end: registering servers, the admin allowlist, and the chat tool loop."""

import json
import sys
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from mcp_types import Tool, ToolAnnotations
from sqlalchemy import select

from app.db.session import Database
from app.llm.base import LLMProvider, LLMRequest, LLMResult, StreamChunk, ToolCall
from app.llm.registry import ProviderRegistry
from app.main import create_app
from app.mcp.manager import MCPManager
from app.mcp.models import MCPServerRow
from app.mcp.schemas import ServerCreate
from app.mcp.service import MCPService
from app.schemas.chat import Usage
from tests.conftest import AppClient, make_settings

DEMO = {
    "name": "demo",
    "transport": "stdio",
    "command": sys.executable,
    "args": ["-m", "app.mcp.demo_server"],
}
TEST_SERVER = {
    "name": "test",
    "transport": "stdio",
    "command": sys.executable,
    "args": ["-m", "tests.mcp_test_server"],
}
SETTINGS = {"secret_key": "test-secret-key"}


def chat_body(text: str, servers: list[str] | None = None) -> dict:
    body = {"model": "mock/echo", "messages": [{"role": "user", "content": text}]}
    if servers is not None:
        body["tools"] = {"servers": servers}
    return body


async def enable(api, server: str, tool: str, on: bool = True) -> None:
    response = await api.patch(f"/v1/tools/servers/{server}/tools/{tool}", json={"enabled": on})
    assert response.status_code == 200, response.text


async def test_register_server_lists_tools_switched_off(api_factory):
    async with api_factory(**SETTINGS) as api:
        response = await api.post("/v1/tools/servers", json=DEMO)

        assert response.status_code == 201, response.text
        server = response.json()
        assert server["last_error"] is None and server["last_connected_at"]
        tools = {t["name"]: t for t in server["tools"]}
        assert set(tools) == {"get_time", "calculate", "lookup_order", "cancel_order"}
        assert not any(t["enabled"] for t in tools.values())  # admin allowlist: all off at first
        assert tools["get_time"]["read_only"] and not tools["get_time"]["destructive"]
        assert tools["cancel_order"]["destructive"]
        assert (await api.get("/v1/tools")).json() == {"data": []}  # nothing usable yet

        duplicate = await api.post("/v1/tools/servers", json=DEMO)
        assert duplicate.status_code == 409


async def test_chat_runs_an_enabled_tool(api_factory):
    async with api_factory(**SETTINGS) as api:
        await api.post("/v1/tools/servers", json=DEMO)
        await enable(api, "demo", "calculate")

        available = (await api.get("/v1/tools")).json()["data"]
        assert [(s["name"], [t["name"] for t in s["tools"]]) for s in available] == [("demo", ["calculate"])]

        response = await api.post(
            "/v1/chat", json=chat_body('/tool calculate {"expression": "6*7"}', ["demo"])
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["content"] == "Tool results:\n- demo__calculate: 6*7 = 42"
        assert body["finish_reason"] == "stop"
        [trace] = body["tool_calls"]
        assert (trace["server"], trace["tool"], trace["arguments"]) == (
            "demo",
            "calculate",
            {"expression": "6*7"},
        )
        assert trace["result"] == "6*7 = 42" and trace["is_error"] is False
        assert body["usage"]["input_tokens"] > 0  # summed over both model calls


async def test_disabled_and_destructive_tools_are_not_offered(api_factory):
    async with api_factory(**SETTINGS) as api:
        await api.post("/v1/tools/servers", json=DEMO)
        await enable(api, "demo", "calculate")

        # cancel_order is destructive and still switched off, so the model never sees it
        body = (
            await api.post("/v1/chat", json=chat_body('/tool cancel_order {"order_id": "A-1001"}', ["demo"]))
        ).json()

        assert body["tool_calls"] == []
        assert body["content"].startswith("Echo: /tool cancel_order")


async def test_tool_errors_are_returned_to_the_model(api_factory):
    async with api_factory(**SETTINGS) as api:
        await api.post("/v1/tools/servers", json=DEMO)
        await enable(api, "demo", "lookup_order")

        body = (
            await api.post("/v1/chat", json=chat_body('/tool lookup_order {"order_id": "zzz"}', ["demo"]))
        ).json()

        [trace] = body["tool_calls"]
        assert trace["is_error"] is True and "No order 'zzz'" in trace["result"]
        assert "No order 'zzz'" in body["content"]


async def test_stream_reports_tool_calls_and_results(api_factory):
    async with api_factory(**SETTINGS) as api:
        await api.post("/v1/tools/servers", json=DEMO)
        await enable(api, "demo", "get_time")

        async with api.stream(
            "POST", "/v1/chat/stream", json=chat_body('/tool get_time {"timezone": "UTC"}', ["demo"])
        ) as r:
            assert r.status_code == 200
            events = [json.loads(line[5:]) async for line in r.aiter_lines() if line.startswith("data:")]

        types = [e["type"] for e in events]
        assert types[:3] == ["start", "tool_call", "tool_result"] and types[-1] == "done"
        assert events[1]["tool"]["tool"] == "get_time" and "result" not in events[1]["tool"]
        assert "UTC" in events[2]["tool"]["result"]
        text = "".join(e["delta"] for e in events if e["type"] == "delta")
        assert text.startswith("Tool results:\n- demo__get_time: ")


async def test_chat_names_unknown_or_disabled_server(api_factory):
    async with api_factory(**SETTINGS) as api:
        await api.post("/v1/tools/servers", json=DEMO)

        missing = await api.post("/v1/chat", json=chat_body("hi", ["nope"]))
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "mcp_server_not_found"

        await api.patch("/v1/tools/servers/demo", json={"enabled": False})
        disabled = await api.post("/v1/chat", json=chat_body("hi", ["demo"]))
        assert disabled.status_code == 404


def test_tools_need_a_database(client: TestClient):
    response = client.post("/v1/chat", json=chat_body("hi", ["demo"]))
    assert response.status_code == 503 and response.json()["error"]["code"] == "tools_unavailable"
    assert client.get("/v1/tools").status_code == 503


async def test_secrets_are_encrypted_hidden_and_reach_the_server(api_factory, database: Database):
    async with api_factory(**SETTINGS) as api:
        response = await api.post("/v1/tools/servers", json=TEST_SERVER | {"env": {"API_TOKEN": "s3cret"}})

        assert response.status_code == 201, response.text
        assert response.json()["env_names"] == ["API_TOKEN"]
        assert "s3cret" not in response.text
        assert "s3cret" not in (await api.get("/v1/tools/servers")).text
        async with database.session() as session:
            row = await session.scalar(select(MCPServerRow).where(MCPServerRow.name == "test"))
            assert row.env_secret and "s3cret" not in row.env_secret

        tried = await api.post(
            "/v1/tools/servers/test/tools/env/call", json={"arguments": {"name": "API_TOKEN"}}
        )
        assert tried.json()["content"] == "s3cret"  # the stdio process received the decrypted value


async def test_secret_needs_secret_key(api_factory):
    async with api_factory() as api:
        response = await api.post("/v1/tools/servers", json=TEST_SERVER | {"env": {"API_TOKEN": "x"}})
        assert response.status_code == 400 and response.json()["error"]["code"] == "secret_key_missing"


async def test_stdio_servers_can_be_switched_off(api_factory):
    async with api_factory(mcp_allow_stdio=False) as api:
        response = await api.post("/v1/tools/servers", json=DEMO)
        assert response.status_code == 403 and response.json()["error"]["code"] == "mcp_stdio_disabled"


async def test_unreachable_server_is_saved_with_its_error(api_factory):
    async with api_factory() as api:
        body = {"name": "remote", "transport": "http", "url": "http://127.0.0.1:1/mcp"}
        created = await api.post("/v1/tools/servers", json=body)

        assert created.status_code == 201
        assert "ConnectError" in created.json()["last_error"] and created.json()["tools"] == []
        refreshed = await api.post("/v1/tools/servers/remote/refresh")
        assert refreshed.status_code == 502 and refreshed.json()["error"]["code"] == "mcp_connection_failed"


async def test_invalid_server_definitions_are_rejected(api_factory):
    async with api_factory() as api:
        no_command = await api.post("/v1/tools/servers", json={"name": "x1", "transport": "stdio"})
        bad_url = await api.post(
            "/v1/tools/servers", json={"name": "x2", "transport": "http", "url": "ftp://x"}
        )
        bad_name = await api.post("/v1/tools/servers", json=DEMO | {"name": "Bad Name"})
        assert {no_command.status_code, bad_url.status_code, bad_name.status_code} == {422}


async def test_admin_key_protects_server_management(api_factory):
    async with api_factory(admin_api_key="admin-key") as api:
        assert (await api.post("/v1/tools/servers", json=DEMO)).status_code == 401
        assert (await api.get("/v1/tools/servers")).status_code == 401
        assert (await api.get("/v1/tools")).status_code == 200  # what chats can use stays readable
        allowed = await api.post("/v1/tools/servers", json=DEMO, headers={"X-Admin-Key": "admin-key"})
        assert allowed.status_code == 201


async def test_demo_preset_can_be_registered_as_is(api_factory):
    async with api_factory() as api:
        [preset] = (await api.get("/v1/tools/presets")).json()["data"]
        assert preset["name"] == "demo" and preset["command"] == sys.executable

        created = await api.post("/v1/tools/servers", json=preset)
        assert created.status_code == 201 and len(created.json()["tools"]) == 4


async def test_try_a_tool_and_delete_a_server(api_factory):
    async with api_factory() as api:
        await api.post("/v1/tools/servers", json=DEMO)

        tried = await api.post(
            "/v1/tools/servers/demo/tools/calculate/call", json={"arguments": {"expression": "2+2"}}
        )
        assert tried.status_code == 200
        assert (
            tried.json()["content"] == "2+2 = 4" and tried.json()["is_error"] is False
        )  # works while switched off
        assert (await api.post("/v1/tools/servers/demo/tools/nope/call", json={})).status_code == 404

        assert (await api.delete("/v1/tools/servers/demo")).status_code == 204
        assert (await api.get("/v1/tools/servers/demo")).status_code == 404


# --- Service rules, with a fake connection manager ---


class ListingManager:
    """Returns whatever tool list the test sets; no real server."""

    def __init__(self) -> None:
        self.tools: list[Tool] = []

    async def list_tools(self, target) -> list[Tool]:
        return self.tools

    async def disconnect(self, name: str) -> None:
        pass


def tool(name: str, **hints) -> Tool:
    return Tool(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object"},
        annotations=ToolAnnotations(**hints) if hints else None,
    )


async def test_refresh_keeps_choices_and_rechecks_tools(database: Database):
    manager = ListingManager()
    manager.tools = [
        tool("search", read_only_hint=True),
        tool("update", read_only_hint=False, destructive_hint=False),
        tool("old"),
    ]
    async with database.session() as session:
        service = MCPService(session, manager, make_settings())  # type: ignore[arg-type]
        created = await service.create_server(
            ServerCreate(name="svc", transport="http", url="http://svc/mcp")
        )
        flags = {t.name: (t.read_only, t.destructive) for t in created.tools}
        assert flags == {
            "search": (True, False),
            "update": (False, False),
            "old": (False, True),
        }  # no hints = destructive
        await service.set_tool_enabled("svc", "search", True)
        await service.set_tool_enabled("svc", "update", True)

        # Next refresh: `update` now says it's destructive, `old` is gone, `new` appeared.
        manager.tools = [
            tool("search", read_only_hint=True),
            tool("update", destructive_hint=True),
            tool("new", read_only_hint=True),
        ]
        refreshed = await service.refresh_server("svc")

    states = {t.name: (t.enabled, t.destructive) for t in refreshed.tools}
    assert states == {"search": (True, False), "update": (False, True), "new": (False, False)}


# --- The round limit ---


class AlwaysCallsTools(LLMProvider):
    """A model that asks for a tool on every turn."""

    name = "loop"

    def __init__(self) -> None:
        super().__init__(["loop"])
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResult:
        self.calls += 1
        call = ToolCall(id=f"c{self.calls}", name=request.tools[0].name, arguments={"expression": "1+1"})
        return LLMResult(
            content="", finish_reason="tool_calls", usage=Usage(input_tokens=1, output_tokens=1),
            model="loop", tool_calls=[call], provider_state={"calls": [call]},
        )  # fmt: skip

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        result = await self.complete(request)
        yield StreamChunk(
            done=True, finish_reason="tool_calls", usage=result.usage, model="loop",
            tool_calls=result.tool_calls, provider_state=result.provider_state,
        )  # fmt: skip


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
async def test_tool_rounds_are_limited(database: Database, path: str):
    provider = AlwaysCallsTools()
    app = create_app(
        make_settings(mcp_max_tool_rounds=2),
        registry=ProviderRegistry([provider], "loop/loop"),
        database=database,
    )
    async with AppClient(app) as api:
        await api.post("/v1/tools/servers", json=DEMO)
        await enable(api, "demo", "calculate")

        body = chat_body("loop", ["demo"]) | {"model": "loop/loop"}
        if path == "/v1/chat":
            result = (await api.post(path, json=body)).json()
            assert result["finish_reason"] == "tool_calls" and len(result["tool_calls"]) == 2
            assert result["usage"] == {"input_tokens": 3, "output_tokens": 3}
        else:
            async with api.stream("POST", path, json=body) as r:
                events = [json.loads(line[5:]) async for line in r.aiter_lines() if line.startswith("data:")]
            assert [e["type"] for e in events].count("tool_result") == 2
            assert events[-1]["type"] == "done" and events[-1]["finish_reason"] == "tool_calls"
    assert provider.calls == 3  # 2 tool rounds, then one more model call that isn't followed up


def test_manager_is_created_and_closed_with_the_app():
    app = create_app(make_settings())
    with TestClient(app):
        assert isinstance(app.state.mcp, MCPManager)
