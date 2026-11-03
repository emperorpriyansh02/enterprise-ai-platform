# Enterprise AI Platform

A multi-provider AI platform built in layers: one API in front of Claude, OpenAI, Gemini and
local models, with prompts, RAG, tools, access control, guardrails, caching, routing,
observability and evals added one layer at a time.

- **Backend:** Python 3.13 + FastAPI (`backend/`)
- **Frontend:** Next.js + TypeScript + Tailwind (`frontend/`)

## Layers

| # | Layer | Status | Where |
|---|-------|--------|-------|
| 1 | API calls: unified multi-provider LLM API (Claude, OpenAI, Gemini, Ollama), streaming | ✅ Built | `backend/app/llm`, `backend/app/api` |
| 2 | System prompts: versioned templates in Postgres, admin API, Prompts page | ✅ Built | `backend/app/prompts`, `frontend/components/prompts` |
| 3 | RAG: knowledge bases (txt/md/pdf/docx/html), hybrid search on pgvector, cited answers | ✅ Built | `backend/app/rag`, `frontend/components/knowledge` |
| 4 | MCP tools: stdio + HTTP MCP servers, admin tool allowlist, model ↔ tool loop for all providers, Tools page | ✅ Built | `backend/app/mcp`, `frontend/components/tools` |
| 5 | RBAC + OAuth (Keycloak / OIDC) | Planned | `backend/app/auth` |
| 6 | Guardrails | Planned | `backend/app/guardrails` |
| 7 | Cache + router | Planned | `backend/app/cache`, `backend/app/routing` |
| 8 | Observability (OpenTelemetry) | Planned (JSON logs today) | `backend/app/observability` |
| 9 | Evals | Planned | `backend/evals` |

## Code guides

- [docs/WORKFLOW_CODE_MAP.md](docs/WORKFLOW_CODE_MAP.md): for each workflow (startup, chat, streaming, errors, frontend...), which functions run, in order.
- [docs/FUNCTION_GUIDE.md](docs/FUNCTION_GUIDE.md): every function in plain language (what it takes in, what it does, what it gives back, how it fails, how to debug it), plus a debugging cheat sheet.
- [docs/architecture.md](docs/architecture.md): request flow and design decisions.

A test (`backend/tests/test_docs_coverage.py`) fails if any function is missing from the two guides, so they stay complete as the code grows.

## Quick start

Needs Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22, and Postgres 16 with the
[pgvector](https://github.com/pgvector/pgvector) extension for prompts and knowledge bases (optional; chat works without it).

**1. Backend** (http://localhost:8000, API docs at http://localhost:8000/docs)

```bash
cd backend
cp .env.example .env    # add at least one API key, or use the built-in mock models
uv sync
```

For managed prompts and knowledge bases, set `DATABASE_URL=postgresql+asyncpg://eap:eap@localhost:5432/eap` in `backend/.env`, then create the tables (this also enables pgvector, so run it as a user allowed to create extensions):

```bash
uv run alembic upgrade head
```

```bash
uv run python -m app.prompts.seed
```

Start the API:

```bash
uv run uvicorn app.main:app --reload
```

**2. Frontend** (http://localhost:3000)

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

**Or with Docker** (includes Postgres, and runs migrations on start):

```bash
docker compose up --build
```

```bash
docker compose exec backend python -m app.prompts.seed
```

Add `--profile local` to also run Ollama, then set `OLLAMA_BASE_URL=http://ollama:11434` in
`backend/.env` and pull a model: `docker compose exec ollama ollama pull llama3.2`.

## Configuration

Providers turn on automatically when their key (or URL, for Ollama) is set in `backend/.env`.
Only models listed in config can be called. See [`backend/.env.example`](backend/.env.example).

| Provider | Enable with | Models setting |
|----------|-------------|----------------|
| Anthropic Claude | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODELS` (default: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`) |
| OpenAI | `OPENAI_API_KEY` (+ optional `OPENAI_BASE_URL`) | `OPENAI_MODELS` |
| Google Gemini | `GEMINI_API_KEY` | `GEMINI_MODELS` |
| Ollama | `OLLAMA_BASE_URL` | `OLLAMA_MODELS` |
| Mock | `ENABLE_MOCK_PROVIDER=true` (default) | `echo` (echoes your message), `echo-system` (replies with the system prompt it received) |

| Setting | Purpose |
|---------|---------|
| `DATABASE_URL` | Postgres for managed prompts. Blank = prompt features off. |
| `DEFAULT_PROMPT_NAME` | Prompt used when a request sends neither `prompt` nor `system`. |
| `ADMIN_API_KEY` | If set, prompt and knowledge-base changes need the `X-Admin-Key` header (stand-in until RBAC in Layer 5). |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | Embeddings for knowledge bases: `openai`, `gemini`, `ollama` or `mock` (blank = first configured). Vectors are 768 numbers. See [backend/app/rag/README.md](backend/app/rag/README.md). |
| `RAG_*` | Chunk size and overlap, passages per answer, upload limit, indexing concurrency. |
| `SECRET_KEY` | Encrypts MCP server secrets (env values, headers) in the database. Needed only to save secrets. |
| `MCP_ALLOW_STDIO` | Allow MCP servers the backend starts as local commands (default `true`). `false` = HTTP servers only. |
| `MCP_*` | Tool rounds per message (5), call and connect timeouts, max tool result length. See [backend/app/mcp/README.md](backend/app/mcp/README.md). |

OpenAI and Gemini model names change often. Check the provider's current model list and set
`OPENAI_MODELS` / `GEMINI_MODELS` to match.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness, enabled providers, database / RAG / tools status |
| GET | `/v1/models` | Models you can call, and the default |
| POST | `/v1/chat` | Chat completion (JSON) |
| POST | `/v1/chat/stream` | Chat completion as server-sent events |
| GET/POST | `/v1/prompts` | List / create managed prompts |
| GET/PATCH/DELETE | `/v1/prompts/{name}` | Read / edit description / archive |
| POST | `/v1/prompts/{name}/versions` | New version (`"activate": true` to switch to it) |
| POST | `/v1/prompts/{name}/activate` | Make a version active (rollback = an older one) |
| POST | `/v1/prompts/{name}/render` | Preview the final text without calling an LLM |
| GET | `/v1/prompts/{name}/versions/{n}` | One version |
| GET/POST | `/v1/collections` | List / create knowledge bases |
| GET/DELETE | `/v1/collections/{name}` | Details / archive |
| POST | `/v1/collections/{name}/documents` | Upload files (multipart `files`); `.../documents/text` for pasted text |
| GET/DELETE | `/v1/collections/{name}/documents/{id}` | Document status / delete |
| POST | `/v1/collections/{name}/search` | Test search (no LLM) |
| POST | `/v1/collections/{name}/reindex` | Re-embed everything (after changing the embedding model) |
| GET | `/v1/tools` | MCP servers and tools a chat may use |
| GET/POST | `/v1/tools/servers` | List / register MCP servers (stdio command or HTTP URL) |
| GET/PATCH/DELETE | `/v1/tools/servers/{name}` | Details / enable-disable / delete |
| POST | `/v1/tools/servers/{name}/refresh` | Reconnect and re-read the tool list |
| PATCH | `/v1/tools/servers/{name}/tools/{tool}` | Switch a tool on or off (`{"enabled": true}`) |
| POST | `/v1/tools/servers/{name}/tools/{tool}/call` | Run a tool directly, to test it |
| GET | `/v1/tools/presets` | Ready-made servers (the built-in demo server) |

```bash
curl -s localhost:8000/v1/chat -H 'content-type: application/json' -d '{
  "model": "anthropic/claude-opus-5-5",
  "system": "You are a concise assistant.",
  "messages": [{"role": "user", "content": "What is RAG?"}]
}'
```

Response:

```json
{
  "id": "chat_…",
  "provider": "anthropic",
  "model": "claude-opus-5-5",
  "content": "…",
  "finish_reason": "stop",
  "usage": {"input_tokens": 20, "output_tokens": 85},
  "latency_ms": 2140
}
```

With a managed prompt (Layer 2): add `"prompt": {"name": "support-agent", "variables": {"company_name": "Acme", "support_email": "help@acme.com"}}`.
The reply then includes `"prompt": {"name": "support-agent", "version": 1}`. Any `system` text is added after the prompt.
See [backend/app/prompts/README.md](backend/app/prompts/README.md).

With knowledge bases (Layer 3): add `"rag": {"collections": ["hr-policies"]}`. The best passages are added as numbered
sources, the model cites them as [1], [2], and the reply includes `"citations"`.
See [backend/app/rag/README.md](backend/app/rag/README.md).

With MCP tools (Layer 4): add `"tools": {"servers": ["demo"]}`. The model may call that server's switched-on tools
(up to `MCP_MAX_TOOL_ROUNDS` rounds), and the reply includes `"tool_calls"`: each call's server, tool, arguments,
result and duration. Register servers and switch tools on in the **Tools** page (try **Use demo server**).
See [backend/app/mcp/README.md](backend/app/mcp/README.md).

Streaming events (`/v1/chat/stream`), one JSON object per SSE `data:` line:
`start` → `delta` (text chunks), with `tool_call` / `tool_result` pairs when tools run → `done` (finish reason, usage, latency), or `error`.

Errors always look like `{"error": {"code": "...", "message": "...", "provider": "..."}}`.

## Development

```bash
cd backend && uv run pytest          # tests (no API keys, network or database needed: uses SQLite; pgvector tests skip)
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://eap:eap@localhost:5432/eap_test uv run pytest  # same tests on Postgres
cd backend && uv run ruff check .    # lint
cd frontend && npm run lint && npm run build
```

## Project structure

```
backend/
  app/
    main.py                 FastAPI app, CORS, request logging
    core/                   config, errors, logging
    schemas/chat.py         public request/response models
    api/v1/routes.py        HTTP routes
    services/chat_service.py  orchestration (later layers plug in here)
    db/                     database connection (async SQLAlchemy)
    llm/
      base.py               provider interface
      registry.py           provider setup + model allowlist
      providers/            anthropic, openai, gemini, ollama, mock
    prompts/                Layer 2: models, repository (SQL), service (rules), templates, seed
    rag/                    Layer 3: parsers, chunking, embedders, hybrid search, background indexing
    mcp/                    Layer 4: MCP connections, server registry + tool allowlist, toolbox, demo server
    auth/ guardrails/ cache/ routing/ observability/   (layers 5–8)
  migrations/               Alembic database migrations
  evals/                    (layer 9)
  tests/
frontend/
  app/page.tsx              chat page
  app/prompts/page.tsx      prompts page
  app/knowledge/page.tsx    knowledge page
  app/tools/page.tsx        tools page
  components/chat.tsx       chat UI (prompt + model pickers, variables, streaming)
  components/prompts/       prompts UI (list, versions, rollback, preview, editor)
  components/knowledge/     knowledge UI (collections, uploads, indexing status, test search)
  components/tools/         tools UI (MCP servers, tool allowlist, try a tool)
  lib/api.ts                typed API client + SSE parser
docs/
  WORKFLOW_CODE_MAP.md      which functions run, in order, per workflow
  FUNCTION_GUIDE.md         every function explained, for learning and debugging
  architecture.md
docker-compose.yml
```
