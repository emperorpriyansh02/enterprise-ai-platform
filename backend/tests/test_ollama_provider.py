import json

import httpx
import pytest

from app.core.errors import ProviderBadRequestError, ProviderUnavailableError
from app.llm.base import LLMRequest
from app.llm.providers.ollama_provider import OllamaProvider


def make_provider(handler) -> tuple[OllamaProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(base_url="http://ollama:11434", transport=httpx.MockTransport(record))
    return OllamaProvider(
        base_url="http://ollama:11434", models=["llama3.2"], timeout_s=10, http_client=client
    ), seen


async def test_complete(llm_request: LLMRequest):
    reply = {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": "Hi there"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 8,
        "eval_count": 2,
    }
    provider, seen = make_provider(lambda _: httpx.Response(200, json=reply))
    request = LLMRequest(model="llama3.2", system="Be brief.", messages=llm_request.messages, max_tokens=20)

    result = await provider.complete(request)

    assert result.content == "Hi there"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (8, 2)
    body = json.loads(seen[-1].content)
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert body["options"] == {"num_predict": 20}
    assert body["stream"] is False


async def test_stream_reads_ndjson(llm_request: LLMRequest):
    lines = [
        {"model": "llama3.2", "message": {"role": "assistant", "content": "Hi"}, "done": False},
        {"model": "llama3.2", "message": {"role": "assistant", "content": " there"}, "done": False},
        {"model": "llama3.2", "message": {"role": "assistant", "content": ""}, "done": True,
         "done_reason": "stop", "prompt_eval_count": 8, "eval_count": 2},
    ]  # fmt: skip
    body = "\n".join(json.dumps(line) for line in lines) + "\n"
    provider, _ = make_provider(lambda _: httpx.Response(200, content=body))

    chunks = [c async for c in provider.stream(LLMRequest(model="llama3.2", messages=llm_request.messages))]

    assert "".join(c.delta for c in chunks) == "Hi there"
    assert chunks[-1].done and chunks[-1].usage.output_tokens == 2


async def test_missing_model_gives_helpful_error(llm_request: LLMRequest):
    provider, _ = make_provider(lambda _: httpx.Response(404, json={"error": "model 'llama3.2' not found"}))
    with pytest.raises(ProviderBadRequestError, match="ollama pull"):
        await provider.complete(LLMRequest(model="llama3.2", messages=llm_request.messages))


async def test_unreachable_server(llm_request: LLMRequest):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider, _ = make_provider(refuse)
    with pytest.raises(ProviderUnavailableError):
        await provider.complete(LLMRequest(model="llama3.2", messages=llm_request.messages))
