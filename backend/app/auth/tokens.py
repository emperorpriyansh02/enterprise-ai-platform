"""Checking access tokens from the identity provider (Keycloak, or any OIDC provider).

A token is accepted only if all of these hold:
- it's a JWT signed with an asymmetric algorithm (RS*/PS*/ES*; never `none` or a shared secret),
- by a key from the provider's published key set (JWKS), found by the token's `kid`,
- `iss` is OIDC_ISSUER, `aud` contains OIDC_AUDIENCE, and it hasn't expired (30 s clock leeway).

Keys are cached for OIDC_JWKS_CACHE_S. A token with an unknown `kid` (the provider rotated its keys)
makes us re-read the key set, at most once every 30 seconds.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt

from app.auth.principal import Principal
from app.core.config import Settings
from app.core.errors import AuthUnavailableError, InvalidTokenError

logger = logging.getLogger(__name__)

ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
LEEWAY_S = 30
REFETCH_AFTER_S = 30  # unknown `kid`: re-read the key set at most this often


class TokenVerifier:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        assert settings.oidc_issuer, "OIDC_ISSUER is required when auth is enabled"
        self._settings = settings
        self._issuer = settings.oidc_issuer
        self._http = http_client or httpx.AsyncClient(timeout=10)
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0  # last successful read of the key set
        self._last_attempt = 0.0  # last read, successful or not
        self._lock = asyncio.Lock()

    async def verify(self, token: str) -> Principal:
        """Check the token and say who it belongs to. Raises InvalidTokenError (401) if it's not good."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("The access token is not a valid JWT.") from exc
        algorithm = header.get("alg")
        if algorithm not in ALGORITHMS:
            raise InvalidTokenError(f"Token algorithm {algorithm!r} is not accepted.")
        key = await self._key(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self._settings.oidc_audience,
                issuer=self._issuer,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise InvalidTokenError("The access token has expired. Sign in again.") from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidTokenError(
                f"The access token is not meant for this API (audience must include "
                f"{self._settings.oidc_audience!r})."
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidTokenError(
                f"The access token was issued by someone else (expected {self._issuer})."
            ) from exc
        except jwt.InvalidSignatureError as exc:
            raise InvalidTokenError("The access token's signature is wrong.") from exc
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(f"The access token was rejected: {exc}.") from exc
        return principal_from_claims(claims, self._settings.oidc_audience)

    async def _key(self, kid: str | None) -> Any:
        """The public key for `kid`, from the cache or a fresh read of the key set."""
        if not kid:
            raise InvalidTokenError("The access token has no key id (kid).")
        if self._needs_fetch(kid):
            async with self._lock:  # one fetch at a time; the others reuse its result
                if self._needs_fetch(kid):
                    try:
                        await self._fetch_keys()
                    except AuthUnavailableError:
                        if kid not in self._keys:
                            raise
                        logger.warning("auth: can't refresh signing keys; using the cached ones")
        key = self._keys.get(kid)
        if key is None:
            raise InvalidTokenError("The access token was signed with an unknown key.")
        return key

    def _needs_fetch(self, kid: str) -> bool:
        """Read the key set? Yes when the cache is old, or for an unknown `kid` (keys rotated).

        At most once per REFETCH_AFTER_S while some keys are cached, so a flood of bad tokens or a
        provider outage doesn't turn every request into a slow network call.
        """
        now = time.monotonic()
        if self._keys and now - self._last_attempt < REFETCH_AFTER_S:
            return False
        return now - self._fetched_at > self._settings.oidc_jwks_cache_s or kid not in self._keys

    async def _fetch_keys(self) -> None:
        """Read the provider's key set (JWKS) and keep every signing key by its `kid`."""
        self._last_attempt = time.monotonic()
        url = self._settings.oidc_jwks_url or await self._discover_jwks_url()
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            key_set = jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            raise AuthUnavailableError(
                f"Can't read the identity provider's signing keys from {url}: {exc}"
            ) from exc
        self._keys = {k.key_id: k.key for k in key_set.keys if k.key_id and k.public_key_use in (None, "sig")}
        self._fetched_at = time.monotonic()
        logger.info("auth: loaded %d signing keys", len(self._keys))

    async def _discover_jwks_url(self) -> str:
        """`jwks_uri` from {OIDC_ISSUER}/.well-known/openid-configuration."""
        url = f"{self._issuer}/.well-known/openid-configuration"
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            return response.json()["jwks_uri"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise AuthUnavailableError(
                f"Can't read the identity provider's configuration at {url}: {exc}"
            ) from exc

    async def aclose(self) -> None:
        await self._http.aclose()


def principal_from_claims(claims: dict[str, Any], audience: str) -> Principal:
    """Token claims → Principal. Roles: Keycloak realm roles plus this API's client roles."""
    realm_roles = (claims.get("realm_access") or {}).get("roles") or []
    client_roles = ((claims.get("resource_access") or {}).get(audience) or {}).get("roles") or []
    return Principal(
        subject=str(claims["sub"]),
        username=str(claims.get("preferred_username") or claims["sub"]),
        name=claims.get("name"),
        email=claims.get("email"),
        roles=frozenset(str(r) for r in [*realm_roles, *client_roles]),
    )
