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
| 2 | System prompts: versioned templates | Planned | `backend/app/prompts` |
| 3 | RAG: Postgres + pgvector | Planned | `backend/app/rag` |
| 4 | MCP tools | Planned | `backend/app/mcp` |
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

Needs Python 3.13 with [uv](https://docs.astral.sh/uv/), and Node 22.

**1. Backend** (http://localhost:8000, API docs at http://localhost:8000/docs)

```bash
cd backend
cp .env.example .env    # add at least one API key, or use the built-in mock/echo model
uv sync
uv run uvicorn app.main:app --reload
```

**2. Frontend** (http://localhost:3000)

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

**Or with Docker:**

```bash
docker compose up --build
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
| Mock (echo) | `ENABLE_MOCK_PROVIDER=true` (default) | `echo` |

OpenAI and Gemini model names change often. Check the provider's current model list and set
`OPENAI_MODELS` / `GEMINI_MODELS` to match.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + enabled providers |
| GET | `/v1/models` | Models you can call, and the default |
| POST | `/v1/chat` | Chat completion (JSON) |
| POST | `/v1/chat/stream` | Chat completion as server-sent events |

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

Streaming events (`/v1/chat/stream`), one JSON object per SSE `data:` line:
`start` → `delta` (text chunks) → `done` (finish reason, usage, latency), or `error`.

Errors always look like `{"error": {"code": "...", "message": "...", "provider": "..."}}`.

## Development

```bash
cd backend && uv run pytest          # tests (no API keys or network needed)
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
    llm/
      base.py               provider interface
      registry.py           provider setup + model allowlist
      providers/            anthropic, openai, gemini, ollama, mock
    prompts/ rag/ mcp/ auth/ guardrails/ cache/ routing/ observability/   (layers 2–8)
  evals/                    (layer 9)
  tests/
frontend/
  app/page.tsx              chat page
  components/chat.tsx       chat UI (model picker, system prompt, streaming)
  lib/api.ts                typed API client + SSE parser
docs/
  WORKFLOW_CODE_MAP.md      which functions run, in order, per workflow
  FUNCTION_GUIDE.md         every function explained, for learning and debugging
  architecture.md
docker-compose.yml
```
