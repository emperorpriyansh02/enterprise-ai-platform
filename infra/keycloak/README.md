# Keycloak realm for local development

`eap-realm.json` is imported by Keycloak on start (`--import-realm`; `docker compose up` does this).

| What | Value |
|---|---|
| Realm | `eap` → issuer `http://localhost:8080/realms/eap` |
| Roles | `eap-admin`, `eap-editor`, `eap-user` (every new user gets `eap-user` by default) |
| Web client | `eap-web`: public, authorization code + PKCE (S256), redirect `http://localhost:3000/*` and `http://127.0.0.1:3000/*` |
| API audience | an audience mapper adds `eap-api` to access tokens (the backend's `OIDC_AUDIENCE`) |
| Demo users | `alice` / `alice-dev` (admin), `bob` / `bob-dev` (editor), `carol` / `carol-dev` (user) |
| Admin console | http://localhost:8080/admin, user `admin`, password `admin` (or `KEYCLOAK_ADMIN_PASSWORD`) |

The passwords are for local development only. For anything shared, delete the demo users, change the
admin password, and run Keycloak in production mode (`start`) with TLS and a real database.

Keycloak only imports a realm that doesn't exist yet. After editing the file, delete the realm in the
admin console (or the Keycloak data volume) and restart.
