# Architecture

## Request flow (Layers 1–5, today)

```
Browser (Next.js UI) ── sign in: Keycloak, authorization code + PKCE ──► access token (JWT)
   │  POST /v1/chat/stream  (SSE)        POST /v1/chat  (JSON)      Authorization: Bearer <token>
   ▼
FastAPI  app/api/v1/routes.py
   │  validation (schemas/chat.py) · request-id + JSON logs with user (main.py)
   │  get_principal: TokenVerifier (JWKS signature, iss, aud, exp) → roles     ◄── Layer 5
   │  require("chat:use")  → 403 if the roles don't allow it
   ▼
ChatService  app/services/chat_service.py      ◄── every later layer plugs in here
   │  registry.resolve("provider/model")       ◄── allowlist: only configured models; MODEL_ROLES per role
   │  _build_system()  ──►  PromptService  ──►  Postgres (prompts, prompt_versions)
   │                        render {{ variables }}, record {name, version}
   │  _retrieve()      ──►  RagService  ──►  embed question ──► pgvector + full-text ──► RRF
   │                        append numbered Sources, return citations
   │  _toolbox()       ──►  MCPService  ──►  enabled tools of the named MCP servers
   │  release the database connection
   │  model ↔ tool loop (≤ MCP_MAX_TOOL_ROUNDS):
   │     LLM asks for tools ──► Toolbox ──► MCPManager ──► MCP servers (stdio process / streamable HTTP)
   │                       ◄── results (cut to MCP_MAX_RESULT_CHARS) back to the LLM
   ▼
LLMProvider adapters  app/llm/providers/
   ├─ anthropic_provider.py   official anthropic SDK
   ├─ openai_provider.py      official openai SDK (also Azure / compatible servers)
   ├─ gemini_provider.py      official google-genai SDK
   ├─ ollama_provider.py      Ollama REST API (local models)
   └─ mock_provider.py        echo, for tests and keyless demos
```

## Target flow (all 9 layers)

```
request
  → [5] auth: validate Keycloak JWT, load roles, check permissions
  → [2] prompts: pick + render the versioned system prompt
  → [6] input guardrails: injection / PII / policy
  → [7a] cache: return early on a hit
  → [7b] router: choose model by policy, with fallback
  → [3] RAG: retrieve permitted chunks from pgvector
  → [4] MCP tools: model ↔ tool loop
  → [1] LLM call through the provider adapter
  → [6] output guardrails
  → [8] traces, metrics, cost · [9] eval logging
response
```

## Key decisions

- **One unified API.** Callers send `{model: "provider/model", messages, system, ...}` and get the
  same response shape from every vendor. Vendor SDKs are only touched inside `app/llm/providers/`.
- **Config-driven model allowlist.** A model can be called only if it's listed in config
  (e.g. `OPENAI_MODELS`). New models need no code change; unapproved models can't be reached.
- **Uniform errors.** Each adapter maps its SDK's exceptions to `Provider*Error`
  (`app/core/errors.py`), returned as `{"error": {"code", "message", "provider"}}` with a
  meaningful status: 400 bad request, 404 unknown model, 429 rate limit, 502 auth/config,
  503 unavailable, 504 timeout.
- **Streaming protocol.** `/v1/chat/stream` sends server-sent events whose `data` is a JSON
  `StreamEvent`: one `start`, many `delta`, then exactly one `done` (finish reason, usage,
  latency) or `error`. The provider is called *before* the response starts, so failures before
  the first token are normal HTTP errors; failures mid-stream arrive as an `error` event.
- **Provider quirks live in adapters.** Examples: current Claude models reject `temperature`,
  so the Anthropic adapter drops it; OpenAI uses `max_completion_tokens`; Gemini calls the
  assistant role `model`; Ollama streams NDJSON.
- **Refusal fallback (Claude).** For Claude models that support it, the adapter enables
  Anthropic's server-side fallback: if the model's safety classifier declines, the API retries on
  Anthropic's recommended fallback model in the same call. The response's `model` field shows
  which model actually answered. Turn off with `ANTHROPIC_REFUSAL_FALLBACK=false`.
- **Prompts are append-only versions (Layer 2).** Editing a prompt creates a new version; one
  version is active; rollback = activate an older one. Every reply records `prompt: {name, version}`,
  so evals (Layer 9) and audits can tie any answer to the exact text used. Requests can pin a version.
- **Prompt rules vs SQL.** `PromptService` holds the rules; `PromptRepository` holds the queries;
  routes stay thin. Tables are created by Alembic migrations (`backend/migrations`), never at startup.
- **Safe templates.** `{{ variable }}` substitution only: no loops, expressions or code, so a template
  can't execute anything. Missing values fail loudly (400) instead of sending a half-filled prompt.
- **Database is optional.** Without `DATABASE_URL`, chat works as in Layer 1 and prompt endpoints
  return 503. The read transaction for a prompt ends before the LLM call, so streaming doesn't hold
  a database connection.
- **Knowledge bases (Layer 3).** Uploads are parsed and saved as `pending`, then indexed by a
  background worker in the API process (chunk → embed → store), so uploads return immediately (202).
  Unfinished documents are re-queued on restart.
- **Hybrid retrieval.** Vector search (pgvector, HNSW, cosine) finds passages by meaning; Postgres
  full-text search (OR of the question's words) finds exact names and codes. Reciprocal Rank Fusion
  merges the two without having to calibrate their scores against each other.
- **One embedding space per collection.** Every vector has 768 numbers (all three providers support it).
  Each collection records the embedding model that indexed it; searching it with a different model is a
  409 until it's re-indexed, instead of silently returning nonsense.
- **Sources are data, not instructions.** Retrieved passages go into a clearly marked "Sources" block
  that tells the model not to follow directions inside them (stronger guardrails in Layer 6).
- **MCP tools are an admin allowlist (Layer 4).** Registering a server lists its tools, all switched
  off. An admin switches on the ones models may use; destructive tools (by the server's own
  annotations, or no annotations at all) are flagged and need a confirmation in the UI. A tool that
  starts calling itself destructive on a refresh is switched off again. Once on, tools run without
  asking (auto-run), so the allowlist is the control point.
- **One long-lived connection per MCP server.** `MCPManager` keeps each client open in its own
  background task (the MCP client must be opened and closed by the same task), connects lazily,
  reconnects after a crash, and limits each call (`MCP_CALL_TIMEOUT_S`). A non-destructive call that
  hit a dropped connection is retried once; a destructive one never is.
- **Local and remote servers.** stdio servers are processes the backend starts (minimal environment
  plus their own env values, never the backend's secrets); HTTP servers are reached over streamable
  HTTP with stored headers. `MCP_ALLOW_STDIO=false` turns local servers off for the whole platform.
  Env values and headers are encrypted at rest with a key derived from `SECRET_KEY`.
- **One tool contract for four vendors.** The adapters translate tools, tool calls and results for
  Claude, OpenAI, Gemini and Ollama. The model's own turn is kept as an opaque `provider_state` and
  replayed unchanged, so Claude's thinking blocks and Gemini's thought signatures survive the loop.
  Tool names are `server__tool` (letters, digits, `_`, `-`, ≤ 64 chars), valid for every vendor.
- **Tool failures don't fail the chat.** A tool error, timeout or unreachable server becomes an error
  result for the model to handle; the reply's `tool_calls` trace shows what ran, with what arguments,
  and how long it took.
- **Identity is the provider's job; permissions are ours (Layer 5).** Keycloak (any OIDC provider)
  signs users in and puts their roles in the access token. The backend only verifies tokens (public
  keys from the JWKS, cached, re-read on key rotation; `iss`, `aud`, `exp`; asymmetric algorithms only)
  and maps roles to permissions in code (`app/auth/permissions.py`), so changing what a role can do is
  a reviewed code change, not a console click.
- **Three roles, checked twice.** `eap-user` (chat, read), `eap-editor` (+ change prompts and knowledge),
  `eap-admin` (+ MCP servers). Routes declare the permission they need (`require(...)`); services filter
  what a role may see. The UI hides what you can't do, but the backend is what enforces it.
- **Resource rules hide, not just refuse.** Collections and MCP servers may list `allowed_roles`
  (empty = everyone; admins always pass). Others don't see them, and get the same 404 as for a name
  that doesn't exist, so names of restricted collections don't leak. Models can be limited with
  `MODEL_ROLES` (403, since model names aren't secret).
- **Browser sign-in with PKCE, no secret in the frontend.** The web client is public; oidc-client-ts runs
  the authorization code flow with PKCE (S256), keeps tokens in sessionStorage, and renews them
  before they expire. The frontend reads the provider settings from `/v1/auth/config` at runtime.
- **Who did it.** Every request log line names the user; prompts, collections, documents and MCP
  servers record `created_by`.
- **Sign-in off for local development.** `AUTH_ENABLED=false` makes every request act as a dev user
  (`DEV_USER_ROLES`); the startup log warns about it and the UI shows "sign-in off".
- **Testability.** Every adapter accepts an injected HTTP client; tests replay realistic provider
  responses through a mock transport, so the suite needs no keys and no network.
