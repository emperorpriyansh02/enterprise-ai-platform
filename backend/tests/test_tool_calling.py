"""Tool calling in every provider adapter: offering tools, reading tool calls (blocking and streaming),
and sending tool results back in each vendor's format."""

import json

import httpx
import httpx2

from app.llm.base import LLMRequest, ToolResult, ToolSpec, ToolTurn
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.providers.mock_provider import MockProvider
from app.llm.providers.ollama_provider import OllamaProvider
from app.llm.providers.openai_provider import OpenAIProvider
from app.schemas.chat import Message
from tests.conftest import Recorder, sse

TOOL = ToolSpec(
    name="demo__get_time",
    description="Current time in a time zone.",
    input_schema={"type": "object", "properties": {"timezone": {"type": "string"}}, "required": ["timezone"]},
)
MESSAGES = [Message(role="user", content="What time is it in Tokyo?")]
STREAM_HEADERS = {"content-type": "text/event-stream"}


def request(model: str) -> LLMRequest:
    return LLMRequest(model=model, messages=MESSAGES, tools=[TOOL])


def follow_up(first: LLMRequest, result) -> LLMRequest:
    """The request after running the tools the model asked for."""
    results = [ToolResult(call_id=c.id, name=c.name, content="12:00 JST") for c in result.tool_calls]
    return first.with_turn(
        ToolTurn(
            text=result.content,
            calls=result.tool_calls,
            provider_state=result.provider_state,
            results=results,
        )
    )


# --- Anthropic ---

CLAUDE_TOOL_USE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig-1"},
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "toolu_1", "name": "demo__get_time", "input": {"timezone": "Asia/Tokyo"}},
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 20, "output_tokens": 10},
}


def claude(recorder: Recorder) -> AnthropicProvider:
    return AnthropicProvider(
        api_key="k", models=["claude-opus-5-5"], timeout_s=10, max_retries=0, http_client=recorder.client()
    )


async def test_anthropic_tool_round_trip():
    final = CLAUDE_TOOL_USE | {"content": [{"type": "text", "text": "It's noon."}], "stop_reason": "end_turn"}
    replies = iter([CLAUDE_TOOL_USE, final])
    recorder = Recorder(lambda _: httpx2.Response(200, json=next(replies)))
    provider = claude(recorder)

    first = request("claude-opus-5-5")
    result = await provider.complete(first)

    assert result.finish_reason == "tool_calls"
    assert result.content == "Let me check."
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ("toolu_1", "demo__get_time", {"timezone": "Asia/Tokyo"})
    ]
    sent_tools = recorder.last_json["tools"]
    assert sent_tools == [
        {"name": TOOL.name, "description": TOOL.description, "input_schema": TOOL.input_schema}
    ]
    assert "tool_choice" not in recorder.last_json  # stays automatic

    second = await provider.complete(follow_up(first, result))

    assert second.content == "It's noon." and not second.tool_calls
    messages = recorder.last_json["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    replayed = messages[1]["content"]
    assert [b["type"] for b in replayed] == ["thinking", "text", "tool_use"]  # unchanged, thinking kept
    assert replayed[0]["signature"] == "sig-1"
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "12:00 JST", "is_error": False}
    ]


async def test_anthropic_no_tool_calls_when_cut_off():
    cut = CLAUDE_TOOL_USE | {"stop_reason": "max_tokens"}
    result = await claude(Recorder(lambda _: httpx2.Response(200, json=cut))).complete(
        request("claude-opus-5-5")
    )
    assert result.tool_calls == [] and result.finish_reason == "length"


async def test_anthropic_stream_collects_tool_call():
    start = CLAUDE_TOOL_USE | {"content": [], "stop_reason": None}
    events = [
        {"type": "message_start", "message": start},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking."}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_9", "name": "demo__get_time", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"timezone": "Asia/'},
        },
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": 'Tokyo"}'}},  # noqa: E501
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 15}},  # noqa: E501
        {"type": "message_stop"},
    ]  # fmt: skip
    recorder = Recorder(lambda _: httpx2.Response(200, content=sse(events), headers=STREAM_HEADERS))

    chunks = [c async for c in claude(recorder).stream(request("claude-opus-5-5"))]

    assert [c.delta for c in chunks if not c.done] == ["Checking."]
    final = chunks[-1]
    assert final.finish_reason == "tool_calls"
    assert [(c.id, c.arguments) for c in final.tool_calls] == [("toolu_9", {"timezone": "Asia/Tokyo"})]
    assert final.provider_state is not None
    assert recorder.last_json["tools"][0]["eager_input_streaming"] is True


# --- OpenAI ---


def openai(recorder: Recorder) -> OpenAIProvider:
    return OpenAIProvider(
        api_key="k", models=["gpt-5"], timeout_s=10, max_retries=0, http_client=recorder.client()
    )


def completion(message: dict, finish: str) -> dict:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-5",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "refusal": None} | message, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
    }


async def test_openai_tool_round_trip():
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "demo__get_time", "arguments": '{"timezone":"Asia/Tokyo"}'},
    }
    replies = iter(
        [
            completion({"content": None, "tool_calls": [call]}, "tool_calls"),
            completion({"content": "Noon."}, "stop"),
        ]
    )
    recorder = Recorder(lambda _: httpx2.Response(200, json=next(replies)))
    provider = openai(recorder)

    first = request("gpt-5")
    result = await provider.complete(first)

    assert result.finish_reason == "tool_calls"
    assert [(c.id, c.arguments) for c in result.tool_calls] == [("call_1", {"timezone": "Asia/Tokyo"})]
    assert recorder.last_json["tools"] == [
        {
            "type": "function",
            "function": {"name": TOOL.name, "description": TOOL.description, "parameters": TOOL.input_schema},
        }
    ]

    second = await provider.complete(follow_up(first, result))

    assert second.content == "Noon."
    messages = recorder.last_json["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[1]["tool_calls"][0]["id"] == "call_1"
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {"timezone": "Asia/Tokyo"}
    assert messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": "12:00 JST"}  # fmt: skip


async def test_openai_stream_assembles_tool_call_pieces():
    def chunk(delta: dict | None, finish=None, usage=None) -> dict:
        choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
        return {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-5",
            "choices": choices,
            "usage": usage,
            "type": "x",
        }

    events = [
        chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_7", "type": "function", "function": {"name": "demo__get_time", "arguments": ""}}]}),  # noqa: E501
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"timezone":'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"UTC"}'}}]}),
        chunk({}, finish="stop"),  # some compatible servers say "stop" even with tool calls
        chunk(None, usage={"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}),
    ]  # fmt: skip
    body = sse(events, named=False, done_marker=True)
    recorder = Recorder(lambda _: httpx2.Response(200, content=body, headers=STREAM_HEADERS))

    chunks = [c async for c in openai(recorder).stream(request("gpt-5"))]

    final = chunks[-1]
    assert final.done and final.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in final.tool_calls] == [
        ("call_7", "demo__get_time", {"timezone": "UTC"})
    ]
    assert final.provider_state["tool_calls"][0]["id"] == "call_7"


# --- Gemini ---


def gemini(recorder: Recorder) -> GeminiProvider:
    return GeminiProvider(
        api_key="k",
        models=["gemini-flash-latest"],
        timeout_s=10,
        max_retries=0,
        http_client=recorder.client(),
    )


def gemini_call(call_id: str | None = None) -> dict:
    call = {"name": "demo__get_time", "args": {"timezone": "Asia/Tokyo"}} | (
        {"id": call_id} if call_id else {}
    )
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"functionCall": call, "thoughtSignature": "c2lnLTE="}]}, "finishReason": "STOP"}  # noqa: E501
        ],
        "modelVersion": "gemini-flash-latest",
    }  # fmt: skip


async def test_gemini_tool_round_trip():
    answer = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "Noon."}]}, "finishReason": "STOP"}]
    }
    replies = iter([gemini_call(), answer])
    recorder = Recorder(lambda _: httpx2.Response(200, json=next(replies)))
    provider = gemini(recorder)

    first = request("gemini-flash-latest")
    result = await provider.complete(first)

    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].name == "demo__get_time"
    assert result.tool_calls[0].arguments == {"timezone": "Asia/Tokyo"}
    declared = recorder.last_json["tools"][0]["functionDeclarations"][0]
    schema = declared.get("parametersJsonSchema", declared.get("parameters_json_schema"))  # API takes both
    assert declared["name"] == TOOL.name and schema == TOOL.input_schema

    second = await provider.complete(follow_up(first, result))

    assert second.content == "Noon."
    contents = recorder.last_json["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert contents[1]["parts"][0]["thoughtSignature"] == "c2lnLTE="  # replayed, signature kept
    response = contents[2]["parts"][0]["functionResponse"]
    assert response["name"] == "demo__get_time"
    assert response["response"] == {"result": "12:00 JST"}
    assert "id" not in response  # Gemini gave no id; the one we made up isn't sent back


async def test_gemini_stream_returns_tool_call_with_id():
    body = sse([gemini_call("fc-1") | {"type": "x"}], named=False)
    recorder = Recorder(lambda _: httpx2.Response(200, content=body, headers=STREAM_HEADERS))

    chunks = [c async for c in gemini(recorder).stream(request("gemini-flash-latest"))]

    final = chunks[-1]
    assert final.finish_reason == "tool_calls"
    assert final.tool_calls[0].id == "fc-1"
    assert final.provider_state.parts[0].function_call.name == "demo__get_time"


# --- Ollama ---


def ollama(handler) -> tuple[OllamaProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client = httpx.AsyncClient(base_url="http://ollama:11434", transport=httpx.MockTransport(record))
    return OllamaProvider(
        base_url="http://ollama:11434", models=["llama3.2"], timeout_s=10, http_client=client
    ), seen


async def test_ollama_tool_round_trip():
    call_reply = {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "demo__get_time", "arguments": '{"timezone": "UTC"}'}}]},  # noqa: E501
        "done": True,
        "done_reason": "stop",
    }  # fmt: skip
    answer = {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": "Noon."},
        "done": True,
        "done_reason": "stop",
    }
    replies = iter([call_reply, answer])
    provider, seen = ollama(lambda _: httpx.Response(200, json=next(replies)))

    first = request("llama3.2")
    result = await provider.complete(first)

    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].arguments == {"timezone": "UTC"}  # JSON text arguments are parsed
    assert json.loads(seen[-1].content)["tools"][0]["function"]["name"] == TOOL.name

    second = await provider.complete(follow_up(first, result))

    assert second.content == "Noon."
    messages = json.loads(seen[-1].content)["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[1]["tool_calls"] == [
        {"function": {"name": "demo__get_time", "arguments": {"timezone": "UTC"}}}
    ]
    assert messages[2] == {"role": "tool", "tool_name": "demo__get_time", "content": "12:00 JST"}


async def test_ollama_stream_collects_tool_calls():
    lines = [
        {"model": "llama3.2", "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "demo__get_time", "arguments": {"timezone": "UTC"}}}]}, "done": False},  # noqa: E501
        {"model": "llama3.2", "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "eval_count": 3},  # noqa: E501
    ]  # fmt: skip
    body = "\n".join(json.dumps(line) for line in lines) + "\n"
    provider, _ = ollama(lambda _: httpx.Response(200, content=body))

    chunks = [c async for c in provider.stream(request("llama3.2"))]

    final = chunks[-1]
    assert final.finish_reason == "tool_calls"
    assert final.tool_calls[0].id.startswith("ollama_")
    assert final.provider_state["tool_calls"][0]["function"]["name"] == "demo__get_time"


# --- Mock ---


async def test_mock_tool_command_matches_short_name_and_reports_results():
    provider = MockProvider()
    first = LLMRequest(
        model="echo",
        messages=[Message(role="user", content='/tool get_time {"timezone": "UTC"}')],
        tools=[TOOL],
    )

    result = await provider.complete(first)

    assert result.finish_reason == "tool_calls"
    assert (result.tool_calls[0].name, result.tool_calls[0].arguments) == (
        "demo__get_time",
        {"timezone": "UTC"},
    )
    second = await provider.complete(follow_up(first, result))
    assert second.content == "Tool results:\n- demo__get_time: 12:00 JST"


async def test_mock_ignores_tool_command_without_tools():
    result = await MockProvider().complete(
        LLMRequest(model="echo", messages=[Message(role="user", content="/tool get_time")])
    )
    assert result.tool_calls == [] and result.content == "Echo: /tool get_time"
