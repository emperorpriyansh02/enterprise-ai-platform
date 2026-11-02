# Architecture

## Request flow (Layers 1–2, today)

```
Browser (Next.js chat UI)
   │  POST /v1/chat/stream  (SSE)        POST /v1/chat  (JSON)
   ▼
FastAPI  app/api/v1/routes.py
   │  validation (schemas/chat.py) · request-id + JSON logs (main.py)
   ▼
ChatService  app/services/chat_service.py      ◄── every later layer plugs in here
   │  registry.resolve("provider/model")       ◄── allowlist: only configured models
   │  _build_system()  ──►  PromptService  ──►  Postgres (prompts, prompt_versions)
   │                        render {{ variables }}, record {name, version}
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
  → [5] auth: validate Keycloak JWT, load roles
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
- **Testability.** Every adapter accepts an injected HTTP client; tests replay realistic provider
  responses through a mock transport, so the suite needs no keys and no network.
