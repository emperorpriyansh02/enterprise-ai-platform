# Layer 5 — RBAC + OAuth

OIDC login via Keycloak (JWT validation on every request), plus role-based access control:
which roles may use which models, tools, document collections and admin APIs.

**Hooks into:** a FastAPI dependency on every route, and `ChatService` for per-model/tool checks.
**Status:** not built yet.
