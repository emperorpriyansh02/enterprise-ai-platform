import json
import os
from collections.abc import AsyncIterator, Callable

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import Database
from app.llm.base import LLMRequest
from app.main import create_app
from app.prompts.service import PromptService
from app.schemas.chat import Message

# Database tests use in-memory SQLite unless TEST_DATABASE_URL points at a real database, e.g.
#   TEST_DATABASE_URL=postgresql+asyncpg://eap:eap@localhost:5432/eap_test uv run pytest
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# Knowledge-base tests need Postgres with pgvector; on SQLite they're skipped with this reason.
requires_pgvector = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres + pgvector: set TEST_DATABASE_URL=postgresql+asyncpg://...",
)


def make_settings(**overrides) -> Settings:
    """Settings that ignore the developer's real .env and environment."""
    base = {
        "anthropic_api_key": None,
        "openai_api_key": None,
        "gemini_api_key": None,
        "ollama_base_url": None,
        "database_url": None,
        "default_prompt_name": None,
        "auth_enabled": False,
        "oidc_issuer": None,
        "oidc_jwks_url": None,
        "dev_user_roles": ["eap-admin"],
        "model_roles": {},
        "embedding_provider": None,
        "embedding_model": None,
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


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    """A database with empty prompt tables, wiped after the test."""
    db = Database(TEST_DATABASE_URL)
    await db.drop_all()
    await db.create_all()
    yield db
    await db.drop_all()
    await db.aclose()


@pytest.fixture
async def prompt_service(database: Database) -> AsyncIterator[PromptService]:
    async with database.session() as session:
        yield PromptService(session)


@pytest.fixture
def api_factory(database: Database):
    """Build an async HTTP client for the full app with a database: `async with api_factory() as api`."""

    def factory(**settings_overrides) -> "AppClient":
        return AppClient(create_app(make_settings(**settings_overrides), database=database))

    return factory


class AppClient:
    """Runs the app's startup/shutdown and gives an httpx client bound to it (same event loop)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __aenter__(self) -> httpx.AsyncClient:
        self._lifespan = self.app.router.lifespan_context(self.app)
        await self._lifespan.__aenter__()
        self._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        return self._client

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()
        await self._lifespan.__aexit__(*exc)
