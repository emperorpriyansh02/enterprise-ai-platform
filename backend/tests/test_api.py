import json

from fastapi.testclient import TestClient

from app.llm.providers.mock_provider import MockProvider
from app.llm.registry import ProviderRegistry
from app.main import create_app
from tests.conftest import make_settings

HELLO = {"model": "mock/echo", "messages": [{"role": "user", "content": "hello world"}]}


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "providers": ["mock"],
        "database": "disabled",
        "rag": "disabled",
        "tools": "disabled",
    }
    assert response.headers["X-Request-ID"]


def test_models(client: TestClient):
    body = client.get("/v1/models").json()
    assert body["default"] == "mock/echo"
    assert body["data"] == [
        {"id": "mock/echo", "provider": "mock", "model": "echo"},
        {"id": "mock/echo-system", "provider": "mock", "model": "echo-system"},
    ]


def test_chat(client: TestClient):
    response = client.post("/v1/chat", json=HELLO)
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Echo: hello world"
    assert body["provider"] == "mock"
    assert body["finish_reason"] == "stop"
    assert body["usage"]["output_tokens"] == 3
    assert body["id"].startswith("chat_")


def test_chat_uses_default_model_when_omitted(client: TestClient):
    response = client.post("/v1/chat", json={"messages": HELLO["messages"]})
    assert response.status_code == 200
    assert response.json()["model"] == "echo"


def test_unknown_model_is_404(client: TestClient):
    response = client.post("/v1/chat", json=HELLO | {"model": "openai/gpt-5"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_first_message_must_be_user(client: TestClient):
    response = client.post("/v1/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})
    assert response.status_code == 422


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream(client: TestClient):
    with client.stream("POST", "/v1/chat/stream", json=HELLO) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(response.read().decode())

    assert [e["type"] for e in events] == ["start", "delta", "delta", "delta", "done"]
    assert "".join(e["delta"] for e in events if e["type"] == "delta") == "Echo: hello world"
    done = events[-1]
    assert done["finish_reason"] == "stop" and done["usage"]["output_tokens"] == 3
    assert len({e["id"] for e in events}) == 1


def test_stream_unknown_model_is_http_error(client: TestClient):
    response = client.post("/v1/chat/stream", json=HELLO | {"model": "nope/x"})
    assert response.status_code == 404


def test_stream_mid_stream_failure_sends_error_event():
    class FailingProvider(MockProvider):
        async def stream(self, request):
            from app.core.errors import ProviderUnavailableError
            from app.llm.base import StreamChunk

            yield StreamChunk(delta="partial")
            raise ProviderUnavailableError("upstream dropped", provider="mock")

    registry = ProviderRegistry([FailingProvider()], default_model="mock/echo")
    with TestClient(create_app(make_settings(), registry=registry)) as client:
        with client.stream("POST", "/v1/chat/stream", json=HELLO) as response:
            events = parse_sse(response.read().decode())

    assert [e["type"] for e in events] == ["start", "delta", "error"]
    assert events[-1]["error"]["code"] == "provider_unavailable"
