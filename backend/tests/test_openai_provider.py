import httpx2
import pytest

from app.core.errors import ProviderBadRequestError, ProviderUnavailableError
from app.llm.base import LLMRequest
from app.llm.providers.openai_provider import OpenAIProvider
from tests.conftest import Recorder, sse

COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-5-2026-01-01",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hi there", "refusal": None},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
}


def chunk(delta: dict | None = None, finish_reason=None, usage=None) -> dict:
    choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-5-2026-01-01",
        "choices": choices,
        "usage": usage,
    }


def make_provider(recorder: Recorder) -> OpenAIProvider:
    return OpenAIProvider(
        api_key="test-key", models=["gpt-5"], timeout_s=10, max_retries=0, http_client=recorder.client()
    )


async def test_complete_maps_request_and_response(llm_request: LLMRequest):
    recorder = Recorder(lambda _: httpx2.Response(200, json=COMPLETION))
    request = LLMRequest(model="gpt-5", system="Be brief.", messages=llm_request.messages, max_tokens=50)

    result = await make_provider(recorder).complete(request)

    assert result.content == "Hi there"
    assert result.finish_reason == "stop"
    assert result.model == "gpt-5-2026-01-01"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 3)
    body = recorder.last_json
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert body["max_completion_tokens"] == 50
    assert "temperature" not in body  # only sent when the caller sets it


async def test_refusal(llm_request: LLMRequest):
    refused = COMPLETION | {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "refusal": "I can't help with that."},
                "finish_reason": "stop",
            }
        ]
    }
    result = await make_provider(Recorder(lambda _: httpx2.Response(200, json=refused))).complete(
        LLMRequest(model="gpt-5", messages=llm_request.messages)
    )
    assert result.finish_reason == "refusal"
    assert result.content == "I can't help with that."


async def test_stream(llm_request: LLMRequest):
    events = [
        chunk({"role": "assistant", "content": "Hi"}),
        chunk({"content": " there"}),
        chunk({}, finish_reason="length"),
        chunk(None, usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}),
    ]
    body = sse([e | {"type": "chunk"} for e in events], named=False, done_marker=True)
    recorder = Recorder(
        lambda _: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )

    chunks = [
        c
        async for c in make_provider(recorder).stream(
            LLMRequest(model="gpt-5", messages=llm_request.messages)
        )
    ]

    assert "".join(c.delta for c in chunks) == "Hi there"
    assert chunks[-1].done and chunks[-1].finish_reason == "length"
    assert chunks[-1].usage.output_tokens == 3
    assert recorder.last_json["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize(
    ("status", "expected"), [(400, ProviderBadRequestError), (500, ProviderUnavailableError)]
)
async def test_errors_are_mapped(llm_request: LLMRequest, status, expected):
    body = {"error": {"message": "bad", "type": "invalid_request_error", "code": None, "param": None}}
    provider = make_provider(Recorder(lambda _: httpx2.Response(status, json=body)))
    with pytest.raises(expected):
        await provider.complete(LLMRequest(model="gpt-5", messages=llm_request.messages))
