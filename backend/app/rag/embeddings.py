"""Embedding providers: turn text into vectors of EMBEDDING_DIMENSIONS (768) numbers.

All of them give back unit-length vectors (length 1), so cosine similarity compares them fairly.
Errors use the same classes, and the same mapping functions, as the chat adapters.
"""

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from typing import Any, Literal

import httpx
import httpx2
import openai
from google import genai
from google.genai import types

from app.core.config import Settings
from app.core.errors import ProviderError
from app.llm.providers import gemini_provider, ollama_provider, openai_provider
from app.rag.models import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

Kind = Literal["document", "query"]

DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
    "ollama": "nomic-embed-text",
    "mock": "hash",
}


class Embedder(ABC):
    provider: str
    batch_size = 64

    def __init__(self, model: str) -> None:
        self.model = model
        self.dimensions = EMBEDDING_DIMENSIONS

    @property
    def id(self) -> str:
        """Stored on each collection, e.g. "openai/text-embedding-3-small@768"."""
        return f"{self.provider}/{self.model}@{self.dimensions}"

    async def embed(self, texts: list[str], *, kind: Kind) -> list[list[float]]:
        """One unit-length vector per text, in the same order. Sends `batch_size` texts per call."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            result = await self._embed_batch(batch, kind)
            if len(result) != len(batch):
                raise ProviderError(
                    f"{self.provider} returned {len(result)} embeddings for {len(batch)} texts",
                    provider=self.provider,
                )
            for vector in result:
                if len(vector) != self.dimensions:
                    raise ProviderError(
                        f"{self.provider} model {self.model} returned {len(vector)} numbers, "
                        f"expected {self.dimensions}. Pick a {self.dimensions}-dimension model.",
                        provider=self.provider,
                    )
                vectors.append(normalize(vector))
        return vectors

    @abstractmethod
    async def _embed_batch(self, texts: list[str], kind: Kind) -> list[list[float]]:
        """Call the provider for one batch."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook, no-op by default
        """Release network clients. Called on app shutdown."""


class OpenAIEmbedder(Embedder):
    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float,
        max_retries: int,
        base_url: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(model)
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_s, "max_retries": max_retries}
        if base_url:
            kwargs["base_url"] = base_url
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = openai.AsyncOpenAI(**kwargs)

    async def _embed_batch(self, texts: list[str], kind: Kind) -> list[list[float]]:
        try:
            response = await self._client.embeddings.create(
                model=self.model, input=texts, dimensions=self.dimensions
            )
        except openai.OpenAIError as exc:
            raise openai_provider._map_error(exc) from exc
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    async def aclose(self) -> None:
        await self._client.close()


class GeminiEmbedder(Embedder):
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float,
        max_retries: int,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(model)
        http_options = types.HttpOptions(
            timeout=int(timeout_s * 1000),
            retry_options=types.HttpRetryOptions(attempts=max_retries + 1),
            httpx_async_client=http_client,
        )
        self._client = genai.Client(api_key=api_key, http_options=http_options)

    async def _embed_batch(self, texts: list[str], kind: Kind) -> list[list[float]]:
        config = types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT",
            output_dimensionality=self.dimensions,
        )
        try:
            response = await self._client.aio.models.embed_content(
                model=self.model, contents=texts, config=config
            )
        except Exception as exc:
            raise gemini_provider._map_error(exc) from exc
        return [list(item.values or []) for item in response.embeddings or []]

    async def aclose(self) -> None:
        await self._client.aio.aclose()


class OllamaEmbedder(Embedder):
    provider = "ollama"
    batch_size = 32

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_s: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(model)
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def _embed_batch(self, texts: list[str], kind: Kind) -> list[list[float]]:
        if self.model.startswith("nomic-embed"):
            # nomic-embed-text is trained with these prefixes and works noticeably better with them.
            prefix = "search_query: " if kind == "query" else "search_document: "
            texts = [prefix + t for t in texts]
        try:
            response = await self._client.post("/api/embed", json={"model": self.model, "input": texts})
            ollama_provider._raise_for_status(response)
        except httpx.HTTPError as exc:
            raise ollama_provider._map_error(exc) from exc
        return response.json().get("embeddings", [])

    async def aclose(self) -> None:
        await self._client.aclose()


class MockEmbedder(Embedder):
    """No network, no key. Texts that share words get similar vectors. For tests and demos only."""

    provider = "mock"

    def __init__(self, model: str = "hash") -> None:
        super().__init__(model)

    async def _embed_batch(self, texts: list[str], kind: Kind) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        if not any(vector):
            vector[0] = 1.0  # an all-zero vector has no direction, and cosine distance would be undefined
        return vector


def normalize(vector: list[float]) -> list[float]:
    """Scale to length 1. Gemini, for one, doesn't do this for reduced dimensions."""
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else vector


def build_embedder(settings: Settings) -> Embedder | None:
    """The embedder from EMBEDDING_PROVIDER, or the first configured one. None if none is available."""
    common = {"timeout_s": settings.request_timeout_s, "max_retries": settings.max_retries}
    available = {
        "openai": settings.openai_api_key is not None,
        "gemini": settings.gemini_api_key is not None,
        "ollama": settings.ollama_base_url is not None,
        "mock": settings.enable_mock_provider,
    }
    provider = (settings.embedding_provider or "").lower() or next(
        (name for name, ok in available.items() if ok), None
    )
    if provider is None:
        logger.info("no embedding provider configured: knowledge bases are off")
        return None
    if provider not in available:
        logger.warning("unknown EMBEDDING_PROVIDER %r: knowledge bases are off", provider)
        return None
    if not available[provider]:
        logger.warning("EMBEDDING_PROVIDER=%s but it isn't configured (key/URL missing)", provider)
        return None

    model = settings.embedding_model or DEFAULT_MODELS[provider]
    embedder: Embedder
    if provider == "openai":
        embedder = OpenAIEmbedder(
            api_key=settings.openai_api_key.get_secret_value(),
            model=model,
            base_url=settings.openai_base_url,
            **common,
        )
    elif provider == "gemini":
        embedder = GeminiEmbedder(api_key=settings.gemini_api_key.get_secret_value(), model=model, **common)
    elif provider == "ollama":
        embedder = OllamaEmbedder(
            base_url=settings.ollama_base_url, model=model, timeout_s=settings.request_timeout_s
        )
    else:
        embedder = MockEmbedder(model)
    logger.info("embedding model: %s", embedder.id)
    return embedder
