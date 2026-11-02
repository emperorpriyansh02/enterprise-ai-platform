import json
from collections.abc import Callable

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm.base import LLMRequest
from app.main import create_app
from app.schemas.chat import Message


def make_settings(**overrides) -> Settings:
    """Settings that ignore the developer's real .env and environment."""
    base = {
        "anthropic_api_key": None,
        "openai_api_key": None,
        "gemini_api_key": None,
        "ollama_base_url": None,
        "enable_mock_provider": True,
        "mock_stream_delay_s": 0.0,
        "default_model": "mock/echo",
    }
    return Settings(_env_file=None, **(base | overrides))


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client


@pytest.fixture
def llm_request() -> LLMRequest:
    return LLMRequest(
        model="test-model",
        system="Be brief.",
        messages=[Message(role="user", content="Hello")],
    )


def sse(events: list[dict], *, named: bool = True, done_marker: bool = False) -> bytes:
    """Encode events as a server-sent-events body."""
    lines = []
    for event in events:
        if named:
            lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    if done_marker:
        lines += ["data: [DONE]", ""]
    return ("\n".join(lines) + "\n").encode()


class Recorder:
    """httpx2 mock transport that records requests and replies via a handler."""

    def __init__(self, handler: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.handler(request)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    @property
    def last_json(self) -> dict:
        return json.loads(self.requests[-1].content)
