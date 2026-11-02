"""Prompts admin API and chat-with-prompt, through the full app with a real database session."""

import json

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings

SUPPORT = {"name": "support", "description": "Support bot", "template": "You help {{ company }} customers."}
ASK = [{"role": "user", "content": "hi"}]


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]


async def test_prompt_lifecycle(api_factory):
    async with api_factory() as api:
        created = await api.post("/v1/prompts", json=SUPPORT)
        assert created.status_code == 201
        assert created.json()["active_version"] == 1

        v2 = await api.post(
            "/v1/prompts/support/versions", json={"template": "v2 {{ company }}", "note": "n"}
        )
        assert v2.status_code == 201 and v2.json()["version"] == 2 and not v2.json()["is_active"]

        activated = await api.post("/v1/prompts/support/activate", json={"version": 2})
        assert activated.json()["active_version"] == 2

        rolled_back = await api.post("/v1/prompts/support/activate", json={"version": 1})
        assert rolled_back.json()["active_version"] == 1

        got = await api.get("/v1/prompts/support/versions/2")
        assert got.json()["template"] == "v2 {{ company }}"

        patched = await api.patch("/v1/prompts/support", json={"description": "Updated"})
        assert patched.json()["description"] == "Updated"

        rendered = await api.post("/v1/prompts/support/render", json={"variables": {"company": "Acme"}})
        assert rendered.json() == {
            "name": "support",
            "version": 1,
            "text": "You help Acme customers.",
            "variables": ["company"],
        }

        listing = await api.get("/v1/prompts")
        assert [p["name"] for p in listing.json()["data"]] == ["support"]

        archived = await api.delete("/v1/prompts/support")
        assert archived.json()["archived"] is True
        assert (await api.get("/v1/prompts")).json()["data"] == []


async def test_errors_have_clear_codes(api_factory):
    async with api_factory() as api:
        await api.post("/v1/prompts", json=SUPPORT)

        duplicate = await api.post("/v1/prompts", json=SUPPORT)
        missing = await api.get("/v1/prompts/nope")
        bad_version = await api.post("/v1/prompts/support/activate", json={"version": 7})
        missing_var = await api.post("/v1/prompts/support/render", json={})
        bad_name = await api.post("/v1/prompts", json=SUPPORT | {"name": "Bad Name!"})

    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "prompt_conflict")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "prompt_not_found")
    assert (bad_version.status_code, bad_version.json()["error"]["code"]) == (404, "prompt_version_not_found")
    assert (missing_var.status_code, missing_var.json()["error"]["code"]) == (400, "prompt_variable_missing")
    assert bad_name.status_code == 422


async def test_admin_key_required_for_writes_when_configured(api_factory):
    async with api_factory(admin_api_key="s3cret") as api:
        no_key = await api.post("/v1/prompts", json=SUPPORT)
        wrong_key = await api.post("/v1/prompts", json=SUPPORT, headers={"X-Admin-Key": "nope"})
        ok = await api.post("/v1/prompts", json=SUPPORT, headers={"X-Admin-Key": "s3cret"})
        read = await api.get("/v1/prompts/support")  # reads stay open

    assert (no_key.status_code, no_key.json()["error"]["code"]) == (401, "admin_auth_required")
    assert wrong_key.status_code == 401
    assert ok.status_code == 201
    assert read.status_code == 200


def test_without_database_prompts_return_503_and_chat_still_works():
    with TestClient(create_app(make_settings())) as client:
        listing = client.get("/v1/prompts")
        with_prompt = client.post("/v1/chat", json={"messages": ASK, "prompt": {"name": "support"}})
        plain = client.post("/v1/chat", json={"messages": ASK})

    assert (listing.status_code, listing.json()["error"]["code"]) == (503, "prompt_store_unavailable")
    assert with_prompt.status_code == 503
    assert plain.status_code == 200


async def test_chat_uses_rendered_prompt_then_extra_system(api_factory):
    async with api_factory() as api:
        await api.post("/v1/prompts", json=SUPPORT)
        body = {
            "model": "mock/echo-system",
            "messages": ASK,
            "prompt": {"name": "support", "variables": {"company": "Acme"}},
            "system": "Reply in French.",
        }
        reply = (await api.post("/v1/chat", json=body)).json()

    assert reply["content"] == "You help Acme customers.\n\nReply in French."
    assert reply["prompt"] == {"name": "support", "version": 1}


async def test_chat_with_pinned_version_and_missing_variable(api_factory):
    async with api_factory() as api:
        await api.post("/v1/prompts", json=SUPPORT)
        await api.post("/v1/prompts/support/versions", json={"template": "Version two.", "activate": True})

        pinned = await api.post(
            "/v1/chat",
            json={
                "model": "mock/echo-system",
                "messages": ASK,
                "prompt": {"name": "support", "version": 1, "variables": {"company": "Acme"}},
            },
        )
        active = await api.post(
            "/v1/chat", json={"model": "mock/echo-system", "messages": ASK, "prompt": {"name": "support"}}
        )
        missing = await api.post(
            "/v1/chat",
            json={"model": "mock/echo-system", "messages": ASK, "prompt": {"name": "support", "version": 1}},
        )

    assert pinned.json()["content"] == "You help Acme customers."
    assert active.json()["content"] == "Version two." and active.json()["prompt"]["version"] == 2
    assert (missing.status_code, missing.json()["error"]["code"]) == (400, "prompt_variable_missing")


async def test_default_prompt_applies_when_request_has_no_system(api_factory):
    async with api_factory(default_prompt_name="support") as api:
        await api.post("/v1/prompts", json=SUPPORT | {"template": "Company-wide rules."})
        default = (await api.post("/v1/chat", json={"model": "mock/echo-system", "messages": ASK})).json()
        own = (
            await api.post("/v1/chat", json={"model": "mock/echo-system", "messages": ASK, "system": "Mine."})
        ).json()

    assert (default["content"], default["prompt"]) == (
        "Company-wide rules.",
        {"name": "support", "version": 1},
    )
    assert (own["content"], own.get("prompt")) == ("Mine.", None)


async def test_stream_reports_prompt_and_errors_before_stream(api_factory):
    async with api_factory() as api:
        await api.post("/v1/prompts", json=SUPPORT)
        ok = await api.post(
            "/v1/chat/stream",
            json={
                "model": "mock/echo-system",
                "messages": ASK,
                "prompt": {"name": "support", "variables": {"company": "Acme"}},
            },
        )
        unknown = await api.post(
            "/v1/chat/stream", json={"model": "mock/echo", "messages": ASK, "prompt": {"name": "nope"}}
        )

    events = parse_sse(ok.text)
    assert events[0]["type"] == "start" and events[0]["prompt"] == {"name": "support", "version": 1}
    assert "".join(e.get("delta", "") for e in events) == "You help Acme customers."
    assert events[-1]["type"] == "done" and events[-1]["prompt"]["version"] == 1
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "prompt_not_found")


async def test_health_reports_database(api_factory):
    async with api_factory() as api:
        assert (await api.get("/health")).json()["database"] == "ok"
