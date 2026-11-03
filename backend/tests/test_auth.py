"""Sign-in and access control (Layer 5): token checks, roles → permissions, and per-resource rules.

Tokens are signed with real RSA keys by `FakeIdP`, which also serves the key set and discovery
document the way Keycloak does, through a mock HTTP transport.
"""

import asyncio
import json
import logging
import sys
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

import app.auth.tokens as tokens_module
from app.auth.permissions import ADMIN, EDITOR, USER, permissions_for, roles_allow
from app.auth.tokens import TokenVerifier
from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from tests.conftest import AppClient, make_settings, requires_pgvector

ISSUER = "http://idp.test/realms/eap"
AUDIENCE = "eap-api"
DEMO = {
    "name": "demo",
    "transport": "stdio",
    "command": sys.executable,
    "args": ["-m", "app.mcp.demo_server"],
}
USERS = {"alice": [ADMIN], "bob": [EDITOR], "carol": [USER]}


class FakeIdP:
    """Signs tokens like Keycloak and serves its key set (JWKS) and discovery document."""

    def __init__(self) -> None:
        self.keys = {"key-1": rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.requests: list[str] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if self.down:
            return httpx.Response(503)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"}
            )
        if request.url.path.endswith("/protocol/openid-connect/certs"):
            return httpx.Response(200, json={"keys": [self.jwk(kid) for kid in self.keys]})
        return httpx.Response(404)

    def jwk(self, kid: str) -> dict:
        return json.loads(RSAAlgorithm.to_jwk(self.keys[kid].public_key())) | {
            "kid": kid,
            "use": "sig",
            "alg": "RS256",
        }

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def token(
        self, username: str, roles: list[str] | None = None, *, kid: str = "key-1", key=None, **claims
    ) -> str:
        now = int(time.time())
        payload = {
            "iss": ISSUER,
            "aud": [AUDIENCE, "account"],
            "sub": str(uuid.uuid5(uuid.NAMESPACE_DNS, username)),
            "azp": "eap-web",
            "typ": "Bearer",
            "iat": now,
            "exp": now + 300,
            "preferred_username": username,
            "name": username.title(),
            "email": f"{username}@example.com",
            "realm_access": {"roles": roles if roles is not None else USERS.get(username, [])},
        } | claims
        return jwt.encode(payload, key or self.keys[kid], algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


def auth_app(idp: FakeIdP, database: Database | None = None, **overrides):
    settings = make_settings(auth_enabled=True, oidc_issuer=ISSUER, **overrides)
    return AppClient(
        create_app(settings, database=database, auth=TokenVerifier(settings, http_client=idp.client()))
    )


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- Tokens ---


async def test_each_user_gets_their_roles_and_permissions(idp: FakeIdP):
    async with auth_app(idp) as api:
        me = {name: (await api.get("/v1/me", headers=bearer(idp.token(name)))).json() for name in USERS}

    assert me["alice"]["username"] == "alice" and me["alice"]["roles"] == [ADMIN]
    assert "tools:admin" in me["alice"]["permissions"]
    assert "prompts:write" in me["bob"]["permissions"] and "tools:admin" not in me["bob"]["permissions"]
    assert sorted(me["carol"]["permissions"]) == ["chat:use", "knowledge:read", "prompts:read", "tools:use"]
    assert me["carol"]["email"] == "carol@example.com" and me["carol"]["auth_enabled"] is True
    assert me["carol"]["is_dev"] is False


async def test_missing_token_is_401_with_a_bearer_challenge(idp: FakeIdP):
    async with auth_app(idp) as api:
        missing = await api.get("/v1/me")
        basic = await api.get("/v1/me", headers={"Authorization": "Basic YWxpY2U6cHc="})
        health = await api.get("/health")  # stays public
        config = await api.get("/v1/auth/config")  # stays public

    assert (missing.status_code, missing.json()["error"]["code"]) == (401, "auth_required")
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert basic.status_code == 401
    assert health.status_code == 200
    assert config.json() == {
        "enabled": True,
        "issuer": ISSUER,
        "client_id": "eap-web",
        "scope": "openid profile email",
    }


@pytest.mark.parametrize(
    ("make_token", "message"),
    [
        (lambda idp: idp.token("alice", exp=int(time.time()) - 120), "expired"),
        (lambda idp: idp.token("alice", aud="someone-else"), "not meant for this API"),
        (lambda idp: idp.token("alice", iss="http://evil.test/realms/eap"), "issued by someone else"),
        (
            lambda idp: idp.token(
                "alice", key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ),
            "signature",
        ),
        (lambda idp: idp.token("alice", kid="key-9", key=idp.keys["key-1"]), "unknown key"),
        (lambda idp: jwt.encode({"sub": "alice"}, None, algorithm="none"), "algorithm 'none'"),
        (
            lambda idp: jwt.encode({"sub": "alice"}, "x" * 32, algorithm="HS256", headers={"kid": "key-1"}),
            "HS256",
        ),
        (lambda idp: "not-a-jwt", "not a valid JWT"),
    ],
    ids=["expired", "audience", "issuer", "signature", "unknown-kid", "alg-none", "hs256", "garbage"],
)
async def test_bad_tokens_are_rejected(idp: FakeIdP, make_token, message: str):
    async with auth_app(idp) as api:
        response = await api.get("/v1/me", headers=bearer(make_token(idp)))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert message in response.json()["error"]["message"]
    assert "invalid_token" in response.headers["WWW-Authenticate"]


async def test_keys_are_discovered_cached_and_refetched_after_rotation(idp: FakeIdP, monkeypatch):
    async with auth_app(idp) as api:
        for _ in range(3):
            assert (await api.get("/v1/me", headers=bearer(idp.token("alice")))).status_code == 200
        assert idp.requests == [
            "/realms/eap/.well-known/openid-configuration",
            "/realms/eap/protocol/openid-connect/certs",
        ]  # discovered once, then cached

        idp.keys["key-2"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # the IdP rotates
        monkeypatch.setattr(tokens_module, "REFETCH_AFTER_S", 0)
        rotated = await api.get("/v1/me", headers=bearer(idp.token("alice", kid="key-2")))

    assert rotated.status_code == 200
    assert idp.requests.count("/realms/eap/protocol/openid-connect/certs") == 2


async def test_configured_jwks_url_skips_discovery(idp: FakeIdP):
    async with auth_app(
        idp, oidc_jwks_url="http://keycloak:8080/realms/eap/protocol/openid-connect/certs"
    ) as api:
        assert (await api.get("/v1/me", headers=bearer(idp.token("bob")))).status_code == 200
    assert idp.requests == ["/realms/eap/protocol/openid-connect/certs"]


async def test_provider_down(idp: FakeIdP, monkeypatch):
    idp.down = True
    async with auth_app(idp) as api:
        response = await api.get("/v1/me", headers=bearer(idp.token("alice")))
    assert (response.status_code, response.json()["error"]["code"]) == (503, "auth_unavailable")

    # Keys already cached: an outage while refreshing them keeps working with the cached keys.
    idp.down = False
    monkeypatch.setattr(tokens_module, "REFETCH_AFTER_S", 0)
    async with auth_app(idp, oidc_jwks_cache_s=0.001) as api:
        assert (await api.get("/v1/me", headers=bearer(idp.token("alice")))).status_code == 200
        idp.down = True
        await asyncio.sleep(0.01)
        assert (await api.get("/v1/me", headers=bearer(idp.token("alice")))).status_code == 200


class ListHandler(logging.Handler):
    """Keeps log records in a list (the app replaces the root handlers, so pytest's capture misses them)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def test_request_log_names_the_user(idp: FakeIdP):
    handler = ListHandler()
    request_logger = logging.getLogger("app.request")
    async with auth_app(idp) as api:
        request_logger.addHandler(handler)
        try:
            await api.get("/v1/me", headers=bearer(idp.token("carol")))
        finally:
            request_logger.removeHandler(handler)
    [line] = [r for r in handler.records if r.getMessage() == "request"]
    assert line.user == "carol"


# --- Roles → permissions on routes ---


async def test_permission_matrix(idp: FakeIdP, database: Database):
    prompt = {"name": "greeter", "template": "Say hi.", "description": ""}
    async with auth_app(idp, database) as api:
        carol, bob, alice = (bearer(idp.token(n)) for n in ("carol", "bob", "alice"))

        assert (await api.get("/v1/prompts", headers=carol)).status_code == 200
        assert (await api.post("/v1/prompts", json=prompt, headers=carol)).status_code == 403
        created = await api.post("/v1/prompts", json=prompt, headers=bob)
        assert created.status_code == 201 and created.json()["versions"][0]["created_by"] == "bob"

        assert (await api.get("/v1/tools", headers=carol)).status_code == 200
        assert (await api.get("/v1/tools/servers", headers=bob)).status_code == 403
        assert (await api.get("/v1/tools/servers", headers=alice)).status_code == 200

        chat = {"model": "mock/echo", "messages": [{"role": "user", "content": "hi"}]}
        assert (await api.post("/v1/chat", json=chat, headers=carol)).status_code == 200
        assert (await api.post("/v1/chat", json=chat)).status_code == 401
        nobody = bearer(idp.token("dave", roles=["some-other-role"]))
        refused = await api.post("/v1/chat", json=chat, headers=nobody)
        assert refused.status_code == 403 and "chat:use" in refused.json()["error"]["message"]


async def test_model_roles_limit_who_may_use_a_model(idp: FakeIdP):
    roles = {"mock/echo-system": [ADMIN]}
    async with auth_app(idp, model_roles=roles, default_model="mock/echo-system") as api:
        carol, alice = bearer(idp.token("carol")), bearer(idp.token("alice"))
        carol_models = (await api.get("/v1/models", headers=carol)).json()
        alice_models = (await api.get("/v1/models", headers=alice)).json()
        body = {"model": "mock/echo-system", "messages": [{"role": "user", "content": "hi"}]}
        refused = await api.post("/v1/chat", json=body, headers=carol)
        allowed = await api.post("/v1/chat", json=body, headers=alice)
        stream_refused = await api.post("/v1/chat/stream", json=body, headers=carol)

    assert [m["id"] for m in carol_models["data"]] == ["mock/echo"]
    assert carol_models["default"] == "mock/echo"  # the configured default isn't allowed for carol
    assert alice_models["default"] == "mock/echo-system" and len(alice_models["data"]) == 2
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert allowed.status_code == 200
    assert stream_refused.status_code == 403


async def test_mcp_server_roles_limit_who_may_use_its_tools(idp: FakeIdP, database: Database):
    async with auth_app(idp, database) as api:
        alice, bob, carol = (bearer(idp.token(n)) for n in ("alice", "bob", "carol"))
        created = await api.post("/v1/tools/servers", json=DEMO | {"allowed_roles": [EDITOR]}, headers=alice)
        assert created.json()["allowed_roles"] == [EDITOR] and created.json()["created_by"] == "alice"
        await api.patch("/v1/tools/servers/demo/tools/get_time", json={"enabled": True}, headers=alice)

        seen = {n: [s["name"] for s in (await api.get("/v1/tools", headers=h)).json()["data"]] for n, h in
                (("alice", alice), ("bob", bob), ("carol", carol))}  # fmt: skip
        chat = {
            "model": "mock/echo",
            "messages": [{"role": "user", "content": '/tool get_time {"timezone": "UTC"}'}],
            "tools": {"servers": ["demo"]},
        }
        carol_chat = await api.post("/v1/chat", json=chat, headers=carol)
        bob_chat = await api.post("/v1/chat", json=chat, headers=bob)

        opened = await api.patch("/v1/tools/servers/demo", json={"allowed_roles": []}, headers=alice)
        carol_after = await api.get("/v1/tools", headers=carol)

    assert seen == {"alice": ["demo"], "bob": ["demo"], "carol": []}
    assert carol_chat.status_code == 404  # as if the server didn't exist
    assert bob_chat.status_code == 200 and bob_chat.json()["tool_calls"][0]["tool"] == "get_time"
    assert opened.json()["allowed_roles"] == []
    assert [s["name"] for s in carol_after.json()["data"]] == ["demo"]


@requires_pgvector
async def test_collection_roles_hide_collections(idp: FakeIdP, database: Database):
    async with auth_app(idp, database, embedding_provider="mock") as api:
        alice, bob, carol = (bearer(idp.token(n)) for n in ("alice", "bob", "carol"))
        secret = await api.post(
            "/v1/collections", json={"name": "board-minutes", "allowed_roles": [EDITOR]}, headers=alice
        )
        await api.post("/v1/collections", json={"name": "handbook"}, headers=bob)
        refused = await api.post("/v1/collections", json={"name": "carols"}, headers=carol)
        added = await api.post(
            "/v1/collections/board-minutes/documents/text",
            json={"title": "Q3", "text": "Revenue grew."},
            headers=bob,
        )

        carol_list = [c["name"] for c in (await api.get("/v1/collections", headers=carol)).json()["data"]]
        bob_list = [c["name"] for c in (await api.get("/v1/collections", headers=bob)).json()["data"]]
        carol_get = await api.get("/v1/collections/board-minutes", headers=carol)
        chat = {"model": "mock/echo", "messages": [{"role": "user", "content": "revenue?"}],
                "rag": {"collections": ["board-minutes"]}}  # fmt: skip
        carol_chat = await api.post("/v1/chat", json=chat, headers=carol)

        opened = await api.patch("/v1/collections/board-minutes", json={"allowed_roles": []}, headers=bob)
        carol_after = await api.get("/v1/collections/board-minutes", headers=carol)

    assert secret.json()["allowed_roles"] == [EDITOR] and secret.json()["created_by"] == "alice"
    assert refused.status_code == 403
    assert added.json()["created_by"] == "bob"
    assert carol_list == ["handbook"] and bob_list == ["board-minutes", "handbook"]
    assert carol_get.status_code == 404 and carol_chat.status_code == 404
    assert opened.json()["allowed_roles"] == [] and carol_after.status_code == 200


# --- Dev mode and settings ---


def test_dev_mode_acts_as_the_dev_user(client):
    me = client.get("/v1/me").json()
    config = client.get("/v1/auth/config").json()
    assert (me["username"], me["roles"], me["is_dev"], me["auth_enabled"]) == ("dev", [ADMIN], True, False)
    assert config["enabled"] is False


async def test_dev_user_roles_can_be_lowered():
    async with AppClient(create_app(make_settings(dev_user_roles=[USER]))) as api:
        me = (await api.get("/v1/me")).json()
    assert me["roles"] == [USER] and "prompts:write" not in me["permissions"]


def test_auth_needs_an_issuer():
    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        Settings(_env_file=None, auth_enabled=True)


def test_permissions_and_resource_rules():
    assert permissions_for(["unknown"]) == frozenset()
    assert permissions_for([USER, EDITOR]) == permissions_for([EDITOR])
    assert roles_allow([USER], []) and roles_allow([USER], None)
    assert not roles_allow([USER], [EDITOR])
    assert roles_allow([ADMIN], [EDITOR])  # admins pass every resource rule
    assert roles_allow([USER, EDITOR], [EDITOR])
