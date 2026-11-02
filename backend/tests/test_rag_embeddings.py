import json
import math

import httpx
import httpx2
import pytest

from app.core.errors import ProviderAuthError, ProviderError
from app.rag.embeddings import (
    GeminiEmbedder,
    MockEmbedder,
    OllamaEmbedder,
    OpenAIEmbedder,
    build_embedder,
    normalize,
)
from app.rag.models import EMBEDDING_DIMENSIONS
from tests.conftest import Recorder, make_settings

DIMS = EMBEDDING_DIMENSIONS


def vector(seed: float) -> list[float]:
    return [seed] * DIMS


def is_unit(v: list[float]) -> bool:
    return math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6)


async def test_openai_sends_768_dimensions_batches_and_normalizes():
    def reply(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        data = [
            {"object": "embedding", "index": i, "embedding": vector(i + 1.0)}
            for i in reversed(range(len(body["input"])))  # out of order on purpose
        ]
        usage = {"prompt_tokens": 1, "total_tokens": 1}
        return httpx2.Response(
            200, json={"object": "list", "data": data, "model": body["model"], "usage": usage}
        )

    recorder = Recorder(reply)
    embedder = OpenAIEmbedder(
        api_key="k", model="text-embedding-3-small", timeout_s=5, max_retries=0, http_client=recorder.client()
    )

    vectors = await embedder.embed([f"text {i}" for i in range(70)], kind="document")

    assert len(vectors) == 70 and all(is_unit(v) for v in vectors)
    assert len(recorder.requests) == 2  # 64 + 6
    assert recorder.last_json["dimensions"] == DIMS
    assert embedder.id == "openai/text-embedding-3-small@768"


async def test_openai_errors_use_the_chat_error_mapping():
    body = {"error": {"message": "bad key", "type": "invalid_request_error", "code": None, "param": None}}
    embedder = OpenAIEmbedder(
        api_key="k",
        model="m",
        timeout_s=5,
        max_retries=0,
        http_client=Recorder(lambda _: httpx2.Response(401, json=body)).client(),
    )
    with pytest.raises(ProviderAuthError):
        await embedder.embed(["x"], kind="query")


async def test_gemini_task_type_and_dimensions():
    def reply(request: httpx2.Request) -> httpx2.Response:
        count = len(json.loads(request.content)["requests"])
        return httpx2.Response(200, json={"embeddings": [{"values": vector(0.5)} for _ in range(count)]})

    recorder = Recorder(reply)
    embedder = GeminiEmbedder(
        api_key="k", model="gemini-embedding-001", timeout_s=5, max_retries=0, http_client=recorder.client()
    )

    vectors = await embedder.embed(["a question"], kind="query")

    assert is_unit(vectors[0])  # Gemini doesn't normalize reduced dimensions; we do
    sent = recorder.last_json["requests"][0]
    assert sent["taskType"] == "RETRIEVAL_QUERY" and sent["outputDimensionality"] == DIMS


async def test_ollama_nomic_prefixes_and_wrong_size_is_rejected():
    seen: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        size = DIMS if body["model"] == "nomic-embed-text" else 384
        return httpx.Response(200, json={"embeddings": [vector(1.0)[:size] for _ in body["input"]]})

    client = httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(reply))
    good = OllamaEmbedder(base_url="http://ollama", model="nomic-embed-text", timeout_s=5, http_client=client)
    small = OllamaEmbedder(base_url="http://ollama", model="all-minilm", timeout_s=5, http_client=client)

    await good.embed(["doc text"], kind="document")
    await good.embed(["user question"], kind="query")
    assert seen[0]["input"] == ["search_document: doc text"]
    assert seen[1]["input"] == ["search_query: user question"]
    with pytest.raises(ProviderError, match="expected 768"):
        await small.embed(["x"], kind="document")


async def test_mock_embedder_is_deterministic_and_word_based():
    embedder = MockEmbedder()
    a, b, c, empty = await embedder.embed(
        ["refund policy for orders", "orders refund policy", "rocket engine thrust", ""], kind="document"
    )
    similarity = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))  # noqa: E731
    assert a == (await embedder.embed(["refund policy for orders"], kind="query"))[0]
    assert similarity(a, b) > similarity(a, c)
    assert is_unit(empty)  # never an all-zero vector


def test_normalize_keeps_zero_vectors():
    assert normalize([0.0, 0.0]) == [0.0, 0.0]
    assert normalize([3.0, 4.0]) == [0.6, 0.8]


def test_build_embedder_picks_configured_or_first_available():
    assert build_embedder(make_settings()).id == "mock/hash@768"
    assert build_embedder(make_settings(openai_api_key="k")).provider == "openai"
    assert build_embedder(make_settings(openai_api_key="k", embedding_provider="mock")).provider == "mock"
    assert build_embedder(make_settings(enable_mock_provider=False)) is None
    assert build_embedder(make_settings(embedding_provider="gemini")) is None  # chosen but no key
    custom = build_embedder(
        make_settings(ollama_base_url="http://o:11434", embedding_model="mxbai-embed-large")
    )
    assert custom.id == "ollama/mxbai-embed-large@768"
