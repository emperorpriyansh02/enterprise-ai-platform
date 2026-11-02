import httpx2
import pytest

from app.core.errors import ProviderAuthError, ProviderRateLimitError
from app.llm.base import LLMRequest
from app.llm.providers.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from tests.conftest import Recorder, sse

MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Hi there"},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 12, "output_tokens": 4},
}


def make_provider(recorder: Recorder, **kwargs) -> AnthropicProvider:
    return AnthropicProvider(
        api_key="test-key",
        models=["claude-opus-5-5", "claude-haiku-5-5"],
        timeout_s=10,
        max_retries=0,
        effort="medium",
        fallback_models=["claude-opus-5-5"],
        http_client=recorder.client(),
        **kwargs,
    )


async def test_complete_maps_request_and_response(llm_request: LLMRequest):
    recorder = Recorder(lambda _: httpx2.Response(200, json=MESSAGE))
    provider = make_provider(recorder)
    request = LLMRequest(
        model="claude-opus-5-5", system="Be brief.", messages=llm_request.messages, temperature=0.7
    )

    result = await provider.complete(request)

    assert result.content == "Hi there"  # thinking block is not returned
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 12 and result.usage.output_tokens == 4
    body = recorder.last_json
    assert body["system"] == "Be brief."
    assert body["messages"] == [{"role": "user", "content": "Hello"}]
    assert body["max_tokens"] == 16_000
    assert body["output_config"] == {"effort": "medium"}
    assert "temperature" not in body  # current Claude models reject it
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in recorder.requests[-1].headers["anthropic-beta"]


async def test_fallback_only_for_supported_models(llm_request: LLMRequest):
    recorder = Recorder(lambda _: httpx2.Response(200, json=MESSAGE | {"model": "claude-haiku-5-5"}))
    provider = make_provider(recorder)

    await provider.complete(LLMRequest(model="claude-haiku-5-5", messages=llm_request.messages))

    assert "fallbacks" not in recorder.last_json
    assert "anthropic-beta" not in recorder.requests[-1].headers


async def test_refusal_is_reported(llm_request: LLMRequest):
    refusal = MESSAGE | {"content": [], "stop_reason": "refusal"}
    provider = make_provider(Recorder(lambda _: httpx2.Response(200, json=refusal)))

    result = await provider.complete(LLMRequest(model="claude-opus-5-5", messages=llm_request.messages))

    assert result.finish_reason == "refusal"
    assert result.content == ""


async def test_stream_yields_deltas_then_final(llm_request: LLMRequest):
    events = [
        {"type": "message_start", "message": MESSAGE | {"content": [], "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " there"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "max_tokens", "stop_sequence": None},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    ]
    recorder = Recorder(
        lambda _: httpx2.Response(200, content=sse(events), headers={"content-type": "text/event-stream"})
    )
    provider = make_provider(recorder)

    chunks = [
        c async for c in provider.stream(LLMRequest(model="claude-opus-5-5", messages=llm_request.messages))
    ]

    assert [c.delta for c in chunks if not c.done] == ["Hi", " there"]
    final = chunks[-1]
    assert final.done and final.finish_reason == "length"
    assert final.usage.output_tokens == 9
    assert recorder.last_json["max_tokens"] == 64_000


@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [(401, "authentication_error", ProviderAuthError), (429, "rate_limit_error", ProviderRateLimitError)],
)
async def test_errors_are_mapped(llm_request: LLMRequest, status, error_type, expected):
    body = {"type": "error", "error": {"type": error_type, "message": "nope"}}
    provider = make_provider(Recorder(lambda _: httpx2.Response(status, json=body)))

    with pytest.raises(expected):
        await provider.complete(LLMRequest(model="claude-opus-5-5", messages=llm_request.messages))
