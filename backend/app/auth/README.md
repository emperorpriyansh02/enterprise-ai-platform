# Layer 5 — RBAC + OAuth

Sign-in with Keycloak (or any OpenID Connect provider) and role-based access control.

| File | What's in it |
|---|---|
| `permissions.py` | Roles (`eap-admin`, `eap-editor`, `eap-user`), the permissions each gives, and the `allowed_roles` rule |
| `principal.py` | `Principal`: who is calling (from a verified token, or the dev user when sign-in is off) |
| `tokens.py` | `TokenVerifier`: checks access tokens against the provider's published keys (JWKS), with caching and key rotation |
| `schemas.py` | `AuthConfig` (`GET /v1/auth/config`) and `Me` (`GET /v1/me`) |

FastAPI dependencies live in `app/api/deps.py`: `get_principal` (who), `require(permission)` (may they).

| Role | Permissions |
|---|---|
| `eap-user` | `chat:use`, `prompts:read`, `knowledge:read`, `tools:use` |
| `eap-editor` | user + `prompts:write`, `knowledge:write` |
| `eap-admin` | editor + `tools:admin`; passes every `allowed_roles` rule |

Per resource: collections and MCP servers have `allowed_roles` (empty = everyone); models can be limited
with `MODEL_ROLES`. Resources you may not use look missing (404); a forbidden model is 403.

**Tokens accepted:** asymmetrically signed JWTs (RS/PS/ES; never `none` or HS*) from a key in the provider's
JWKS, with `iss` = `OIDC_ISSUER`, `aud` containing `OIDC_AUDIENCE` (`eap-api`), not expired (30 s leeway).

**Local development:** `AUTH_ENABLED=false` (default) makes every request act as a dev user with
`DEV_USER_ROLES` (default `eap-admin`). Try `DEV_USER_ROLES=eap-user` to see a normal user's view.

**With Keycloak:** `docker compose up` starts Keycloak with the `eap` realm from `infra/keycloak/`
(demo users alice / bob / carol). See `infra/keycloak/README.md`.
