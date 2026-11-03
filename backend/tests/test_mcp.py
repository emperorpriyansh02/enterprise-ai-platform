"""MCP connections (real stdio and HTTP servers), result formatting, tool names, and secret encryption."""

import asyncio
import socket
import subprocess
import sys
import time

import pytest
from mcp_types import CallToolResult, EmbeddedResource, ImageContent, TextContent, TextResourceContents

from app.core.errors import MCPConnectionError, MCPStdioDisabledError, SecretKeyMissingError
from app.llm.base import ToolCall, ToolSpec
from app.mcp.manager import MCPManager, ServerTarget
from app.mcp.secrets import decrypt_secrets, encrypt_secrets
from app.mcp.toolbox import Toolbox, ToolEntry, format_result, model_tool_name, tool_schema
from tests.conftest import make_settings

DEMO = ServerTarget(
    name="demo", transport="stdio", command=sys.executable, args=["-m", "app.mcp.demo_server"]
)
TEST = ServerTarget(
    name="test", transport="stdio", command=sys.executable, args=["-m", "tests.mcp_test_server"]
)


@pytest.fixture
async def manager():
    mcp = MCPManager(make_settings(mcp_call_timeout_s=5, mcp_connect_timeout_s=15))
    yield mcp
    await mcp.aclose()


def text(result: CallToolResult) -> str:
    return format_result(result, 10_000)


# --- Manager over stdio ---


async def test_lists_tools_with_annotations(manager: MCPManager):
    tools = {t.name: t for t in await manager.list_tools(DEMO)}

    assert set(tools) == {"get_time", "calculate", "lookup_order", "cancel_order"}
    assert tools["get_time"].annotations.read_only_hint is True
    assert tools["cancel_order"].annotations.destructive_hint is True
    assert tools["lookup_order"].input_schema["required"] == ["order_id"]
    assert manager.is_connected("demo")


async def test_calls_tools_and_reports_tool_errors(manager: MCPManager):
    ok = await manager.call_tool(DEMO, "calculate", {"expression": "(2 + 3) * 4"}, retry_safe=True)
    assert not ok.is_error and text(ok) == "(2 + 3) * 4 = 20"

    failed = await manager.call_tool(DEMO, "lookup_order", {"order_id": "nope"}, retry_safe=True)
    assert failed.is_error and "No order 'nope'" in text(failed)

    unknown = await manager.call_tool(DEMO, "no_such_tool", {}, retry_safe=True)
    assert unknown.is_error and "no_such_tool" in text(unknown)


async def test_parallel_calls_share_one_connection(manager: MCPManager):
    results = await asyncio.gather(*(manager.call_tool(TEST, "pid", {}, retry_safe=True) for _ in range(5)))
    assert len({text(r) for r in results}) == 1


async def test_reconnects_after_the_server_dies(manager: MCPManager):
    before = text(await manager.call_tool(TEST, "pid", {}, retry_safe=True))

    with pytest.raises(MCPConnectionError, match="may or may not have run"):
        await manager.call_tool(TEST, "crash", {}, retry_safe=False)  # destructive: no automatic retry

    after = text(await manager.call_tool(TEST, "pid", {}, retry_safe=True))
    assert after != before  # a fresh server process


async def test_call_timeout(manager: MCPManager):
    manager._settings = make_settings(mcp_call_timeout_s=0.5)
    started = time.perf_counter()
    with pytest.raises(MCPConnectionError, match=r"didn.t finish within 0\.5s"):
        await manager.call_tool(TEST, "slow", {"seconds": 5}, retry_safe=True)
    assert time.perf_counter() - started < 3


async def test_changed_settings_open_a_new_connection(manager: MCPManager):
    first = text(await manager.call_tool(TEST, "env", {"name": "EAP_FLAG"}, retry_safe=True))
    changed = ServerTarget(
        name="test", transport="stdio", command=TEST.command, args=TEST.args, env={"EAP_FLAG": "on"}
    )
    second = text(await manager.call_tool(changed, "env", {"name": "EAP_FLAG"}, retry_safe=True))
    assert (first, second) == ("(unset)", "on")


@pytest.mark.parametrize(
    ("target", "message"),
    [
        (ServerTarget(name="bad", transport="stdio", command="/nonexistent/binary"), "No such file"),
        (
            ServerTarget(name="bad", transport="stdio", command=sys.executable, args=["-c", "pass"]),
            "Connection closed",
        ),
        (ServerTarget(name="bad", transport="http", url="http://127.0.0.1:1/mcp"), "ConnectError"),
    ],
)
async def test_connection_failures_are_clear(manager: MCPManager, target: ServerTarget, message: str):
    with pytest.raises(MCPConnectionError, match=message):
        await manager.list_tools(target)
    assert not manager.is_connected("bad")


async def test_stdio_can_be_switched_off():
    manager = MCPManager(make_settings(mcp_allow_stdio=False))
    with pytest.raises(MCPStdioDisabledError):
        await manager.list_tools(DEMO)


async def test_close_stops_the_server_process(manager: MCPManager):
    pid = int(text(await manager.call_tool(TEST, "pid", {}, retry_safe=True)))
    await manager.aclose()
    for _ in range(50):
        if not _process_exists(pid):
            break
        await asyncio.sleep(0.1)
    assert not _process_exists(pid)


def _process_exists(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"  # a zombie has exited
    except FileNotFoundError:
        return False


# --- Manager over HTTP ---


@pytest.fixture(scope="module")
def http_demo_url():
    """The demo server running over streamable HTTP in a subprocess."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "app.mcp.demo_server", "--http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        process.wait(timeout=10)


async def test_http_server(manager: MCPManager, http_demo_url: str):
    target = ServerTarget(name="demo-http", transport="http", url=http_demo_url, headers={"X-Test": "1"})

    names = {t.name for t in await manager.list_tools(target)}
    result = await manager.call_tool(target, "get_time", {"timezone": "UTC"}, retry_safe=True)

    assert "get_time" in names
    assert not result.is_error and "UTC" in text(result)


# --- Toolbox ---


class FakeManager:
    """Stands in for MCPManager: answers every call with a fixed result or error."""

    def __init__(self, result: CallToolResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, dict, bool]] = []

    async def call_tool(self, target, tool, arguments, *, retry_safe):
        self.calls.append((tool, arguments, retry_safe))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def entry(tool: str, *, destructive: bool = False) -> ToolEntry:
    return ToolEntry(
        spec=ToolSpec(name=f"demo__{tool}", description="", input_schema={"type": "object"}),
        server="demo",
        tool=tool,
        destructive=destructive,
        target=DEMO,
    )


async def test_toolbox_runs_calls_and_traces_them():
    fake = FakeManager(CallToolResult(content=[TextContent(text="42")]))
    box = Toolbox(fake, [entry("calculate"), entry("cancel_order", destructive=True)], max_result_chars=100)

    outcomes = await box.run(
        [
            ToolCall(id="c1", name="demo__calculate", arguments={"expression": "6*7"}),
            ToolCall(id="c2", name="demo__cancel_order", arguments={"order_id": "A-1"}),
            ToolCall(id="c3", name="demo__made_up", arguments={}),
        ]
    )

    results = [r for r, _ in outcomes]
    traces = [t for _, t in outcomes]
    assert [(r.call_id, r.content, r.is_error) for r in results[:2]] == [
        ("c1", "42", False),
        ("c2", "42", False),
    ]
    assert results[2].is_error and "Unknown tool" in results[2].content
    assert [(t.server, t.tool, t.result) for t in traces[:2]] == [
        ("demo", "calculate", "42"),
        ("demo", "cancel_order", "42"),
    ]
    assert all(t.duration_ms is not None for t in traces)
    assert fake.calls == [
        ("calculate", {"expression": "6*7"}, True),
        ("cancel_order", {"order_id": "A-1"}, False),
    ]


async def test_toolbox_turns_connection_errors_into_tool_errors():
    box = Toolbox(FakeManager(MCPConnectionError("server down")), [entry("calculate")], max_result_chars=100)
    [(result, trace)] = await box.run([ToolCall(id="c1", name="demo__calculate", arguments={})])
    assert (result.content, result.is_error, trace.is_error) == ("server down", True, True)


def test_format_result_handles_every_content_type():
    result = CallToolResult(
        content=[
            TextContent(text="hello"),
            ImageContent(data="aGk=", mime_type="image/png"),
            EmbeddedResource(resource=TextResourceContents(uri="file:///a.txt", text="file text")),
        ]
    )
    assert format_result(result, 1000) == "hello\n[image: image/png]\nfile text"
    assert format_result(CallToolResult(content=[], structured_content={"a": 1}), 1000) == '{"a": 1}'
    assert format_result(CallToolResult(content=[]), 1000) == "(the tool returned no output)"
    long = format_result(CallToolResult(content=[TextContent(text="x" * 600)]), 500)
    assert long.startswith("x" * 500) and long.endswith("[... 100 more characters cut]")


def test_model_tool_names_are_safe_and_unique():
    assert model_tool_name("demo", "get_time", set()) == "demo__get_time"
    assert model_tool_name("files", "read.file", set()) == "files__read_file"
    assert model_tool_name("1password", "get", set()).startswith("t_1password__")
    long = model_tool_name("server", "t" * 100, set())
    assert len(long) == 64
    taken = {"a__b_c"}
    assert model_tool_name("a", "b.c", taken) != "a__b_c"


def test_tool_schema_defaults_to_an_object():
    assert tool_schema(None) == {"type": "object", "properties": {}}
    assert tool_schema({"type": "string"}) == {"type": "object", "properties": {}}
    schema = {"type": "object", "properties": {"x": {"type": "number"}}}
    assert tool_schema(schema) is schema


# --- Secrets ---


def test_secrets_round_trip_and_need_a_key():
    token = encrypt_secrets({"API_TOKEN": "s3cret"}, "key-1")
    assert token and "s3cret" not in token
    assert decrypt_secrets(token, "key-1") == {"API_TOKEN": "s3cret"}
    assert encrypt_secrets({}, None) is None and decrypt_secrets(None, None) == {}
    with pytest.raises(SecretKeyMissingError):
        encrypt_secrets({"A": "b"}, None)
    with pytest.raises(MCPConnectionError, match="SECRET_KEY changed"):
        decrypt_secrets(token, "another-key")
