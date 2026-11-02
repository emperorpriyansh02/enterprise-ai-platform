import httpx2
import pytest

from app.core.errors import ProviderAuthError, ProviderRateLimitError
from app.llm.base import LLMRequest, StreamChunk
from app.llm.providers.gemini_provider import GeminiProvider
from app.schemas.chat import Message
from tests.conftest import Recorder, sse


def response(text: str, finish: str | None = "STOP", usage: dict | None = None) -> dict:
    candidate = {"content": {"role": "model", "parts": [{"text": text}]}}
    if finish:
        candidate["finishReason"] = finish
    body = {"candidates": [candidate], "modelVersion": "gemini-flash-latest"}
    if usage:
        body["usageMetadata"] = usage
    return body


def make_provider(recorder: Recorder) -> GeminiProvider:
    return GeminiProvider(
        api_key="test-key",
        models=["gemini-flash-latest"],
        timeout_s=10,
        max_retries=0,
        http_client=recorder.client(),
    )


async def test_complete_maps_roles_and_system():
    usage = {"promptTokenCount": 5, "candidatesTokenCount": 2, "thoughtsTokenCount": 3}
    recorder = Recorder(lambda _: httpx2.Response(200, json=response("Hi there", usage=usage)))
    request = LLMRequest(
        model="gemini-flash-latest",
        system="Be brief.",
        temperature=0.2,
        messages=[
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hey"),
            Message(role="user", content="How are you?"),
        ],
    )

    result = await make_provider(recorder).complete(request)

    assert result.content == "Hi there"
    assert result.finish_reason == "stop"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (5, 5)  # thoughts count as output
    sent = recorder.requests[-1]
    assert sent.url.path.endswith("/models/gemini-flash-latest:generateContent")
    body = recorder.last_json
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert body["generationConfig"]["temperature"] == 0.2


async def test_safety_block_maps_to_content_filter(llm_request: LLMRequest):
    blocked = response("", finish="SAFETY")
    result = await make_provider(Recorder(lambda _: httpx2.Response(200, json=blocked))).complete(
        LLMRequest(model="gemini-flash-latest", messages=llm_request.messages)
    )
    assert result.finish_reason == "content_filter"


async def test_stream(llm_request: LLMRequest):
    events = [
        response("Hi", finish=None) | {"type": "x"},
        response(" there", usage={"promptTokenCount": 5, "candidatesTokenCount": 2}) | {"type": "x"},
    ]
    body = sse(events, named=False)
    recorder = Recorder(
        lambda _: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )

    chunks: list[StreamChunk] = [
        c
        async for c in make_provider(recorder).stream(
            LLMRequest(model="gemini-flash-latest", messages=llm_request.messages)
        )
    ]

    assert "".join(c.delta for c in chunks) == "Hi there"
    assert chunks[-1].done and chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage.input_tokens == 5
    assert "streamGenerateContent" in recorder.requests[-1].url.path


@pytest.mark.parametrize(("status", "expected"), [(403, ProviderAuthError), (429, ProviderRateLimitError)])
async def test_errors_are_mapped(llm_request: LLMRequest, status, expected):
    body = {"error": {"code": status, "message": "nope", "status": "ERROR"}}
    provider = make_provider(Recorder(lambda _: httpx2.Response(status, json=body)))
    with pytest.raises(expected):
        await provider.complete(LLMRequest(model="gemini-flash-latest", messages=llm_request.messages))
