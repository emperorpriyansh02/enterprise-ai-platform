# Function Guide

Every function in the project, explained in plain language, grouped by workflow. Use it to
learn the code and to debug it.

- Want the **order** things run in? See [WORKFLOW_CODE_MAP.md](WORKFLOW_CODE_MAP.md). The section numbers match.
- Each function uses the same template:
  - **Where / Called by / Calls**: where it lives and how it connects.
  - **Takes in**: each input, what it looks like, and where it comes from.
  - **What it does**: step by step, including any conversion.
  - **Gives back**: what comes out, with an example.
  - **Can fail with**: what goes wrong and what the caller sees.
  - **Debug tips**: what to check when it misbehaves.

> Paths are relative to the repo root. Names like `ChatService.complete` mean "the `complete`
> method of the `ChatService` class".

---

## Contents

0. [Data shapes (read this first)](#0-data-shapes-read-this-first)
1. [App startup](#1-app-startup)
2. [Every HTTP request](#2-every-http-request)
3. [Health check](#3-health-check)
4. [List models](#4-list-models)
5. [Chat, one reply](#5-chat-one-reply)
6. [Chat, streaming](#6-chat-streaming)
7. [Provider adapters](#7-provider-adapters)
8. [Errors](#8-errors)
9. [Logging](#9-logging)
10. [Shutdown](#10-shutdown)
11. [Frontend](#11-frontend)
12. [Tests](#12-tests)
13. [Prompts (Layer 2)](#13-prompts-layer-2)
14. [Knowledge bases / RAG (Layer 3)](#14-knowledge-bases--rag-layer-3)
15. [Debugging cheat sheet](#15-debugging-cheat-sheet)

---

## 0. Data shapes (read this first)

These are the "containers" data travels in. Functions below take and return them.

### Public API shapes (`backend/app/schemas/chat.py`)

What callers send and receive over HTTP.

| Shape | What it holds | Example |
|---|---|---|
| `Message` | One chat message: `role` (`"user"` or `"assistant"`) and `content` (non-empty text). | `{"role": "user", "content": "Hi"}` |
| `ChatRequest` | What the caller sends: `model` (optional `"provider/model"`), `messages` (at least one, first must be from the user), `prompt` (optional managed prompt: `{name, version?, variables}`, Layer 2), `system` (optional system prompt, or extra instructions added after `prompt`), `rag` (optional `{collections, top_k?}`: knowledge bases to search, Layer 3), `temperature` (0–2, optional), `max_tokens` (1–128,000, optional), `provider_options` (extra vendor settings, passed through). | `{"model": "openai/gpt-5", "prompt": {"name": "support-agent", "variables": {"company_name": "Acme"}}, "messages": [...]}` |
| `Usage` | Token counts: `input_tokens`, `output_tokens`. | `{"input_tokens": 12, "output_tokens": 40}` |
| `ChatResponse` | The full reply: `id`, `provider`, `model` (the one that actually answered), `content`, `finish_reason`, `usage`, `latency_ms`, `prompt` (`{name, version}` of the managed prompt used, or absent), `citations` (sources given to the model when `rag` was used; `[]` = none found). | see section 5 |
| `StreamEvent` | One streaming event: `type` (`start`/`delta`/`done`/`error`), `id`, and depending on type: `provider`, `model`, `delta` (text piece), `finish_reason`, `usage`, `latency_ms`, `prompt` (on `start`/`done`), `citations` (on `start`), `error`. | `{"type": "delta", "id": "chat_…", "delta": "Hel"}` |
| `ModelInfo` | One model you may call: `id` (`"provider/model"`), `provider`, `model`. | `{"id": "mock/echo", "provider": "mock", "model": "echo"}` |
| `ModelList` | `default` (model id or null) + `data` (list of `ModelInfo`). | see section 4 |

`finish_reason` is always one of: `stop` (finished normally), `length` (hit the token limit),
`refusal` (model declined), `content_filter` (blocked by a safety filter), `other`.

### Prompt shapes (`backend/app/prompts/schemas.py`, Layer 2)

| Shape | What it holds |
|---|---|
| `PromptRef` | The `prompt` field on a chat request: `name`, optional `version` (omit = active), `variables` (name → value). |
| `PromptUsed` | Which prompt a reply used: `name`, `version`. |
| `PromptCreate` | New prompt: `name` (lowercase words with hyphens, 2–64 chars), `description`, `template`, `note`. |
| `PromptUpdate` | `description`. |
| `PromptVersionCreate` | New version: `template`, `note`, `activate` (true = switch to it now). |
| `ActivateRequest` | `version` to make active. |
| `RenderRequest` / `RenderResponse` | Preview input (`version?`, `variables`) and output (`name`, `version`, `text`, `variables`). |
| `PromptVersionOut` | One version: `version`, `template`, `variables` (detected names), `note`, `created_by`, `created_at`, `is_active`. |
| `PromptSummary` | One prompt in a list: `name`, `description`, `active_version`, `latest_version`, `active_variables`, `archived`, `created_at`, `updated_at`. |
| `PromptDetail` | A summary plus `versions` (newest first). |
| `PromptList` | `data`: a list of `PromptSummary`. |

Database tables (`backend/app/prompts/models.py`): `prompts` (one row per prompt; `active_version` is a
version *number*) and `prompt_versions` (one row per version, never edited; unique per prompt + number).

### Knowledge-base shapes (`backend/app/rag/schemas.py`, Layer 3)

| Shape | What it holds |
|---|---|
| `RagRef` | The `rag` field on a chat request: `collections` (1–10 names), optional `top_k` (1–20). |
| `Citation` | One source given to the model: `index` (the [n] in the answer), `collection`, `document_id`, `document_title`, `chunk_id`, `position` (0 = first passage), `snippet` (first 300 characters), `score`. |
| `CollectionCreate` | `name` (lowercase words with hyphens), `description`. |
| `CollectionSummary` / `CollectionDetail` | `name`, `description`, `embedding_model` (which embedder indexed it), `documents` (counts per status), `archived`, timestamps; the detail adds `server_embedding_model` and `needs_reindex`. |
| `DocumentTextCreate` | Pasted text: `title`, `text`. |
| `DocumentOut` | One document: `id`, `title`, `filename`, `mime_type`, `size_bytes`, `status` (`pending`/`processing`/`ready`/`failed`), `error`, `chunk_count`, timestamps. |
| `UploadResult` | `data` (accepted documents) and `errors` (refused files: `filename`, `code`, `message`). |
| `SearchRequest` / `SearchResponse` | `query`, `top_k` → `hits` (each a `SearchHit`) plus the embedding model used. |
| `SearchHit` | One passage: ids, `document_title`, `collection`, `position`, `content`, `score` (merged rank score), `vector_similarity` (0–1 or null), `keyword_score` (or null). |

Database tables (`backend/app/rag/models.py`, Postgres only): `collections`, `documents` (with the
extracted text, so they can be re-indexed) and `chunks` (passage text, a 768-number `embedding`, and a
`search_vector` that Postgres fills in for keyword search).

### Internal shapes (`backend/app/llm/base.py`)

What `ChatService` and the adapters pass between each other. Callers never see these.

| Shape | What it holds |
|---|---|
| `LLMRequest` | `model` (bare model name, **no** `provider/` prefix), `messages`, `system`, `temperature`, `max_tokens`, `provider_options`. |
| `LLMResult` | `content` (reply text), `finish_reason`, `usage`, `model` (model that actually answered). |
| `StreamChunk` | Either a text piece (`delta="Hel"`) or, when `done=True`, the final info: `finish_reason`, `usage`, `model`. |

### Settings (`backend/app/core/config.py`)

`Settings` holds every config value. Each field is read from an environment variable with the same
name in capitals (field `openai_models` ← env `OPENAI_MODELS`), or from `backend/.env`. See
`backend/.env.example` for the full list.

### Error classes (`backend/app/core/errors.py`)

All share `AppError`. Each class has a fixed HTTP `status_code` and `code`. See the table in
[section 8](#8-errors).

---

## 1. App startup

#### `create_app()` · `backend/app/main.py`

- **Where:** `backend/app/main.py`. **Called by:** the module itself (`app = create_app()`), which runs when uvicorn imports `app.main`; tests call it directly. **Calls:** `get_settings`, `configure_logging`, `register_error_handlers`.
- **Takes in:**
  - `settings` (optional `Settings`): config to use. If not given, reads it with `get_settings()`. Tests pass their own.
  - `registry` (optional `ProviderRegistry`): a ready-made set of providers. If not given, one is built from settings at startup. Tests pass fake ones.
  - `database` (optional `Database`): a ready-made database connection (tests). If not given, one is created at startup when `DATABASE_URL` is set.
- **What it does:**
  1. Picks the settings (given ones, or from the environment).
  2. Turns on JSON logging at the configured level.
  3. Defines `lifespan` (startup/shutdown steps, below).
  4. Creates the FastAPI app with the app name and `lifespan`.
  5. Saves the settings on `app.state.settings`.
  6. Adds CORS, so browsers on the origins in `CORS_ORIGINS` (e.g. `http://localhost:3000`) may call the API. It also lets the browser read the `X-Request-ID` header.
  7. Adds the `request_context` middleware (request id + request log).
  8. Registers the error handler, then adds the `/health` route, the `/v1` chat routes and the `/v1/prompts` routes.
- **Gives back:** the configured `FastAPI` app object. Providers are **not** created yet; that happens when the server starts.
- **Can fail with:** a pydantic `ValidationError` from `get_settings()` if an env value has the wrong type (e.g. `REQUEST_TIMEOUT_S=abc`). The server won't start; the error names the bad field.
- **Debug tips:** settings are read when `app.main` is imported. If a change to `.env` has no effect, restart the server. `.env` is read from the folder you start uvicorn in (`backend/`).

#### `lifespan()` · `backend/app/main.py`

- **Where:** defined inside `create_app`. **Called by:** FastAPI, once at server start and once at shutdown. **Calls:** `ProviderRegistry.from_settings`, `Database.__init__`, `ProviderRegistry.aclose`, `Database.aclose`.
- **Takes in:** `app`, the FastAPI app (given by FastAPI). Also uses `settings`, `registry` and `database` from `create_app`.
- **What it does:**
  1. **Startup:** uses the given registry, or builds one from settings with `ProviderRegistry.from_settings()`, and stores it on `app.state.registry`.
  2. Uses the given database, or creates `Database(DATABASE_URL)` if that's set, or `None`. Stores it on `app.state.database` and logs `prompt store: enabled` / `disabled (no DATABASE_URL)`. No connection is opened yet, so a database that's down doesn't stop startup.
  3. Pauses (`yield`) while the server handles requests.
  3. With a database: picks the embedding provider (`build_embedder`), builds `RagRuntime` on `app.state.rag`, and starts its indexing worker (section 14). Without one, the runtime exists but reports RAG as disabled.
  4. **Shutdown:** stops the RAG worker and embedder (`RagRuntime.stop`), calls `app.state.registry.aclose()` to close all network clients, then closes the database pool, but only if `lifespan` created it (a test's database is left for the test to close).
- **Gives back:** nothing. It's a startup/shutdown hook.
- **Can fail with:** if building a provider fails (for example, an SDK rejects a setting), the server fails to start and the error is printed.
- **Debug tips:** if any request fails with `AttributeError: 'State' object has no attribute 'registry'`, startup didn't run. In tests, use `with TestClient(app)` (the `with` triggers startup).

#### `get_settings()` · `backend/app/core/config.py`

- **Where:** `backend/app/core/config.py`. **Called by:** `create_app`. **Calls:** `Settings()`, which triggers the two validators below.
- **Takes in:** nothing directly. It reads environment variables and `backend/.env`.
- **What it does:**
  1. Creates a `Settings` object. pydantic-settings fills each field from the env var with the matching name, then from `.env`, then falls back to the default in the code.
  2. Caches the result (`lru_cache`), so later calls return the same object without re-reading.
- **Gives back:** a `Settings` object, e.g. `settings.openai_models == ["gpt-5", "gpt-5-mini"]`.
- **Can fail with:** `ValidationError` for a value of the wrong type (e.g. `MAX_RETRIES=-1`, which must be ≥ 0).
- **Debug tips:** to see what was loaded, run `uv run python -c "from app.core.config import get_settings; print(get_settings())"` in `backend/`. Secret fields print as `**********`.

#### `Settings._split_csv()` · `backend/app/core/config.py`

- **Where:** a validator on `Settings`. **Called by:** pydantic, automatically, before it stores the list fields (`CORS_ORIGINS`, `ANTHROPIC_MODELS`, `ANTHROPIC_FALLBACK_MODELS`, `OPENAI_MODELS`, `GEMINI_MODELS`, `OLLAMA_MODELS`).
- **Takes in:** `value`, the raw value from the environment (usually a string like `"gpt-5, gpt-5-mini"`), or a list when the code or a test passes one.
- **What it does:**
  1. If it's a string: splits it at commas, trims spaces around each item, and drops empty items.
  2. Anything else (already a list) passes through unchanged.
- **Gives back:** `"gpt-5, gpt-5-mini,"` → `["gpt-5", "gpt-5-mini"]`.
- **Can fail with:** nothing on its own.
- **Debug tips:** model names must match exactly what the vendor expects. A typo here makes the model show up in `/v1/models`, but the vendor rejects it (400 `provider_bad_request`).

#### `Settings._empty_to_none()` · `backend/app/core/config.py`

- **Where:** a validator on `Settings`. **Called by:** pydantic, for `ANTHROPIC_EFFORT`, `OPENAI_BASE_URL`, `OLLAMA_BASE_URL`, `DATABASE_URL`, `DEFAULT_PROMPT_NAME`, `ADMIN_API_KEY`.
- **Takes in:** `value`, the raw value (e.g. `""` from a line like `OLLAMA_BASE_URL=` in `.env`).
- **What it does:** a blank or whitespace-only string becomes `None` ("not set"). Anything else passes through.
- **Gives back:** `""` → `None`; `"http://localhost:11434"` → unchanged.
- **Can fail with:** nothing.
- **Debug tips:** this rule is why copying `.env.example` (which has blank values) doesn't switch Ollama or the database on with an empty URL.

#### `ProviderRegistry.from_settings()` · `backend/app/llm/registry.py`

- **Where:** a class method on `ProviderRegistry`. **Called by:** `lifespan` at startup; tests. **Calls:** each provider's `__init__`, then `ProviderRegistry.__init__`.
- **Takes in:** `settings`, the `Settings` object.
- **What it does:**
  1. Collects the shared options: timeout (`REQUEST_TIMEOUT_S`) and retry count (`MAX_RETRIES`).
  2. Checks each provider in this order: Anthropic (if `ANTHROPIC_API_KEY`), OpenAI (if `OPENAI_API_KEY`), Gemini (if `GEMINI_API_KEY`), Ollama (if `OLLAMA_BASE_URL`), Mock (if `ENABLE_MOCK_PROVIDER`). For each one that is on, it creates the adapter with its key, its model list and its specific options. Secret keys are unwrapped with `get_secret_value()` only here.
  3. Logs `providers enabled: anthropic, mock` (or `none`).
  4. Builds the registry with those adapters and `DEFAULT_MODEL`.
- **Gives back:** a `ProviderRegistry` holding the enabled adapters.
- **Can fail with:** errors from an adapter constructor (rare; constructors don't contact the network).
- **Debug tips:** "my provider isn't showing up" → look for the `providers enabled:` log line at startup, and check the key is in `backend/.env` (not the repo root) with the exact variable name.

#### `ProviderRegistry.__init__()` · `backend/app/llm/registry.py`

- **Where:** `ProviderRegistry` constructor. **Called by:** `from_settings`; tests that build a registry by hand.
- **Takes in:** `providers` (list of adapter objects) and `default_model` (e.g. `"anthropic/claude-opus-5-5"`, or `None`).
- **What it does:** stores the adapters in a dictionary keyed by provider name (`{"anthropic": <adapter>, ...}`), and stores the default model name.
- **Gives back:** the registry object.
- **Can fail with:** nothing. If two adapters had the same name, the later one would replace the earlier one.
- **Debug tips:** the order of `providers` is the order shown in `/v1/models` and `/health`.

#### `LLMProvider.__init__()` · `backend/app/llm/base.py`

- **Where:** base class of all adapters. **Called by:** every adapter's `__init__` (via `super().__init__(models)`).
- **Takes in:** `models`, the list of model names this provider may use (from settings, e.g. `["gpt-5", "gpt-5-mini"]`).
- **What it does:** stores a copy of the list on `self.models`. This list is the **allowlist**: `ProviderRegistry.resolve` refuses any model not in it.
- **Gives back:** nothing (it's a constructor).
- **Can fail with:** nothing.
- **Debug tips:** "model is not enabled" (404) means the model isn't in this list. Add it to the provider's `*_MODELS` setting.

#### `AnthropicProvider.__init__()` · `backend/app/llm/providers/anthropic_provider.py`

- **Where:** Anthropic adapter constructor. **Called by:** `ProviderRegistry.from_settings`; tests.
- **Takes in:**
  - `api_key`: from `ANTHROPIC_API_KEY`.
  - `models`: from `ANTHROPIC_MODELS`.
  - `timeout_s`, `max_retries`: shared settings.
  - `effort`: from `ANTHROPIC_EFFORT` (e.g. `"medium"`, or `None`).
  - `refusal_fallback`: from `ANTHROPIC_REFUSAL_FALLBACK`.
  - `fallback_models`: from `ANTHROPIC_FALLBACK_MODELS`, the models that support server-side fallback.
  - `http_client`: tests only, a fake HTTP client.
- **What it does:**
  1. Stores the model list (via `LLMProvider.__init__`).
  2. Creates the official async Anthropic client with the key, timeout and retries (the SDK retries 429/5xx/connection errors itself).
  3. Remembers the effort and fallback settings for `_params`.
- **Gives back:** the adapter. No network call yet.
- **Can fail with:** nothing at this point. A wrong key only shows up on the first request (502 `provider_auth_error`).
- **Debug tips:** the SDK also reads the `ANTHROPIC_BASE_URL` env var. If calls go to the wrong host, check whether that variable is set.

#### `OpenAIProvider.__init__()` · `backend/app/llm/providers/openai_provider.py`

- **Where:** OpenAI adapter constructor. **Called by:** `ProviderRegistry.from_settings`; tests.
- **Takes in:** `api_key` (`OPENAI_API_KEY`), `models` (`OPENAI_MODELS`), `timeout_s`, `max_retries`, `base_url` (`OPENAI_BASE_URL`, optional), `http_client` (tests only).
- **What it does:** stores the model list, then creates the official async OpenAI client. If `base_url` is set, the client talks to that server instead (Azure OpenAI, vLLM, any OpenAI-compatible API).
- **Gives back:** the adapter. No network call yet.
- **Can fail with:** nothing here. A wrong key or URL shows up on the first request.
- **Debug tips:** with a custom `OPENAI_BASE_URL`, model names must match what *that* server expects.

#### `GeminiProvider.__init__()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** Gemini adapter constructor. **Called by:** `ProviderRegistry.from_settings`; tests.
- **Takes in:** `api_key` (`GEMINI_API_KEY`), `models` (`GEMINI_MODELS`), `timeout_s`, `max_retries`, `http_client` (tests only).
- **What it does:**
  1. Stores the model list.
  2. Builds Gemini's HTTP options:
     - **Timeout in milliseconds:** Gemini's SDK expects ms, so `120` seconds becomes `120000`.
     - **Attempts = `max_retries + 1`:** the SDK counts the first try as an attempt.
     - The test HTTP client, if given.
  3. Creates the `google-genai` client.
- **Gives back:** the adapter. No network call yet.
- **Can fail with:** nothing here.
- **Debug tips:** if Gemini times out too early, check the ms conversion and `REQUEST_TIMEOUT_S`.

#### `OllamaProvider.__init__()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** Ollama adapter constructor. **Called by:** `ProviderRegistry.from_settings`; tests.
- **Takes in:** `base_url` (`OLLAMA_BASE_URL`, e.g. `http://localhost:11434`), `models` (`OLLAMA_MODELS`), `timeout_s`, `http_client` (tests only).
- **What it does:** stores the model list, then uses the given HTTP client or creates a new `httpx.AsyncClient` pointed at `base_url`, with the timeout.
- **Gives back:** the adapter. No network call yet.
- **Can fail with:** nothing here.
- **Debug tips:** inside Docker Compose, the URL must be `http://ollama:11434` (the service name), not `localhost`.

#### `MockProvider.__init__()` · `backend/app/llm/providers/mock_provider.py`

- **Where:** Mock adapter constructor. **Called by:** `ProviderRegistry.from_settings`; tests.
- **Takes in:** `stream_delay_s`, the pause between streamed words (from `MOCK_STREAM_DELAY_S`; 0 in tests).
- **What it does:** registers two models, `echo` and `echo-system`, and stores the delay.
- **Gives back:** the adapter.
- **Can fail with:** nothing.
- **Debug tips:** set `ENABLE_MOCK_PROVIDER=false` in production so nobody gets echo replies.

---

## 2. Every HTTP request

#### `request_context()` · `backend/app/main.py`

- **Where:** defined inside `create_app` as HTTP middleware. **Called by:** Starlette, for **every** request, before CORS and the route. **Calls:** the next layer (`call_next`).
- **Takes in:**
  - `request`: the incoming HTTP request.
  - `call_next`: a function that runs the rest of the app (CORS, then the route) and returns the response.
- **What it does:**
  1. Reads the `X-Request-ID` header. If there isn't one, makes a random 32-character hex id.
  2. Records the start time.
  3. Runs the rest of the app and waits for the response. Error responses come back here too, already turned into JSON.
  4. Puts `X-Request-ID` on the response.
  5. Logs a `request` line with `request_id`, `method`, `path`, `status`, `latency_ms`.
- **Gives back:** the response, unchanged apart from the extra header.
- **Can fail with:** nothing of its own.
- **Debug tips:**
  - For streaming, `latency_ms` measures until the response *starts*, not until the stream ends. Use the `chat stream completed` log for full duration.
  - This id differs from the `chat_…` id in chat responses. To trace one call, send your own `X-Request-ID` header and search the logs for it.

#### `register_error_handlers()` · `backend/app/core/errors.py`

- **Where:** `backend/app/core/errors.py`. **Called by:** `create_app`, once.
- **Takes in:** `app`, the FastAPI app.
- **What it does:** tells FastAPI: "whenever an `AppError` (or any subclass) escapes a route, call `_handle_app_error`".
- **Gives back:** nothing.
- **Can fail with:** nothing.
- **Debug tips:** exceptions that are *not* `AppError` (real bugs) skip this handler and return a plain 500. Look for the Python traceback in the server output.

#### `_handle_app_error()` · `backend/app/core/errors.py`

- **Where:** defined inside `register_error_handlers`. **Called by:** FastAPI, when a route raises an `AppError`.
- **Takes in:** the request (unused), and `exc`, the error object (e.g. `ModelNotFoundError("Provider \"openai\" is not configured...")`).
- **What it does:** builds a JSON response with the error class's HTTP status and body `{"error": exc.to_dict()}`.
- **Gives back:** e.g. status 404 with `{"error": {"code": "model_not_found", "message": "..."}}`.
- **Can fail with:** nothing.
- **Debug tips:** the `code` field is stable and safe to branch on in client code; `message` is for humans.

---

## 3. Health check

#### `get_registry()` · `backend/app/api/deps.py`

- **Where:** `backend/app/api/deps.py`. **Called by:** FastAPI's dependency system, for any route that declares `Registry` (the `health` and `list_models` routes), and by `get_chat_service`.
- **Takes in:** `request`, the current HTTP request (given by FastAPI).
- **What it does:** returns `request.app.state.registry`, the registry created at startup.
- **Gives back:** the shared `ProviderRegistry`.
- **Can fail with:** `AttributeError` if startup (`lifespan`) never ran.
- **Debug tips:** there is one registry per running server, shared by all requests.

#### `health()` · `backend/app/api/v1/routes.py`

- **Where:** route `GET /health`. **Called by:** FastAPI. **Calls:** `ProviderRegistry.provider_names`, `Database.ping`.
- **Takes in:** `request` (to reach `app.state.database`) and `registry`, from `get_registry`.
- **What it does:** reports that the server is up, which providers are enabled, the database status (`disabled` without `DATABASE_URL`, `ok` if it answered `SELECT 1`, `unavailable` if it didn't), and the knowledge-base status from `RagRuntime.status` (`ok`, `unavailable`, or `disabled`).
- **Gives back:** `{"status": "ok", "providers": ["anthropic", "mock"], "database": "ok", "rag": "ok"}`.
- **Can fail with:** nothing in normal operation; a database problem shows as `"unavailable"`, not an error.
- **Debug tips:** used by Docker's health check. It does **not** contact any LLM vendor, so "ok" doesn't prove your API keys work.

#### `ProviderRegistry.provider_names()` · `backend/app/llm/registry.py`

- **Where:** a read-only property on `ProviderRegistry` (used without brackets: `registry.provider_names`). **Called by:** `health`; tests.
- **Takes in:** nothing.
- **What it does:** lists the names of the enabled providers, in registration order.
- **Gives back:** e.g. `["anthropic", "openai", "mock"]`.
- **Can fail with:** nothing.
- **Debug tips:** empty list = no keys set and mock disabled; every chat will fail with 404.

---

## 4. List models

#### `list_models()` · `backend/app/api/v1/routes.py`

- **Where:** route `GET /v1/models`. **Called by:** FastAPI (the frontend calls it on page load). **Calls:** `ProviderRegistry.default_model`, `ProviderRegistry.catalog`.
- **Takes in:** `registry`, from `get_registry`.
- **What it does:** puts the default model and the full model list into a `ModelList`.
- **Gives back:** `{"default": "mock/echo", "data": [{"id": "mock/echo", "provider": "mock", "model": "echo"}]}`.
- **Can fail with:** nothing.
- **Debug tips:** the frontend's dropdown shows exactly this list. If a model is missing in the UI, check this endpoint first.

#### `ProviderRegistry.catalog()` · `backend/app/llm/registry.py`

- **Where:** `ProviderRegistry`. **Called by:** `list_models`, `ProviderRegistry.default_model`.
- **Takes in:** nothing; uses the stored adapters.
- **What it does:** for every adapter, and every model in its allowlist, makes a `ModelInfo` with `id = "<provider>/<model>"`.
- **Gives back:** e.g. `[ModelInfo(id="openai/gpt-5", provider="openai", model="gpt-5"), ...]`.
- **Can fail with:** nothing.
- **Debug tips:** it lists what is *allowed*, not what the vendor actually has. A listed model can still fail if the vendor doesn't recognise the name.

#### `ProviderRegistry.default_model()` · `backend/app/llm/registry.py`

- **Where:** a read-only property on `ProviderRegistry` (`registry.default_model`). **Called by:** `list_models`, `ProviderRegistry.resolve`.
- **Takes in:** nothing; uses `DEFAULT_MODEL` stored at startup.
- **What it does:**
  1. Builds the list of available model ids with `catalog()`.
  2. If `DEFAULT_MODEL` is in that list, returns it.
  3. Otherwise returns the first available model, or `None` if there are no models at all.
- **Gives back:** e.g. `"anthropic/claude-opus-5-5"`, or `"mock/echo"` when Anthropic isn't configured.
- **Can fail with:** nothing.
- **Debug tips:** "why is the default `mock/echo`?" → because the configured default's provider isn't enabled.

---

## 5. Chat, one reply

#### `ChatRequest._first_message_from_user()` · `backend/app/schemas/chat.py`

- **Where:** a validator on the `ChatRequest` schema. **Called by:** pydantic, while FastAPI reads the request body.
- **Takes in:** `messages`, the list of `Message` objects from the request body (already checked to be non-empty).
- **What it does:** checks that the first message has role `user`. Every vendor requires this.
- **Gives back:** the same list, unchanged, if it's valid.
- **Can fail with:** `ValueError("the first message must have role 'user'")`. FastAPI turns it into a **422** response with a `detail` list. The route never runs.
- **Debug tips:** a 422 means the request body itself is wrong. Read `detail[].loc` (which field) and `detail[].msg` (why).

#### `get_chat_service()` · `backend/app/api/deps.py`

- **Where:** `backend/app/api/deps.py`. **Called by:** FastAPI, for the `chat` and `chat_stream` routes (via the `Service` dependency). **Calls:** `get_registry`, `ChatService.__init__`; FastAPI first runs `get_prompt_service` for it.
- **Takes in:** `request`, the current HTTP request, and `prompts`, the `PromptService` (or `None` without a database) from `get_prompt_service`.
- **What it does:** creates a new `ChatService` with the shared registry, this request's prompt service, its RAG service (from `get_rag_service`, or `None`), and the app's `RagRuntime`.
- **Gives back:** a `ChatService` object (cheap; one per request).
- **Can fail with:** same as `get_registry`.
- **Debug tips:** this is where request-scoped wiring lives; later layers (auth) will add their services here too.

#### `ChatService.__init__()` · `backend/app/services/chat_service.py`

- **Where:** `ChatService` constructor. **Called by:** `get_chat_service`.
- **Takes in:** `registry`, the `ProviderRegistry`, and `prompts`, a `PromptService` or `None` (no database).
- **What it does:** keeps them for `complete`, `stream`, `_build_system` and `_retrieve`. Also takes `rag` (a `RagService` or `None`) and `rag_runtime` (for the "is RAG usable?" check).
- **Gives back:** the service object.
- **Can fail with:** nothing.
- **Debug tips:** `prompts` is `None` exactly when `DATABASE_URL` isn't set.

#### `chat()` · `backend/app/api/v1/routes.py`

- **Where:** route `POST /v1/chat`. **Called by:** FastAPI, after the body passed validation. **Calls:** `ChatService.complete`.
- **Takes in:**
  - `body`: the validated `ChatRequest`.
  - `service`: from `get_chat_service`.
- **What it does:** passes the request to `service.complete()` and returns its result.
- **Gives back:** a `ChatResponse`, sent as JSON.
- **Can fail with:** any `AppError` from the service (404 unknown model, 429, 502, 503, 504...), turned into JSON by `_handle_app_error`.
- **Debug tips:** the route is deliberately thin. All logic lives in `ChatService.complete`.

#### `ChatService.complete()` · `backend/app/services/chat_service.py`

- **Where:** `ChatService`. **Called by:** the `chat` route. **Calls:** `ProviderRegistry.resolve`, `ChatService._build_system`, `_new_id`, `_to_llm_request`, the chosen adapter's `complete`, `_ms_since`, `_prompt_label`.
- **Takes in:** `request`, the validated `ChatRequest` from the caller.
- **What it does:**
  1. **Picks the provider:** `registry.resolve(request.model)` turns `"openai/gpt-5"` into (OpenAI adapter, `"gpt-5"`). A missing model means the default.
  2. **Builds the system prompt** with `_build_system()`: managed prompt (rendered) + the request's own `system` text. Also gives back which prompt version was used. Then `_retrieve()` appends knowledge-base sources when the request has `rag` (section 14.8), and gives back the citations.
  3. **Makes an id** like `chat_3f2a…` with `_new_id()`.
  4. **Starts a timer.**
  5. **Converts** the public request into an internal `LLMRequest` (`_to_llm_request`) and calls `provider.complete(...)`. This is where the vendor is contacted.
  6. **Measures** how long it took (`_ms_since`).
  7. **Logs** `chat completed` with request id, provider, model, prompt (e.g. `support-agent@v3`), latency, tokens and finish reason.
  8. **Builds** the `ChatResponse`. `model` comes from the vendor's answer, so after a Claude refusal fallback it shows the fallback model. `prompt` is `{name, version}` when a managed prompt was used; `citations` is set when `rag` was used. The log line also records `sources` (how many passages were added).
- **Gives back:**
  ```json
  {"id": "chat_3f2a…", "provider": "openai", "model": "gpt-5-2026-01-01",
   "content": "Hello!", "finish_reason": "stop",
   "usage": {"input_tokens": 9, "output_tokens": 3}, "latency_ms": 812}
  ```
- **Can fail with:** `ModelNotFoundError` (404) from `resolve`; prompt errors from `_build_system` (404/409/400/503, section 13); any `Provider*Error` from the adapter (see section 8). Nothing is logged as `chat completed` when it fails.
- **Debug tips:**
  - `finish_reason: "length"` → raise `max_tokens`.
  - `"refusal"` / `"content_filter"` → the vendor declined; `content` may be empty.
  - Compare `latency_ms` here with the `request` log to see time spent outside the vendor call.

#### `ProviderRegistry.resolve()` · `backend/app/llm/registry.py`

- **Where:** `ProviderRegistry`. **Called by:** `ChatService.complete`, `ChatService.stream`. **Calls:** `ProviderRegistry.default_model` (when no model is given).
- **Takes in:** `model_ref`, the `model` field from the request (e.g. `"anthropic/claude-opus-5-5"`), or `None`.
- **What it does:**
  1. If `model_ref` is empty, uses the default model. If there isn't one → error "No LLM providers are configured".
  2. Splits at the **first** `/`: `"anthropic/claude-opus-5-5"` → provider `"anthropic"`, model `"claude-opus-5-5"`. (Ollama names with more slashes keep them in the model part.)
  3. No `/`, or nothing after it → error "Model must look like provider/model".
  4. Provider not enabled → error listing the enabled providers.
  5. Model not in that provider's allowlist → error listing the enabled models.
- **Gives back:** a pair `(adapter, "claude-opus-5-5")`.
- **Can fail with:** `ModelNotFoundError` → **404** `model_not_found`. The message says exactly which rule failed.
- **Debug tips:** a 404 here never reached the vendor, so it costs nothing. Fix the model name or the settings.

#### `_new_id()` · `backend/app/services/chat_service.py`

- **Where:** module-level helper. **Called by:** `ChatService.complete`, `ChatService.stream`.
- **Takes in:** nothing.
- **What it does:** creates a random id with the `chat_` prefix.
- **Gives back:** e.g. `"chat_9b1c0e…"` (prefix + 32 hex characters).
- **Can fail with:** nothing.
- **Debug tips:** search the logs for this id (`request_id` field in the `chat completed` line).

#### `_to_llm_request()` · `backend/app/services/chat_service.py`

- **Where:** module-level helper. **Called by:** `ChatService.complete`, `ChatService.stream`.
- **Takes in:**
  - `request`: the public `ChatRequest`.
  - `model`: the bare model name from `resolve` (e.g. `"gpt-5"`).
  - `system`: the final system prompt from `_build_system` (managed prompt + extra instructions), or `None`.
- **What it does:** copies the fields into an internal `LLMRequest`, replacing `"openai/gpt-5"` with just `"gpt-5"` (the name the vendor understands) and the request's `system` with the final one.
- **Gives back:** `LLMRequest(model="gpt-5", messages=[...], system=..., temperature=..., max_tokens=..., provider_options={...})`.
- **Can fail with:** nothing.
- **Debug tips:** to see the exact system prompt a model receives, send the same request with `"model": "mock/echo-system"`.

#### `_ms_since()` · `backend/app/services/chat_service.py`

- **Where:** module-level helper. **Called by:** `ChatService.complete`, `ChatService.stream`.
- **Takes in:** `started`, a timestamp from `time.perf_counter()` taken earlier.
- **What it does:** subtracts it from the current time and converts seconds to whole milliseconds.
- **Gives back:** e.g. `812`.
- **Can fail with:** nothing.
- **Debug tips:** uses a monotonic clock, so it isn't affected by system clock changes.

---

## 6. Chat, streaming

#### `chat_stream()` · `backend/app/api/v1/routes.py`

- **Where:** route `POST /v1/chat/stream`. **Called by:** FastAPI (the frontend uses this route). **Calls:** `ChatService.stream`, `_sse`.
- **Takes in:** `body` (validated `ChatRequest`) and `service` (from `get_chat_service`).
- **What it does:**
  1. Calls `service.stream(body)`, which returns an event generator (nothing has run yet).
  2. **Pulls the first event now** (`await anext(events)`). This runs the service up to the vendor's first reply, so problems like an unknown model, a bad key or a rate limit raise an error *before* the response starts. The caller then gets a normal HTTP error status, not a 200 with a broken stream.
  3. Returns an `EventSourceResponse` (content type `text/event-stream`) that sends the first event and then the rest, via `_sse`. A `: ping` comment is sent every 15 seconds to keep proxies from closing an idle connection.
- **Gives back:** a stream like:
  ```
  data: {"type":"start","id":"chat_…","provider":"mock","model":"echo"}

  data: {"type":"delta","id":"chat_…","delta":"Echo:"}

  data: {"type":"done","id":"chat_…","finish_reason":"stop","usage":{...},"latency_ms":92}
  ```
- **Can fail with:** before the stream starts, any `AppError` → normal JSON error (404/429/502/...). After it starts → an `error` event (see `ChatService.stream`).
- **Debug tips:** test it with `curl -N -X POST localhost:8000/v1/chat/stream -H 'content-type: application/json' -d '{"messages":[{"role":"user","content":"hi"}]}'`. The `-N` flag turns off curl's buffering.

#### `ChatService.stream()` · `backend/app/services/chat_service.py`

- **Where:** `ChatService`. **Called by:** `chat_stream`. **Calls:** `ProviderRegistry.resolve`, `ChatService._build_system`, `_new_id`, `_to_llm_request`, the adapter's `stream`, `_ms_since`, `_prompt_label`, `AppError.to_dict`.
- **Takes in:** `request`, the validated `ChatRequest`.
- **What it does:**
  1. Picks the provider (`resolve`), builds the system prompt (`_build_system`), adds knowledge-base sources (`_retrieve`), makes an id, starts a timer, converts the request.
  2. Starts the adapter's stream and **waits for its first chunk**. Errors here are *raised* (not turned into events), so the route can still return an HTTP error. An adapter that yields nothing at all raises `ProviderError("Provider returned an empty stream")`.
  3. Yields `start` (id, provider, model, `prompt` if a managed prompt was used, and `citations` if `rag` was used).
  4. Loops over the adapter's chunks:
     - a text chunk → yields `delta` with the text;
     - the final chunk (`done=True`) → measures latency, logs `chat stream completed`, yields `done` with finish reason, usage, latency and `prompt`, then stops.
  5. If the adapter raises an `AppError` mid-stream, or ends without a final chunk → logs `chat stream failed` (with `error_code`), yields one `error` event with `{code, message, provider}`, and stops.
- **Gives back:** a sequence of `StreamEvent`s: `start`, `delta`…, then exactly one `done` **or** `error`. All share the same `id`.
- **Can fail with:** before `start`: `ModelNotFoundError`, prompt errors, or any `Provider*Error` (raised). After `start`: never raises; problems become the `error` event.
- **Debug tips:**
  - A stream that stops without `done` or `error` usually means the client disconnected.
  - Check the `chat stream failed` log for the `error_code`.

#### `_sse()` · `backend/app/api/v1/routes.py`

- **Where:** module-level helper in the routes file. **Called by:** the `EventSourceResponse` built in `chat_stream`.
- **Takes in:** `first`, the already-pulled first event, and `rest`, the remaining events from `ChatService.stream`.
- **What it does:** converts each `StreamEvent` into `{"data": "<json>"}`, which the SSE library writes as a `data: <json>` line plus a blank line. Empty fields are left out of the JSON (`exclude_none`).
- **Gives back:** one dictionary per event, in order.
- **Can fail with:** nothing of its own.
- **Debug tips:** the library uses `\r\n` line endings, so clients should split on blank lines with or without `\r` (the frontend's `streamChat` does).

---

## 7. Provider adapters

All adapters turn our `LLMRequest` into a vendor call, and the vendor's answer back into
`LLMResult` (one reply) or `StreamChunk`s (streaming). Vendor SDKs are used **only** inside these files.

### 7.0 The contract

#### `LLMProvider.complete()` · `backend/app/llm/base.py`

- **Where:** abstract method on the base class. **Called by:** `ChatService.complete` (through the chosen adapter).
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:** nothing here; every adapter must provide its own version.
- **Gives back:** (in adapters) an `LLMResult`.
- **Can fail with:** adapters raise `Provider*Error`s, never raw SDK exceptions.
- **Debug tips:** to add a new vendor, subclass `LLMProvider`, implement `complete`/`stream`, and register it in `ProviderRegistry.from_settings`.

#### `LLMProvider.stream()` · `backend/app/llm/base.py`

- **Where:** abstract method on the base class. **Called by:** `ChatService.stream`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:** nothing here; adapters implement it.
- **Gives back:** (in adapters) any number of text `StreamChunk`s, then **exactly one** final chunk with `done=True`.
- **Can fail with:** adapters raise `Provider*Error`s.
- **Debug tips:** if an adapter forgets the final chunk, the stream ends with an `error` event "Stream ended without a final event".

#### `LLMProvider.aclose()` · `backend/app/llm/base.py`

- **Where:** base class. **Called by:** `ProviderRegistry.aclose` at shutdown.
- **Takes in:** nothing.
- **What it does:** nothing by default. Adapters with network clients override it.
- **Gives back:** nothing.
- **Can fail with:** nothing.
- **Debug tips:** used as-is by `MockProvider`.

### 7.1 Anthropic Claude

#### `AnthropicProvider._params()` · `backend/app/llm/providers/anthropic_provider.py`

- **Where:** Anthropic adapter. **Called by:** `AnthropicProvider.complete`, `AnthropicProvider.stream`.
- **Takes in:** `request` (`LLMRequest`) and `stream` (true for streaming).
- **What it does:** builds the keyword arguments for the SDK call:
  1. `model`: the bare model name.
  2. `max_tokens`: the request's value, or 16,000 (one reply) / 64,000 (streaming). Claude requires this field.
  3. `messages`: our messages as `{"role", "content"}` dictionaries (same roles Claude uses).
  4. `system`: only if the request has one. Claude takes the system prompt as a separate field, not as a message.
  5. `output_config: {"effort": ...}`: only if `ANTHROPIC_EFFORT` is set. This controls how hard the model thinks.
  6. Refusal fallback: if on, and the model is in `ANTHROPIC_FALLBACK_MODELS`, adds `fallbacks: "default"` and the beta flag `server-side-fallback-2026-07-01`. If Claude's safety system declines, Anthropic retries on its recommended fallback model in the same call.
  7. `temperature` is **dropped**, because current Claude models reject it (a debug log notes this).
  8. `provider_options` are merged in last, so they can add or override anything.
- **Gives back:** a dictionary, e.g. `{"model": "claude-opus-5-5", "max_tokens": 16000, "messages": [...], "system": "...", "output_config": {"effort": "medium"}, "betas": [...], "fallbacks": "default"}`.
- **Can fail with:** nothing here; a bad value is rejected by the API later (400).
- **Debug tips:** a 400 after adding `provider_options` usually means a misspelled or unsupported field.

#### `AnthropicProvider.complete()` · `backend/app/llm/providers/anthropic_provider.py`

- **Where:** Anthropic adapter. **Called by:** `ChatService.complete`. **Calls:** `_params`, the SDK's `client.beta.messages.create`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Builds the arguments with `_params(stream=False)` and calls the Anthropic API.
  2. Claude's reply is a list of blocks (thinking, text, and so on). It **keeps only text blocks** and joins them into one string.
  3. Converts Claude's `stop_reason` into our `finish_reason`: `end_turn`/`stop_sequence` → `stop`, `max_tokens`/`model_context_window_exceeded` → `length`, `refusal` → `refusal`, anything else → `other`.
  4. Copies `input_tokens`/`output_tokens` into `Usage`.
  5. Uses `message.model` (after a fallback, this is the model that actually answered).
- **Gives back:** `LLMResult(content="Hi there", finish_reason="stop", usage=Usage(12, 4), model="claude-opus-5-5")`.
- **Can fail with:** any SDK error → `_map_error` → our error class.
- **Debug tips:** empty `content` with `finish_reason="refusal"` means Claude (and any fallback) declined. Thinking tokens are counted in `output_tokens` even though thinking text isn't returned.

#### `AnthropicProvider.stream()` · `backend/app/llm/providers/anthropic_provider.py`

- **Where:** Anthropic adapter. **Called by:** `ChatService.stream`. **Calls:** `_params`, the SDK's `client.beta.messages.stream`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Opens a stream with `_params(stream=True)`.
  2. For each piece of reply text the SDK gives (`text_stream`), yields `StreamChunk(delta=text)`. Thinking is not streamed.
  3. When the stream ends, asks the SDK for the final message, then yields the final chunk: stop reason (mapped as in `complete`), token usage, model.
- **Gives back:** text chunks, then one `StreamChunk(done=True, finish_reason=..., usage=..., model=...)`.
- **Can fail with:** SDK errors → `_map_error`. Errors when opening the stream reach `ChatService` before the first chunk, and become HTTP errors.
- **Debug tips:** a long pause before the first text is normal: the model may think first.

#### `AnthropicProvider.aclose()` · `backend/app/llm/providers/anthropic_provider.py`

- **Where:** Anthropic adapter. **Called by:** `ProviderRegistry.aclose`.
- **Takes in:** nothing.
- **What it does:** closes the SDK client's network connections.
- **Gives back:** nothing.
- **Can fail with:** errors are caught and logged by `ProviderRegistry.aclose`.
- **Debug tips:** nothing to debug in normal use.

#### `_map_error()` · `backend/app/llm/providers/anthropic_provider.py`

- **Where:** module-level helper in the Anthropic adapter. **Called by:** `AnthropicProvider.complete`, `AnthropicProvider.stream`.
- **Takes in:** `exc`, an exception from the Anthropic SDK.
- **What it does:** picks our error class, checking the most specific case first:
  1. Timeout (`APITimeoutError`, `DeadlineExceededError`) → `ProviderTimeoutError` (504).
  2. Connection problem (`APIConnectionError`) → `ProviderUnavailableError` (503).
  3. Bad key or no permission (401/403) → `ProviderAuthError` (502).
  4. Rate limit (429) → `ProviderRateLimitError` (429).
  5. Other HTTP errors: 5xx/overloaded → `ProviderUnavailableError` (503); 4xx → `ProviderBadRequestError` (400), including Anthropic's own message.
  6. Anything else → `ProviderError` (502).
- **Gives back:** the error object (the caller raises it).
- **Can fail with:** nothing.
- **Debug tips:**
  - The original SDK exception is kept as the cause (`raise ... from exc`) and shows up in tracebacks.
  - 502 `provider_auth_error` → check `ANTHROPIC_API_KEY`.

### 7.2 OpenAI

#### `OpenAIProvider._params()` · `backend/app/llm/providers/openai_provider.py`

- **Where:** OpenAI adapter. **Called by:** `OpenAIProvider.complete`, `OpenAIProvider.stream`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Builds the message list. OpenAI takes the system prompt **as the first message** (`{"role": "system", ...}`), followed by our messages.
  2. Sets `model`.
  3. Converts `max_tokens` → `max_completion_tokens` (the field newer OpenAI models require), only if set.
  4. Adds `temperature` only if set. Some OpenAI reasoning models reject it; leave it unset for those.
  5. Merges `provider_options` (e.g. `{"reasoning_effort": "low"}`).
- **Gives back:** e.g. `{"model": "gpt-5", "messages": [{"role": "system", ...}, {"role": "user", ...}], "max_completion_tokens": 50}`.
- **Can fail with:** nothing here.
- **Debug tips:** 400 "Unsupported parameter: temperature" → don't send `temperature` to that model.

#### `OpenAIProvider.complete()` · `backend/app/llm/providers/openai_provider.py`

- **Where:** OpenAI adapter. **Called by:** `ChatService.complete`. **Calls:** `_params`, `client.chat.completions.create`, `_usage`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Calls the Chat Completions API.
  2. Takes the first choice.
  3. If the model refused (`message.refusal` is set): `finish_reason = "refusal"` and `content` = the refusal text.
  4. Otherwise `content` = the message text; `finish_reason` mapping: `stop` → `stop`, `length` → `length`, `content_filter` → `content_filter`, others (e.g. `tool_calls`) → `other`.
  5. Token usage via `_usage`; `model` = the exact model version OpenAI reports (e.g. `gpt-5-2026-01-01`).
- **Gives back:** `LLMResult(content="Hi there", finish_reason="stop", usage=Usage(7, 3), model="gpt-5-2026-01-01")`.
- **Can fail with:** SDK errors → `_map_error`.
- **Debug tips:** the reported `model` may carry a date suffix; that's normal.

#### `OpenAIProvider.stream()` · `backend/app/llm/providers/openai_provider.py`

- **Where:** OpenAI adapter. **Called by:** `ChatService.stream`. **Calls:** `_params`, `client.chat.completions.create(stream=True)`, `_usage`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Calls the API with `stream=True` and `stream_options.include_usage=True`, so the last chunk carries token counts.
  2. For each chunk:
     1. Updates the model name.
     2. If the chunk has usage, saves it with `_usage`.
     3. For each choice: yields any text as `StreamChunk(delta=...)`.
     4. Refusal text is yielded too, and marks the finish reason `refusal`.
     5. Remembers the finish reason.
  3. After the last chunk, yields the final chunk with finish reason, usage, model.
- **Gives back:** text chunks, then one final `StreamChunk(done=True, ...)`.
- **Can fail with:** SDK errors → `_map_error`.
- **Debug tips:** usage `0/0` in `done` means the server didn't send the usage chunk. Some OpenAI-compatible servers ignore `include_usage`.

#### `OpenAIProvider.aclose()` · `backend/app/llm/providers/openai_provider.py`

- **Where:** OpenAI adapter. **Called by:** `ProviderRegistry.aclose`.
- **Takes in:** nothing. **What it does:** closes the SDK client. **Gives back:** nothing.
- **Can fail with:** errors are caught by `ProviderRegistry.aclose`.
- **Debug tips:** nothing to debug in normal use.

#### `_usage()` · `backend/app/llm/providers/openai_provider.py`

- **Where:** module-level helper in the OpenAI adapter. **Called by:** `OpenAIProvider.complete`, `OpenAIProvider.stream`.
- **Takes in:** `usage`, OpenAI's usage object (`prompt_tokens`, `completion_tokens`), or `None`.
- **What it does:** renames the fields: `prompt_tokens` → `input_tokens`, `completion_tokens` → `output_tokens`. Missing values count as 0.
- **Gives back:** `Usage(input_tokens=7, output_tokens=3)`; `None` → `Usage(0, 0)`.
- **Can fail with:** nothing.
- **Debug tips:** OpenAI's reasoning tokens are included in `completion_tokens`.

#### `_map_error()` · `backend/app/llm/providers/openai_provider.py`

- **Where:** module-level helper in the OpenAI adapter. **Called by:** `OpenAIProvider.complete`, `OpenAIProvider.stream`.
- **Takes in:** `exc`, an exception from the OpenAI SDK.
- **What it does:** same order as the Anthropic version:
  1. Timeout → `ProviderTimeoutError` (504).
  2. Connection error → `ProviderUnavailableError` (503).
  3. 401/403 → `ProviderAuthError` (502).
  4. 429 → `ProviderRateLimitError` (429).
  5. Other HTTP errors: 5xx → `ProviderUnavailableError` (503); other 4xx → `ProviderBadRequestError` (400) with OpenAI's message.
  6. Anything else → `ProviderError` (502).
- **Gives back:** the error object.
- **Can fail with:** nothing.
- **Debug tips:** a 429 can also mean "out of credits" on OpenAI. Read the message.

### 7.3 Google Gemini

#### `GeminiProvider._params()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** Gemini adapter. **Called by:** `GeminiProvider.complete`, `GeminiProvider.stream`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Converts each message to a Gemini `Content` object. Role `assistant` becomes **`model`** (Gemini's word for it); `user` stays `user`. The text goes into one `Part`.
  2. Builds the generation config:
     - `system_instruction` ← system prompt;
     - `temperature` ← temperature;
     - `max_output_tokens` ← `max_tokens`.
     Each is set only when given.
  3. Merges `provider_options` **into the config** (e.g. `{"top_p": 0.9}`). Unlike the other adapters, they don't go at the top level.
- **Gives back:** `{"model": "gemini-flash-latest", "contents": [Content(role="user", ...)], "config": GenerateContentConfig(...)}`.
- **Can fail with:** a pydantic error if `provider_options` contains a field `GenerateContentConfig` doesn't know.
- **Debug tips:** Gemini needs turns to alternate. Two user messages in a row are allowed but may behave oddly.

#### `GeminiProvider.complete()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** Gemini adapter. **Called by:** `ChatService.complete`. **Calls:** `_params`, `client.aio.models.generate_content`, `_text`, `_finish_reason`, `_usage`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Calls Gemini.
  2. Extracts the reply text (`_text`), the finish reason (`_finish_reason`, or `other` if none) and usage (`_usage`).
  3. Uses `model_version` from the reply as the model name (falls back to the requested name).
- **Gives back:** `LLMResult(content="Hi there", finish_reason="stop", usage=Usage(5, 5), model="gemini-flash-latest")`.
- **Can fail with:** any exception → `_map_error`.
- **Debug tips:** an empty reply with `content_filter` means Gemini's safety filter blocked the prompt or the answer.

#### `GeminiProvider.stream()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** Gemini adapter. **Called by:** `ChatService.stream`. **Calls:** `_params`, `client.aio.models.generate_content_stream`, `_text`, `_finish_reason`, `_usage`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Opens the stream.
  2. For each chunk:
     1. Yields its text (via `_text`), if any.
     2. Remembers the latest finish reason (`_finish_reason`).
     3. Remembers the latest usage (`_usage`, if present).
     4. Remembers the latest model version.
  3. Yields the final chunk with those values.
- **Gives back:** text chunks, then one final `StreamChunk(done=True, ...)`.
- **Can fail with:** any exception → `_map_error`.
- **Debug tips:** Gemini often sends usage only on the last chunk; earlier chunks don't overwrite it with zeros.

#### `GeminiProvider.aclose()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** Gemini adapter. **Called by:** `ProviderRegistry.aclose`.
- **Takes in:** nothing. **What it does:** closes the async Gemini client (`client.aio.aclose()`). **Gives back:** nothing.
- **Can fail with:** errors are caught by `ProviderRegistry.aclose`.
- **Debug tips:** nothing to debug in normal use.

#### `_text()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** module-level helper in the Gemini adapter. **Called by:** `GeminiProvider.complete`, `GeminiProvider.stream`.
- **Takes in:** `response`, a Gemini `GenerateContentResponse` (a full reply or one stream chunk).
- **What it does:**
  1. Looks at the first candidate's parts.
  2. Joins the text of every part that has text and is **not** a "thought" (thought summaries are skipped).
  3. No candidates or no content → empty string.
- **Gives back:** e.g. `"Hi there"` or `""`.
- **Can fail with:** nothing.
- **Debug tips:** if replies are empty but tokens were used, check whether the model only returned thought parts or a blocked candidate.

#### `_finish_reason()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** module-level helper in the Gemini adapter. **Called by:** `GeminiProvider.complete`, `GeminiProvider.stream`.
- **Takes in:** `response`, a Gemini reply or chunk.
- **What it does:**
  1. If Gemini blocked the **prompt** (`prompt_feedback.block_reason`) → `content_filter`.
  2. Else reads the first candidate's finish reason and maps it:
     - `STOP` → `stop`;
     - `MAX_TOKENS` → `length`;
     - `SAFETY`, `RECITATION`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII` → `content_filter`;
     - anything else → `other`.
  3. No finish reason yet (common in middle stream chunks) → `None`.
- **Gives back:** a `finish_reason` string or `None`.
- **Can fail with:** nothing.
- **Debug tips:** `RECITATION` means Gemini stopped because the answer copied known text too closely.

#### `_usage()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** module-level helper in the Gemini adapter. **Called by:** `GeminiProvider.complete`, `GeminiProvider.stream`.
- **Takes in:** `response`, a Gemini reply or chunk.
- **What it does:** reads `usage_metadata`:
  - `input_tokens` = `prompt_token_count`;
  - `output_tokens` = `candidates_token_count` **plus** `thoughts_token_count` (thinking is billed as output, same as the other vendors).
  - Missing metadata → zeros.
- **Gives back:** e.g. `Usage(input_tokens=5, output_tokens=5)`.
- **Can fail with:** nothing.
- **Debug tips:** output tokens higher than the visible reply suggests = thinking tokens.

#### `_map_error()` · `backend/app/llm/providers/gemini_provider.py`

- **Where:** module-level helper in the Gemini adapter. **Called by:** `GeminiProvider.complete`, `GeminiProvider.stream`.
- **Takes in:** `exc`, any exception (Gemini's SDK can raise several kinds).
- **What it does:**
  1. Already one of ours → returned as is.
  2. Timeout (from `httpx` or `httpx2`) → `ProviderTimeoutError` (504).
  3. Network/transport error → `ProviderUnavailableError` (503).
  4. Gemini `APIError` with an HTTP code:
     - 401/403 → `ProviderAuthError` (502);
     - 429 → `ProviderRateLimitError` (429);
     - 5xx → `ProviderUnavailableError` (503);
     - other → `ProviderBadRequestError` (400) with Gemini's message.
  5. Anything else → `ProviderError` (502).
- **Gives back:** the error object.
- **Can fail with:** nothing.
- **Debug tips:** a 400 with "model not found" → fix the name in `GEMINI_MODELS`.

### 7.4 Ollama (local models)

#### `OllamaProvider._payload()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** Ollama adapter. **Called by:** `OllamaProvider.complete`, `OllamaProvider.stream`.
- **Takes in:** `request` (`LLMRequest`) and `stream` (true/false).
- **What it does:**
  1. Builds the message list with the system prompt as the first message (role `system`).
  2. Options: `temperature` → `options.temperature`; `max_tokens` → `options.num_predict` (Ollama's name).
  3. Sets `model` and `stream`.
  4. Merges `provider_options` at the top level (e.g. `{"keep_alive": "10m"}`).
- **Gives back:** e.g. `{"model": "llama3.2", "messages": [...], "stream": false, "options": {"num_predict": 20}}`.
- **Can fail with:** nothing.
- **Debug tips:** start the Ollama server with `OLLAMA_DEBUG=1` for more detailed logs on its side.

#### `OllamaProvider.complete()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** Ollama adapter. **Called by:** `ChatService.complete`. **Calls:** `_payload`, `_raise_for_status`, `_usage`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Sends `POST /api/chat` with the payload.
  2. Checks the status with `_raise_for_status`.
  3. Reads the JSON reply: `content` from `message.content`; `done_reason` mapped (`stop` → `stop`, `length` → `length`, else `other`); usage via `_usage`; `model` from the reply.
- **Gives back:** `LLMResult(content="Hi there", finish_reason="stop", usage=Usage(8, 2), model="llama3.2")`.
- **Can fail with:**
  - HTTP status problems → raised by `_raise_for_status`.
  - Network problems → `_map_error`.
- **Debug tips:** the first call after starting Ollama can be slow while the model loads into memory. Raise `REQUEST_TIMEOUT_S` if it times out.

#### `OllamaProvider.stream()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** Ollama adapter. **Called by:** `ChatService.stream`. **Calls:** `_payload`, `_raise_for_status`, `_usage`, `_map_error`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Opens a streaming `POST /api/chat`. If the status is 400 or higher, reads the body and raises via `_raise_for_status`.
  2. Reads the reply **line by line**. Ollama sends one JSON object per line (NDJSON):
     1. Blank line → skipped.
     2. A line with `"error"` → `ProviderError`.
     3. A line with text in `message.content` → yields it as a delta.
     4. A line with `"done": true` → yields the final chunk (finish reason, usage via `_usage`, model) and stops.
  3. If the connection ends without a `done` line → `ProviderUnavailableError("Ollama stream ended unexpectedly")`.
- **Gives back:** text chunks, then one final `StreamChunk(done=True, ...)`.
- **Can fail with:** as above, plus network errors → `_map_error`.
- **Debug tips:** "stream ended unexpectedly" usually means Ollama crashed or ran out of memory. Check the Ollama logs.

#### `OllamaProvider.aclose()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** Ollama adapter. **Called by:** `ProviderRegistry.aclose`.
- **Takes in:** nothing. **What it does:** closes the `httpx` client. **Gives back:** nothing.
- **Can fail with:** errors are caught by `ProviderRegistry.aclose`.
- **Debug tips:** nothing to debug in normal use.

#### `_usage()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** module-level helper in the Ollama adapter. **Called by:** `OllamaProvider.complete`, `OllamaProvider.stream`.
- **Takes in:** `data`, Ollama's JSON reply (a dictionary).
- **What it does:** `prompt_eval_count` → `input_tokens`, `eval_count` → `output_tokens`. Missing or null values → 0.
- **Gives back:** `Usage(input_tokens=8, output_tokens=2)`.
- **Can fail with:** nothing.
- **Debug tips:** Ollama may leave `prompt_eval_count` out when it reuses a cached prompt, so input tokens can show 0.

#### `_raise_for_status()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** module-level helper in the Ollama adapter. **Called by:** `OllamaProvider.complete`, `OllamaProvider.stream`.
- **Takes in:** `response`, the HTTP response from Ollama.
- **What it does:**
  1. Status below 400 → does nothing.
  2. Otherwise reads the error text (the `error` field of the JSON, or the raw body), then:
     - 404 → `ProviderBadRequestError` with the hint "Pull it first with `ollama pull <model>`";
     - 500 or higher → `ProviderUnavailableError`;
     - other 4xx → `ProviderBadRequestError`.
- **Gives back:** nothing, or raises.
- **Can fail with:** the errors listed above.
- **Debug tips:** a 404 almost always means the model isn't downloaded. Run `ollama pull <name>` on the Ollama machine.

#### `_map_error()` · `backend/app/llm/providers/ollama_provider.py`

- **Where:** module-level helper in the Ollama adapter. **Called by:** `OllamaProvider.complete`, `OllamaProvider.stream`.
- **Takes in:** `exc`, an `httpx` network error.
- **What it does:** a timeout → `ProviderTimeoutError` (504); any other network error (e.g. connection refused) → `ProviderUnavailableError` (503), with the original error text.
- **Gives back:** the error object.
- **Can fail with:** nothing.
- **Debug tips:** "connection refused" → Ollama isn't running, or `OLLAMA_BASE_URL` is wrong.

### 7.5 Mock (echo)

#### `MockProvider._reply()` · `backend/app/llm/providers/mock_provider.py`

- **Where:** Mock adapter (static method). **Called by:** `MockProvider.complete`, `MockProvider.stream`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  - model `echo`: finds the **last** user message and puts `"Echo: "` in front of it;
  - model `echo-system`: returns the system prompt it received, or `"(no system prompt)"`.
- **Gives back:** e.g. `"Echo: hello world"`, or the full system prompt.
- **Can fail with:** nothing (validation guarantees there's at least one user message).
- **Debug tips:** use `mock/echo-system` to see exactly what system prompt (managed prompt + extra instructions) reaches a model.

#### `MockProvider._usage()` · `backend/app/llm/providers/mock_provider.py`

- **Where:** Mock adapter (static method). **Called by:** `MockProvider.complete`, `MockProvider.stream`.
- **Takes in:** `request` and `reply` (the echo text).
- **What it does:** fakes token counts by counting words. Input = words in the system prompt plus all messages; output = words in the reply.
- **Gives back:** e.g. `Usage(input_tokens=4, output_tokens=3)`.
- **Can fail with:** nothing.
- **Debug tips:** these numbers are for UI testing only; they don't match any real tokenizer.

#### `MockProvider.complete()` · `backend/app/llm/providers/mock_provider.py`

- **Where:** Mock adapter. **Called by:** `ChatService.complete`. **Calls:** `_reply`, `_usage`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:** builds the echo reply and fake usage, with `finish_reason="stop"`.
- **Gives back:** `LLMResult(content="Echo: hello world", finish_reason="stop", usage=..., model="echo")`.
- **Can fail with:** nothing.
- **Debug tips:** use `mock/echo` to tell "my app is broken" apart from "the vendor is broken".

#### `MockProvider.stream()` · `backend/app/llm/providers/mock_provider.py`

- **Where:** Mock adapter. **Called by:** `ChatService.stream`. **Calls:** `_reply`, `_usage`.
- **Takes in:** `request`, an `LLMRequest`.
- **What it does:**
  1. Splits the echo reply into words.
  2. Yields them one at a time, with a space before each word after the first, waiting `MOCK_STREAM_DELAY_S` between words.
  3. Yields the final chunk (`stop`, fake usage).
- **Gives back:** `"Echo:"`, `" hello"`, `" world"`, then the final chunk.
- **Can fail with:** nothing.
- **Debug tips:** raise `MOCK_STREAM_DELAY_S` to test the UI's streaming and Stop button by eye.

---

## 8. Errors

#### `AppError.__init__()` · `backend/app/core/errors.py`

- **Where:** base error class; every error in section 8's table inherits it. **Called by:** any code that raises one, e.g. `raise ModelNotFoundError("...")`.
- **Takes in:**
  - `message`: a human-readable explanation;
  - `provider` (optional): which vendor was involved, e.g. `"openai"`.
- **What it does:** stores the message and provider. The HTTP status and `code` come from the class itself (e.g. `ProviderRateLimitError` is always 429 / `provider_rate_limited`).
- **Gives back:** the error object.
- **Can fail with:** nothing.
- **Debug tips:** to add a new error type, subclass `AppError` (or `ProviderError`) and set `status_code` and `code`.

#### `AppError.to_dict()` · `backend/app/core/errors.py`

- **Where:** base error class. **Called by:** `_handle_app_error` (HTTP errors), `ChatService.stream` (stream `error` events).
- **Takes in:** nothing; uses the error's own fields.
- **What it does:** builds the error body. `provider` is included only when set.
- **Gives back:** `{"code": "provider_rate_limited", "message": "OpenAI rate limit hit", "provider": "openai"}`.
- **Can fail with:** nothing.
- **Debug tips:** HTTP errors and stream errors share this exact shape, so clients handle them the same way.

| Class | HTTP | `code` | Typical cause |
|---|---|---|---|
| `ModelNotFoundError` | 404 | `model_not_found` | Wrong model name, provider not configured, model not in the allowlist |
| `ProviderBadRequestError` | 400 | `provider_bad_request` | Vendor rejected a parameter, unknown model upstream, prompt too long |
| `ProviderRateLimitError` | 429 | `provider_rate_limited` | Too many requests, or quota used up |
| `ProviderAuthError` | 502 | `provider_auth_error` | Wrong or expired API key (a server config problem, not the caller's) |
| `ProviderError` | 502 | `provider_error` | Anything else from the vendor |
| `ProviderUnavailableError` | 503 | `provider_unavailable` | Network down, vendor 5xx or overloaded, Ollama not running |
| `ProviderTimeoutError` | 504 | `provider_timeout` | No answer within `REQUEST_TIMEOUT_S` (after retries) |

---

## 9. Logging

#### `configure_logging()` · `backend/app/core/logging.py`

- **Where:** `backend/app/core/logging.py`. **Called by:** `create_app`.
- **Takes in:** `level`, from `LOG_LEVEL` (e.g. `"INFO"`, `"DEBUG"`).
- **What it does:**
  1. Replaces the root logger's handlers with a single stdout handler using `JsonFormatter`.
  2. Sets the log level.
  3. Quiets uvicorn's own access log (our middleware logs each request instead).
- **Gives back:** nothing.
- **Can fail with:** `ValueError` for an unknown level name.
- **Debug tips:** set `LOG_LEVEL=DEBUG` to also see adapter debug lines (e.g. "dropping temperature").

#### `JsonFormatter.format()` · `backend/app/core/logging.py`

- **Where:** `JsonFormatter` class. **Called by:** Python's logging system, for every log line.
- **Takes in:** `record`, a log record (message, level, logger name, and any `extra={...}` fields).
- **What it does:**
  1. Builds a dictionary with `ts` (UTC time), `level`, `logger`, `msg`.
  2. Copies the known extra fields if present: `request_id`, `method`, `path`, `status`, `latency_ms`, `provider`, `model`, `input_tokens`, `output_tokens`, `finish_reason`, `error_code`, `prompt`, `sources`.
  3. Adds the traceback (`exc`) if an exception was logged.
  4. Converts it all to one JSON line (values it can't convert become strings).
- **Gives back:** e.g. `{"ts": "2026-11-02T05:56:02.207+00:00", "level": "INFO", "logger": "app.services.chat_service", "msg": "chat stream completed", "request_id": "chat_…", "provider": "mock", ...}`.
- **Can fail with:** nothing in practice.
- **Debug tips:** new extra fields must be added to `_EXTRA_FIELDS` in this file, or they're silently left out.

---

## 10. Shutdown

#### `ProviderRegistry.aclose()` · `backend/app/llm/registry.py`

- **Where:** `ProviderRegistry`. **Called by:** `lifespan`, at shutdown. **Calls:** each adapter's `aclose`.
- **Takes in:** nothing.
- **What it does:** closes every adapter in turn. If one fails, logs `failed to close provider <name>` with the traceback and carries on with the rest.
- **Gives back:** nothing.
- **Can fail with:** never raises.
- **Debug tips:** warnings about unclosed connections at shutdown point to an adapter whose `aclose` didn't run.

(Each adapter's own `aclose` is described in section 7. `lifespan` then closes the database pool with `Database.aclose`, section 13.)

---

## 11. Frontend

### 11.1 Page load

#### `RootLayout()` · `frontend/app/layout.tsx`

- **Where:** Next.js root layout. **Called by:** Next.js, for every page.
- **Takes in:** `children`, the page content (here, `Home`).
- **What it does:** renders `<html>` and `<body>`, loads the Geist fonts as CSS variables, sets the page title "Enterprise AI Platform", and puts the `NavBar` above every page. The body fills the screen height; each page fills the space under the bar.
- **Gives back:** the page shell (JSX).
- **Can fail with:** a build error if the fonts can't be downloaded at build time (no internet).
- **Debug tips:** global colours live in `frontend/app/globals.css` (light and dark).

#### `Home()` · `frontend/app/page.tsx`

- **Where:** the `/` page. **Called by:** Next.js. **Calls:** `Chat`.
- **Takes in:** nothing.
- **What it does:** renders the chat component.
- **Gives back:** `<Chat />`.
- **Can fail with:** nothing.
- **Debug tips:** a server component; all interactivity lives in `Chat` (a client component).

#### `Chat()` · `frontend/components/chat.tsx`

- **Where:** the chat UI (marked `"use client"`, so it runs in the browser). **Called by:** `Home`. **Calls:** `getModels`, `send`, `stop`, `newChat`, `updateTurn`.
- **Takes in:** nothing. It keeps its own state:
  - `models` and `model`: the dropdown's options and choice;
  - `loadError`;
  - `system`: the system prompt box;
  - `showSettings`;
  - `turns`: the messages on screen;
  - `input`: the text box;
  - `busy`: a reply is in progress;
  - `prompts`, `promptsAvailable`, `promptName`, `promptVars`: the Prompt dropdown's options, whether the prompt store is reachable, the chosen prompt, and the values typed for its variables.
  - `collections`, `ragNames`, `showKnowledge`: the knowledge bases you can search, the ones ticked for this chat, and whether the picker row is open.
- **What it does:**
  1. **Load-models effect** (runs once, after the first render):
     1. Calls `getModels()`.
     2. Success → fills the dropdown and selects the server's default, or the first model.
     3. Failure → shows the server's error message, or "Can't reach the backend. Is it running on port 8000?" for network/CORS failures.
     4. If the page closes mid-request, the request is cancelled and the cancel error is ignored.
  2. **Load-prompts effect** (runs once): calls `listPrompts()`. Success → fills the Prompt dropdown. Failure (e.g. no database) → the dropdown shows "Unavailable"; chat works without it.
  3. **Load-collections effect** (runs once): calls `listCollections()`. If there are any, a **Knowledge** button appears; it opens a row of checkboxes, one per collection, with its ready-document count. Ticked names are kept in `ragNames` (names that no longer exist are dropped). Failure → no button; chat works without it.
  4. Works out `selectedPrompt` and `missingVars` (variables of the chosen prompt that are still empty) on every render.
  5. **Auto-scroll effect** (runs whenever `turns` changes): scrolls the newest message into view.
  6. Renders the header (Prompt dropdown, Model dropdown, "System prompt"/"Extra instructions", "Knowledge", "New chat"), a row of variable inputs when the chosen prompt has variables, the optional instructions box, an error banner, the message list (user bubbles on the right, assistant replies on the left), and the composer. When variables are missing, Send is disabled and the hint under the box names them.
  7. Under each finished reply: `provider/model · prompt name vN · N in / N out · 1.23s`, plus a note for `length`, `refusal`, `content_filter` or "Stopped."
- **Gives back:** the chat screen (JSX).
- **Can fail with:** nothing thrown; errors are shown on screen.
- **Debug tips:**
  - Open the browser console. A CORS error means the page's origin isn't in the backend's `CORS_ORIGINS`.
  - The Network tab shows `/v1/chat/stream` arriving piece by piece.

#### `getModels()` · `frontend/lib/api.ts`

- **Where:** API client. **Called by:** the load-models effect in `Chat`. **Calls:** `apiRequest`.
- **Takes in:** `signal` (optional), used to cancel the request.
- **What it does:** sends `GET {API_URL}/v1/models` through `apiRequest`. `API_URL` comes from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`), fixed at build time.
- **Gives back:** the `ModelList` JSON: `{default, data: [...]}`.
- **Can fail with:**
  - an `ApiError` for HTTP errors;
  - a `TypeError` ("Failed to fetch") for network or CORS problems;
  - an `AbortError` when cancelled.
- **Debug tips:** after changing `NEXT_PUBLIC_API_URL`, rebuild or restart the frontend; the value is baked in.

#### `toApiError()` · `frontend/lib/api.ts`

- **Where:** API client (internal). **Called by:** `apiRequest`, `streamChat`.
- **Takes in:** `response`, a failed `fetch` response.
- **What it does:** tries to read the JSON error body:
  1. Our format `{"error": {"message", "code"}}` → `ApiError(message, status, code)`.
  2. FastAPI validation format `{"detail": [{"msg": ...}]}` → `ApiError` whose message is the joined `msg`s, with code `validation_error`.
  3. Unreadable body → `ApiError("Request failed with status 500", 500)`.
- **Gives back:** an `ApiError` (the caller throws it).
- **Can fail with:** nothing.
- **Debug tips:** the `code` matches the backend error codes in section 8.

#### `ApiError` · `frontend/lib/api.ts`

- **Where:** API client (an `Error` class). **Created by:** `toApiError`, `streamChat`.
- **Takes in:** `message`, `status` (HTTP status), `code` (optional backend error code).
- **What it does:** a normal JavaScript error that also carries the status and code, so the UI can show the server's message.
- **Gives back:** the error object.
- **Can fail with:** nothing.
- **Debug tips:** check `err instanceof ApiError` to tell server errors from network errors.

### 11.2 Sending a message

#### `send()` · `frontend/components/chat.tsx`

- **Where:** inside `Chat`. **Called by:** Enter key in the message box, or the Send button. **Calls:** `nextKey`, `streamChat`, `updateTurn`.
- **Takes in:** nothing directly; reads `input`, `busy`, `model`, `system`, `turns`, `selectedPrompt`, `promptVars` and `missingVars` from the component's state.
- **What it does:**
  1. Trims the input. Stops early if it's empty, a reply is already running, no model is selected, or the chosen prompt has empty variables.
  2. **Builds the history:** all earlier turns that have text and no error, as `{role, content}`, plus the new user message. The backend is stateless, so the whole conversation is sent each time.
  3. Adds two turns to the screen: the user's message and an empty assistant turn marked "streaming" (shows "Thinking…").
  4. Clears the input and marks the chat busy (the Send button becomes Stop).
  5. Creates an `AbortController` so Stop can cancel.
  6. Calls `streamChat` with `{model, messages, system, prompt, rag}`. `system` is sent only if the box isn't blank. `prompt` is `{name, variables}` (only the chosen prompt's variables) when a prompt is selected. `rag` is `{collections: ragNames}` when any knowledge base is ticked. For each event:
     - `start` → saves provider, model and prompt on the assistant turn, plus its `citations`, which render under the reply as a collapsible "Sources (n)" list, or "No matching sources found";
     - `delta` → appends the text, so the reply grows on screen;
     - `done` → saves usage, latency, finish reason, prompt, and marks it finished;
     - `error` → shows the error message under the reply.
  7. If `streamChat` throws:
     - Stop was pressed (abort) → marks the turn "Stopped.";
     - otherwise → shows the server's message (`ApiError`) or "Network error. Please retry."
  8. Always at the end: marks the turn not streaming, forgets the controller, clears busy.
- **Gives back:** nothing (updates the screen).
- **Can fail with:** nothing thrown; errors appear under the reply.
- **Debug tips:**
  - If replies arrive all at once instead of streaming, a proxy is buffering the response.
  - Failed turns are left out of the next request's history.

#### `nextKey()` · `frontend/components/chat.tsx`

- **Where:** module level in `chat.tsx`. **Called by:** `send`.
- **Takes in:** nothing.
- **What it does:** increments a counter and returns a unique key.
- **Gives back:** `"t1"`, `"t2"`, ...
- **Can fail with:** nothing.
- **Debug tips:** React uses these keys to update the right message bubble.

#### `streamChat()` · `frontend/lib/api.ts`

- **Where:** API client. **Called by:** `send`. **Calls:** `toApiError`, `parseEvent`.
- **Takes in:**
  - `request`: `{model, messages, system?, ...}`;
  - `onEvent`: a function called for each event;
  - `signal` (optional): for cancelling.
- **What it does:**
  1. Sends `POST {API_URL}/v1/chat/stream` with the JSON body.
  2. Not OK (e.g. 404 unknown model) → throws `toApiError(...)`. No events are delivered.
  3. Reads the body as text, piece by piece.
  4. Adds each piece to a buffer and splits it at blank lines (`\n\n` or `\r\n\r\n`). Each complete block becomes one event via `parseEvent`, and is passed to `onEvent`. An unfinished block waits in the buffer for the next piece.
  5. At the end, parses whatever is left in the buffer.
- **Gives back:** a promise that finishes when the stream ends.
- **Can fail with:**
  - `ApiError` (HTTP error, or no response body);
  - a network error;
  - `AbortError` when Stop is pressed;
  - a `SyntaxError` if a `data:` line isn't valid JSON.
- **Debug tips:** log each event inside `onEvent` to see exactly what arrives.

#### `parseEvent()` · `frontend/lib/api.ts`

- **Where:** API client (internal). **Called by:** `streamChat`.
- **Takes in:** `block`, the text of one SSE event, e.g. `"data: {\"type\":\"delta\",...}"`.
- **What it does:**
  1. Keeps only the lines starting with `data:`, removes that prefix (and one following space), and joins them.
  2. No data lines (e.g. a `: ping` keep-alive) → returns `null`.
  3. Otherwise parses the JSON.
- **Gives back:** a `StreamEvent` object, or `null`.
- **Can fail with:** `SyntaxError` for invalid JSON.
- **Debug tips:** `: ping` lines every 15 s are normal and ignored.

#### `updateTurn()` · `frontend/components/chat.tsx`

- **Where:** inside `Chat`. **Called by:** `send` (for every stream event, and at the end).
- **Takes in:**
  - `key`: which turn to change;
  - `update`: a function that takes the old turn and returns the new one.
- **What it does:** replaces the matching turn in the list with `update(oldTurn)` and leaves all other turns as they are. It uses React's functional state update, so quick back-to-back deltas never overwrite each other.
- **Gives back:** nothing (triggers a re-render).
- **Can fail with:** nothing.
- **Debug tips:** if text appears in the wrong bubble, check the key passed from `send`.

### 11.3 Stop and New chat

#### `stop()` · `frontend/components/chat.tsx`

- **Where:** inside `Chat`. **Called by:** the Stop button; `newChat`.
- **Takes in:** nothing.
- **What it does:** aborts the current request, if any. `streamChat` then throws an `AbortError`, which `send` shows as "Stopped."
- **Gives back:** nothing.
- **Can fail with:** nothing.
- **Debug tips:** the backend sees a client disconnect and stops streaming, so the vendor call is cut short.

#### `newChat()` · `frontend/components/chat.tsx`

- **Where:** inside `Chat`. **Called by:** the New chat button. **Calls:** `stop`.
- **Takes in:** nothing.
- **What it does:** stops any running reply, then clears all turns. The model and system prompt stay as they are.
- **Gives back:** nothing.
- **Can fail with:** nothing.
- **Debug tips:** there's no server-side conversation to delete; history only lives in the page.

---

## 12. Tests

Run everything with `cd backend && uv run pytest`. Each test below says **what it proves**.
If one fails, the assertion message shows the expected vs actual value.

### 12.1 Shared helpers — `backend/tests/conftest.py`

#### `make_settings()` · `backend/tests/conftest.py`

- **Takes in:** any settings to override, e.g. `make_settings(openai_api_key="x")`.
- **What it does:** builds `Settings` that **ignore your real `.env` and keys**: all API keys off, Ollama off, no database, no default prompt, no admin key, no embedding provider or model choice, mock on with no delay, default model `mock/echo`. Then applies the overrides.
- **Gives back:** a `Settings` object.
- **Debug tips:** if tests behave differently on your machine, check that each test uses this helper rather than `get_settings()`.

#### `client()` · `backend/tests/conftest.py`

- **What it does:** a pytest fixture. Builds the app with `make_settings()`, starts it (runs `lifespan`), hands a `TestClient` to the test, and shuts the app down afterwards.
- **Gives back:** a `TestClient` for making HTTP calls in tests.

#### `llm_request()` · `backend/tests/conftest.py`

- **What it does:** a pytest fixture. A ready `LLMRequest` with system prompt "Be brief." and one user message "Hello".
- **Gives back:** that `LLMRequest`.

#### `sse()` · `backend/tests/conftest.py`

- **Takes in:**
  - `events`: a list of dictionaries;
  - `named`: whether to add `event:` lines (Anthropic style);
  - `done_marker`: whether to end with `data: [DONE]` (OpenAI style).
- **What it does:** writes the events as a server-sent-events body, like a vendor would send.
- **Gives back:** the body as bytes.

#### `Recorder.__init__()` · `backend/tests/conftest.py`

- **Takes in:** `handler`, a function that receives an HTTP request and returns a fake response.
- **What it does:** stores the handler and an empty list of seen requests.

#### `Recorder.__call__()` · `backend/tests/conftest.py`

- **Takes in:** `request`, an outgoing HTTP request from an SDK.
- **What it does:** saves the request, then returns `handler(request)`. No network is touched.

#### `Recorder.client()` · `backend/tests/conftest.py`

- **What it does:** builds an `httpx2.AsyncClient` whose transport is this recorder. It's passed to an adapter as `http_client`, so the **real vendor SDK** runs but its HTTP calls land here.
- **Gives back:** the fake HTTP client.

#### `Recorder.last_json()` · `backend/tests/conftest.py`

- **What it does:** a property that reads the JSON body of the last request the SDK sent.
- **Gives back:** a dictionary. Tests use it to check what we sent to the vendor (e.g. "no `temperature` for Claude").

### 12.2 Provider tests

#### `make_provider()` · `backend/tests/test_anthropic_provider.py`

- **What it does:** builds an `AnthropicProvider` with a test key, no retries, effort `medium`, fallback enabled for `claude-opus-5-5` only, wired to the given `Recorder`.

#### `test_complete_maps_request_and_response()` · `backend/tests/test_anthropic_provider.py`

- **Proves:** we send the right body to Claude and read the reply correctly.
  - Sent: system prompt, messages, `max_tokens` 16,000, effort, fallback flags, **no** temperature.
  - Read back: the text only (not the thinking block), `stop`, and the token counts.

#### `test_fallback_only_for_supported_models()` · `backend/tests/test_anthropic_provider.py`

- **Proves:** models not in the fallback list (here `claude-haiku-5-5`) get no `fallbacks` field and no beta header.

#### `test_refusal_is_reported()` · `backend/tests/test_anthropic_provider.py`

- **Proves:** a Claude `refusal` stop reason becomes `finish_reason="refusal"` with empty content.

#### `test_stream_yields_deltas_then_final()` · `backend/tests/test_anthropic_provider.py`

- **Proves:** a Claude event stream becomes the text chunks `"Hi"`, `" there"`, then a final chunk with `length` and the output token count; streaming uses `max_tokens` 64,000.

#### `test_errors_are_mapped()` · `backend/tests/test_anthropic_provider.py`

- **Proves:** Claude's 401 becomes `ProviderAuthError` and 429 becomes `ProviderRateLimitError`.

#### `chunk()` · `backend/tests/test_openai_provider.py`

- **Takes in:** `delta` (or `None` for the usage-only last chunk), `finish_reason`, `usage`.
- **What it does:** builds one fake OpenAI stream chunk.

#### `make_provider()` · `backend/tests/test_openai_provider.py`

- **What it does:** builds an `OpenAIProvider` with a test key and no retries, wired to the given `Recorder`.

#### `test_complete_maps_request_and_response()` · `backend/tests/test_openai_provider.py`

- **Proves:**
  - The system prompt goes first as a `system` message.
  - `max_tokens` is sent as `max_completion_tokens`.
  - `temperature` isn't sent when unset.
  - The text, finish reason, dated model name and tokens are read correctly.

#### `test_refusal()` · `backend/tests/test_openai_provider.py`

- **Proves:** an OpenAI refusal becomes `finish_reason="refusal"`, with the refusal text as content.

#### `test_stream()` · `backend/tests/test_openai_provider.py`

- **Proves:** the chunks join into `"Hi there"`, `length` is reported, usage from the usage-only last chunk is captured, and `include_usage` was requested.

#### `test_errors_are_mapped()` · `backend/tests/test_openai_provider.py`

- **Proves:** 400 → `ProviderBadRequestError`, 500 → `ProviderUnavailableError`.

#### `response()` · `backend/tests/test_gemini_provider.py`

- **Takes in:** `text`, `finish` (e.g. `"STOP"` or `None`), `usage`.
- **What it does:** builds one fake Gemini reply.

#### `make_provider()` · `backend/tests/test_gemini_provider.py`

- **What it does:** builds a `GeminiProvider` with a test key and no retries, wired to the given `Recorder`.

#### `test_complete_maps_roles_and_system()` · `backend/tests/test_gemini_provider.py`

- **Proves:**
  - Roles become `user`/`model`/`user`.
  - The system prompt goes into `systemInstruction` and temperature into `generationConfig`.
  - The right URL is called.
  - Thinking tokens are added to output tokens.

#### `test_safety_block_maps_to_content_filter()` · `backend/tests/test_gemini_provider.py`

- **Proves:** a `SAFETY` finish becomes `content_filter`.

#### `test_stream()` · `backend/tests/test_gemini_provider.py`

- **Proves:** stream chunks join into `"Hi there"`, with `stop` and usage from the last chunk, using the `streamGenerateContent` endpoint.

#### `test_errors_are_mapped()` · `backend/tests/test_gemini_provider.py`

- **Proves:** 403 → `ProviderAuthError`, 429 → `ProviderRateLimitError`.

#### `make_provider()` · `backend/tests/test_ollama_provider.py`

- **Takes in:** `handler`, the fake Ollama server.
- **What it does:** builds an `OllamaProvider` on a fake `httpx` client and returns it with the list of seen requests.

#### `record()` · `backend/tests/test_ollama_provider.py`

- **What it does:** an inner helper of `make_provider`: saves each request, then passes it to the handler.

#### `test_complete()` · `backend/tests/test_ollama_provider.py`

- **Proves:**
  - The system prompt goes first and `max_tokens` → `options.num_predict`.
  - `stream` is false.
  - The reply and token counts are read correctly.

#### `test_stream_reads_ndjson()` · `backend/tests/test_ollama_provider.py`

- **Proves:** one-JSON-per-line streaming joins into `"Hi there"` and ends with a final chunk carrying usage.

#### `test_missing_model_gives_helpful_error()` · `backend/tests/test_ollama_provider.py`

- **Proves:** an Ollama 404 becomes `ProviderBadRequestError` whose message tells you to run `ollama pull`.

#### `test_unreachable_server()` · `backend/tests/test_ollama_provider.py`

- **Proves:** "connection refused" becomes `ProviderUnavailableError`.

#### `refuse()` · `backend/tests/test_ollama_provider.py`

- **What it does:** an inner helper of `test_unreachable_server`: a fake server that raises a connection error.

### 12.3 Registry tests — `backend/tests/test_registry.py`

#### `test_only_configured_providers_are_enabled()` · `backend/tests/test_registry.py`

- **Proves:** with no keys only `mock` is enabled; with all keys set (and mock off), all four real providers are enabled, in order.

#### `test_csv_lists_and_blank_values_from_env()` · `backend/tests/test_registry.py`

- **Proves:** `OPENAI_MODELS="gpt-a, gpt-b"` becomes a two-item list, and a blank `ANTHROPIC_EFFORT=` becomes `None`.

#### `test_resolve()` · `backend/tests/test_registry.py`

- **Proves:** `"mock/echo"` resolves to the mock adapter and `"echo"`; no model falls back to the default.

#### `test_resolve_rejects_unknown_or_disallowed()` · `backend/tests/test_registry.py`

- **Proves:** each of these raises `ModelNotFoundError`:
  - no slash (`"echo"`);
  - a provider that isn't configured (`"openai/gpt-5"`);
  - a model that isn't allowed (`"mock/unknown"`);
  - an empty model (`"mock/"`).

#### `test_default_model_falls_back_to_first_available()` · `backend/tests/test_registry.py`

- **Proves:** if the configured default isn't available, the first available model is used.

### 12.4 API tests — `backend/tests/test_api.py`

#### `test_health()` · `backend/tests/test_api.py`

- **Proves:** `/health` returns `ok` with `["mock"]`, `database: "disabled"`, `rag: "disabled"` and an `X-Request-ID` header.

#### `test_models()` · `backend/tests/test_api.py`

- **Proves:** `/v1/models` returns `mock/echo` as default, and both mock models in the list.

#### `test_chat()` · `backend/tests/test_api.py`

- **Proves:** `/v1/chat` returns the echo text, the provider, `stop`, the token count and a `chat_` id.

#### `test_chat_uses_default_model_when_omitted()` · `backend/tests/test_api.py`

- **Proves:** a request without `model` uses the default.

#### `test_unknown_model_is_404()` · `backend/tests/test_api.py`

- **Proves:** an unconfigured provider returns 404 `model_not_found`.

#### `test_first_message_must_be_user()` · `backend/tests/test_api.py`

- **Proves:** a conversation starting with an assistant message is rejected with 422.

#### `parse_sse()` · `backend/tests/test_api.py`

- **Takes in:** a raw SSE body as text.
- **What it does:** keeps the `data:` lines and parses each as JSON.
- **Gives back:** a list of event dictionaries.

#### `test_stream()` · `backend/tests/test_api.py`

- **Proves:**
  - The stream's events are `start`, three `delta`s, `done`.
  - The deltas join into the echo text.
  - `done` has `stop` and usage.
  - All events share one id.
  - The content type is `text/event-stream`.

#### `test_stream_unknown_model_is_http_error()` · `backend/tests/test_api.py`

- **Proves:** an unknown model on the **streaming** route still returns a normal 404, not a 200 stream (thanks to the early first-chunk pull).

#### `test_stream_mid_stream_failure_sends_error_event()` · `backend/tests/test_api.py`

- **Proves:** a provider that fails after sending some text produces `start`, `delta`, then an `error` event with `provider_unavailable`.

#### `FailingProvider.stream()` · `backend/tests/test_api.py`

- **What it does:** a fake provider (defined inside the test above) that yields `"partial"` and then raises `ProviderUnavailableError`.

### 12.5 Docs coverage test — `backend/tests/test_docs_coverage.py`

#### `_python_functions()` · `backend/tests/test_docs_coverage.py`

- **Takes in:** `path`, a Python file.
- **What it does:** parses the file's code (without running it) and collects every function name:
  - methods as `Class.method`;
  - functions inside functions by their own name.
- **Gives back:** e.g. `["create_app", "lifespan", "request_context"]`.

#### `visit()` · `backend/tests/test_docs_coverage.py`

- **Takes in:** a code node and the current class name (or none).
- **What it does:** an inner helper of `_python_functions`: walks the code tree and records each function it finds.

#### `_all_functions()` · `backend/tests/test_docs_coverage.py`

- **What it does:** runs `_python_functions` over `backend/app`, `backend/tests`, `backend/migrations`, and a regex scan over the frontend's `.ts`/`.tsx` files.
  The regex finds named functions, arrow functions assigned to a `const` (including `useCallback(...)` and generic `async <T,>(...)` ones), and classes. Plain values like `const x = (a ?? b).filter(...)` are not counted.
- **Gives back:** a list of `(file, function name)` pairs.

#### `_mentioned()` · `backend/tests/test_docs_coverage.py`

- **Takes in:** the lines of a doc, a file path, a function name.
- **What it does:** checks that at least one line contains both the file path and the name in backticks (with or without `()`).
- **Gives back:** `True` or `False`.

#### `test_every_function_is_documented()` · `backend/tests/test_docs_coverage.py`

- **Proves:** both docs mention every function next to its file. When it fails, it prints each missing `file :: function`. Add an entry for each in **both** docs.

#### `test_scanner_finds_functions()` · `backend/tests/test_docs_coverage.py`

- **Proves:** the scanner itself works: a few known functions (`create_app`, `ChatService.complete`, `lifespan`, `streamChat`, `Chat`) are found.

---

## 13. Prompts (Layer 2)

Managed, versioned system prompts stored in Postgres. The order things run in is in
[WORKFLOW_CODE_MAP.md § 13](WORKFLOW_CODE_MAP.md#13-prompts-layer-2).

How the pieces split up:

- **Routes** (`backend/app/api/v1/prompts.py`) only receive the request and hand it on.
- **Service** (`backend/app/prompts/service.py`) holds every rule: versioning, activation, archiving, rendering.
- **Repository** (`backend/app/prompts/repository.py`) holds every SQL query.
- **Template** (`backend/app/prompts/template.py`) fills in `{{ variables }}`.

### 13.1 Database connection

#### `Database.__init__()` · `backend/app/db/session.py`

- **Where:** `Database` class. **Called by:** `lifespan` (when `DATABASE_URL` is set), `main` in the seed script, the `database` test fixture.
- **Takes in:**
  - `url`: a SQLAlchemy async URL, e.g. `postgresql+asyncpg://eap:eap@localhost:5432/eap` (from `DATABASE_URL`), or `sqlite+aiosqlite:///:memory:` in tests;
  - `echo`: print every SQL statement (debugging).
- **What it does:**
  1. Creates an async **engine**, the connection pool.
     - Real databases: `pool_pre_ping` is on, so a dropped connection is replaced instead of failing a request.
     - In-memory SQLite (tests): one shared connection is used, so all sessions see the same data.
  2. Creates a **session factory**. `expire_on_commit=False` keeps loaded objects readable after a commit.
- **Gives back:** the `Database` object. **No connection is opened yet**, so a database that's down doesn't stop the app from starting.
- **Can fail with:** an error for a malformed URL or a missing driver (e.g. `postgresql://` without `+asyncpg`).
- **Debug tips:** the URL must use an async driver: `postgresql+asyncpg://...`.

#### `Database.session()` · `backend/app/db/session.py`

- **Where:** `Database`. **Called by:** `get_db_session` (one per request), `main` (seed), the `prompt_service` fixture.
- **Takes in:** nothing.
- **What it does:** makes a new `AsyncSession`. It borrows a connection from the pool only when the first query runs.
- **Gives back:** an `AsyncSession`, used as `async with db.session() as session:` (closed automatically at the end).
- **Can fail with:** nothing here; connection errors appear on the first query.
- **Debug tips:** one session = one unit of work. Don't share a session between requests.

#### `Database.ping()` · `backend/app/db/session.py`

- **Where:** `Database`. **Called by:** `health`.
- **Takes in:** nothing.
- **What it does:** opens a connection and runs `SELECT 1`. If anything fails, logs `database ping failed` with the reason.
- **Gives back:** `True` (database answered) or `False`.
- **Can fail with:** never raises.
- **Debug tips:** `/health` showing `"database": "unavailable"` → read the `database ping failed` log line for the exact cause (wrong password, host down...).

#### `Database.create_all()` · `backend/app/db/session.py`

- **Where:** `Database`. **Called by:** the `database` test fixture. **Tests only.**
- **Takes in:** nothing.
- **What it does:** gets the table list from `_tables` (on SQLite, the Postgres-only knowledge-base tables are left out). On Postgres, first enables the `vector` extension. Then creates those tables directly from the models.
- **Gives back:** nothing.
- **Can fail with:** database errors (e.g. no permission to create tables).
- **Debug tips:** real databases must use Alembic migrations (`alembic upgrade head`), never this, so the migration history stays correct.

#### `Database.drop_all()` · `backend/app/db/session.py`

- **Where:** `Database`. **Called by:** the `database` test fixture (before and after each test). **Tests only.**
- **Takes in:** nothing. **What it does:** drops every table from `_tables` (the same list `create_all` uses). **Gives back:** nothing.
- **Can fail with:** database errors.
- **Debug tips:** point `TEST_DATABASE_URL` at a **separate test database**: this deletes everything.

#### `Database.aclose()` · `backend/app/db/session.py`

- **Where:** `Database`. **Called by:** `lifespan` at shutdown (only if it opened the database), `main` (seed), the `database` fixture.
- **Takes in:** nothing. **What it does:** closes every pooled connection. **Gives back:** nothing.
- **Can fail with:** nothing in normal use.
- **Debug tips:** warnings about unclosed connections usually mean this wasn't called.

#### `utcnow()` · `backend/app/prompts/models.py`

- **Where:** module-level helper in the models file. **Called by:** SQLAlchemy, to fill `created_at`/`updated_at` on new and changed rows.
- **Takes in:** nothing.
- **What it does:** gets the current time in UTC (time-zone aware).
- **Gives back:** e.g. `datetime(2026, 11, 2, 6, 57, tzinfo=UTC)`.
- **Can fail with:** nothing.
- **Debug tips:** all stored times are UTC; the frontend converts them to the viewer's local time.

### 13.2 Migrations and seeding

#### `database_url()` · `backend/migrations/env.py`

- **Where:** Alembic's runner. **Called by:** `run_migrations_offline`, `run_async_migrations`.
- **Takes in:** nothing; reads `DATABASE_URL` through the app's `Settings` (environment or `backend/.env`).
- **What it does:** returns the URL, or stops with "DATABASE_URL is not set".
- **Gives back:** e.g. `"postgresql+asyncpg://eap:eap@localhost:5432/eap"`.
- **Can fail with:** `RuntimeError` when the URL is missing.
- **Debug tips:** run Alembic from the `backend/` folder so `backend/.env` is found.

#### `run_migrations_offline()` · `backend/migrations/env.py`

- **Where:** Alembic's runner. **Called by:** Alembic, when run with `--sql` (e.g. `uv run alembic upgrade head --sql`).
- **Takes in:** nothing.
- **What it does:** prints the SQL each migration *would* run, without connecting.
- **Gives back:** nothing (SQL goes to the terminal).
- **Can fail with:** the `database_url` error.
- **Debug tips:** use this to review or hand the SQL to a DBA before running it in production.

#### `do_run_migrations()` · `backend/migrations/env.py`

- **Where:** Alembic's runner. **Called by:** `run_async_migrations` (through `run_sync`).
- **Takes in:** `connection`, an open database connection.
- **What it does:** tells Alembic which models to compare against (`Base.metadata`), then runs the pending migrations inside one transaction.
- **Gives back:** nothing.
- **Can fail with:** any SQL error in a migration. Postgres rolls back the whole transaction, so nothing is half-applied.
- **Debug tips:** `uv run alembic current` shows which migration the database is on.

#### `run_async_migrations()` · `backend/migrations/env.py`

- **Where:** Alembic's runner. **Called by:** `run_migrations_online`.
- **Takes in:** nothing.
- **What it does:**
  1. Creates a temporary engine (no pooling).
  2. Connects.
  3. Runs `do_run_migrations` on that connection.
  4. Closes the engine.
- **Gives back:** nothing.
- **Can fail with:** connection errors (wrong URL or password, database down).
- **Debug tips:** the same URL problems as `/health` → `unavailable`.

#### `run_migrations_online()` · `backend/migrations/env.py`

- **Where:** Alembic's runner. **Called by:** Alembic, for normal commands (`upgrade`, `downgrade`, `check`...).
- **Takes in:** nothing. **What it does:** starts the async event loop and runs `run_async_migrations`. **Gives back:** nothing.
- **Can fail with:** as `run_async_migrations`.
- **Debug tips:** `uv run alembic check` fails if the models and migrations differ, so you know a new migration is needed.

#### `upgrade()` · `backend/migrations/versions/0001_create_prompts.py`

- **Where:** migration `0001`. **Called by:** `alembic upgrade head`.
- **Takes in:** nothing.
- **What it does:** creates:
  - table `prompts`, with a unique index on `name`;
  - table `prompt_versions`, with a foreign key to `prompts` (deleting a prompt deletes its versions), a unique rule on (prompt, version number), and an index on `prompt_id`.
- **Gives back:** nothing.
- **Can fail with:** "relation already exists" if the tables were made another way (e.g. `create_all`).
- **Debug tips:** a later change to the tables goes in a **new** migration file. Never edit this one once it has run anywhere.

#### `downgrade()` · `backend/migrations/versions/0001_create_prompts.py`

- **Where:** migration `0001`. **Called by:** `alembic downgrade base` (or `-1`).
- **Takes in:** nothing. **What it does:** drops both tables and their indexes. **All prompt data is lost.** **Gives back:** nothing.
- **Can fail with:** database errors.
- **Debug tips:** only for local development or a planned rollback.

#### `seed()` · `backend/app/prompts/seed.py`

- **Where:** seed script. **Called by:** `main`; `test_seed_is_idempotent`.
- **Takes in:** `service`, a `PromptService`.
- **What it does:** for each example prompt (`general-assistant`, and `support-agent` with `{{ company_name }}` and `{{ support_email }}`):
  1. Checks whether it exists.
  2. If it doesn't, creates it, with `created_by="seed"`.
- **Gives back:** the names it created, e.g. `["general-assistant", "support-agent"]`, or `[]` when both already exist.
- **Can fail with:** database errors.
- **Debug tips:** safe to run any number of times; it never changes an existing prompt.

#### `main()` · `backend/app/prompts/seed.py`

- **Where:** seed script. **Called by:** `uv run python -m app.prompts.seed`.
- **Takes in:** nothing; reads `DATABASE_URL`.
- **What it does:** opens the database, runs `seed` in one session, closes the database, and prints what it created.
- **Gives back:** nothing (prints `Created: ...` or `Nothing to do...`).
- **Can fail with:**
  - `SystemExit("DATABASE_URL is not set.")`;
  - "relation does not exist" if the migrations weren't run first.
- **Debug tips:** order matters: `alembic upgrade head` first, then the seed.

### 13.3 Request dependencies

#### `get_settings_from_app()` · `backend/app/api/deps.py`

- **Where:** `backend/app/api/deps.py`. **Called by:** `get_prompt_service`, `require_admin`.
- **Takes in:** `request`. **What it does:** returns the `Settings` saved on the app by `create_app`. **Gives back:** a `Settings` object.
- **Can fail with:** nothing.
- **Debug tips:** tests that pass their own settings to `create_app` are read through here, not from `.env`.

#### `get_db_session()` · `backend/app/api/deps.py`

- **Where:** `backend/app/api/deps.py`. **Called by:** FastAPI, for `get_prompt_service` (and so for every chat and prompt request).
- **Takes in:** `request` (to find `app.state.database`).
- **What it does:**
  1. No database configured → gives `None`.
  2. Otherwise opens a session for this request.
  3. Closes it after the response (any work that wasn't committed is discarded).
- **Gives back:** an `AsyncSession` or `None`.
- **Can fail with:** nothing at this point; connection errors show up on the first query.
- **Debug tips:** writes are committed explicitly by `PromptService._commit`, before the response is sent. So a 2xx reply means the change is saved.

#### `get_prompt_service()` · `backend/app/api/deps.py`

- **Where:** `backend/app/api/deps.py`. **Called by:** FastAPI, for `get_chat_service` and `require_prompt_service`. **Calls:** `get_settings_from_app`.
- **Takes in:** `request`, and `session` (from `get_db_session`).
- **What it does:** wraps the session in a `PromptService`, set up with `DEFAULT_PROMPT_NAME`.
- **Gives back:** a `PromptService`, or `None` without a database.
- **Can fail with:** nothing.
- **Debug tips:** FastAPI reuses the same session for every dependency in one request.

#### `require_prompt_service()` · `backend/app/api/deps.py`

- **Where:** `backend/app/api/deps.py`. **Called by:** FastAPI, for every `/v1/prompts` route.
- **Takes in:** `service` (from `get_prompt_service`).
- **What it does:** lets the request through when there is a prompt service; otherwise stops it.
- **Gives back:** the `PromptService`.
- **Can fail with:** `PromptStoreUnavailableError` → **503** `prompt_store_unavailable` when `DATABASE_URL` isn't set.
- **Debug tips:** chat routes use `get_prompt_service` instead, so chat keeps working without a database.

#### `require_admin()` · `backend/app/api/deps.py`

- **Where:** `backend/app/api/deps.py`. **Called by:** FastAPI, for the write routes: create, patch, archive, new version, activate. **Calls:** `get_settings_from_app`.
- **Takes in:** `request`, and `x_admin_key` (the `X-Admin-Key` header, if sent).
- **What it does:**
  1. `ADMIN_API_KEY` not set → allows everything (development).
  2. Otherwise the header must match exactly. The comparison takes the same time whether or not the key matches, so an attacker can't guess it character by character.
- **Gives back:** nothing (lets the request through).
- **Can fail with:** `AdminAuthError` → **401** `admin_auth_required`.
- **Debug tips:**
  - Reads (list, get, render) never need the key.
  - This is a stand-in until real users and roles arrive in Layer 5.

### 13.4 Prompt routes

Each route only passes the request to `PromptService`; the rules and errors are described there (13.5).

#### `list_prompts()` · `backend/app/api/v1/prompts.py`

- **Route:** `GET /v1/prompts?include_archived=false`. **Calls:** `PromptService.list_prompts`.
- **Takes in:** `include_archived` (query flag), `service`.
- **Gives back:** `{"data": [PromptSummary, ...]}`, sorted by name.
- **Debug tips:** archived prompts are hidden unless `include_archived=true`.

#### `create_prompt()` · `backend/app/api/v1/prompts.py`

- **Route:** `POST /v1/prompts` (admin). **Calls:** `PromptService.create_prompt`.
- **Takes in:** `body` (`PromptCreate`: name, description, template, note), `service`.
- **Gives back:** **201** with the new `PromptDetail` (version 1, active).
- **Can fail with:** 422 bad name format, 409 `prompt_conflict` name taken, 401 without admin key.

#### `get_prompt()` · `backend/app/api/v1/prompts.py`

- **Route:** `GET /v1/prompts/{name}`. **Calls:** `PromptService.get_prompt`.
- **Takes in:** `name`, `service`. **Gives back:** `PromptDetail` with every version (newest first).
- **Can fail with:** 404 `prompt_not_found`.

#### `update_prompt()` · `backend/app/api/v1/prompts.py`

- **Route:** `PATCH /v1/prompts/{name}` (admin). **Calls:** `PromptService.update_prompt`.
- **Takes in:** `name`, `body` (`{"description": "..."}`), `service`. **Gives back:** the updated `PromptDetail`.
- **Can fail with:** 404, 401.

#### `archive_prompt()` · `backend/app/api/v1/prompts.py`

- **Route:** `DELETE /v1/prompts/{name}` (admin). **Calls:** `PromptService.archive`.
- **Takes in:** `name`, `service`. **Gives back:** the `PromptDetail` with `archived: true`. Nothing is deleted from the database.
- **Can fail with:** 404, 401.

#### `get_prompt_version()` · `backend/app/api/v1/prompts.py`

- **Route:** `GET /v1/prompts/{name}/versions/{version}`. **Calls:** `PromptService.get_version`.
- **Takes in:** `name`, `version` (number), `service`. **Gives back:** one `PromptVersionOut`.
- **Can fail with:** 404 `prompt_not_found` or `prompt_version_not_found`.

#### `create_prompt_version()` · `backend/app/api/v1/prompts.py`

- **Route:** `POST /v1/prompts/{name}/versions` (admin). **Calls:** `PromptService.add_version`.
- **Takes in:** `name`, `body` (`template`, `note`, `activate`), `service`. **Gives back:** **201** with the new `PromptVersionOut`.
- **Can fail with:** 404, 409 `prompt_archived`, 409 `prompt_conflict` (two saves at once), 401.

#### `activate_prompt_version()` · `backend/app/api/v1/prompts.py`

- **Route:** `POST /v1/prompts/{name}/activate` (admin), body `{"version": 2}`. **Calls:** `PromptService.activate`.
- **Takes in:** `name`, `body`, `service`. **Gives back:** the `PromptDetail` with the new active version.
- **Can fail with:** 404 (prompt or version), 409 `prompt_archived`, 401.
- **Debug tips:** this is how you roll back: activate an older version number.

#### `render_prompt()` · `backend/app/api/v1/prompts.py`

- **Route:** `POST /v1/prompts/{name}/render`, body `{"version": 1, "variables": {...}}` (version optional). **Calls:** `PromptService.render`.
- **Takes in:** `name`, `body`, `service`.
- **Gives back:** `{"name", "version", "text", "variables"}`, the exact text a model would receive. No LLM is called.
- **Can fail with:** 404, 409 `prompt_archived`, 400 `prompt_variable_missing`.

### 13.5 Prompt service (the rules)

#### `PromptService.__init__()` · `backend/app/prompts/service.py`

- **Called by:** `get_prompt_service`, `main` (seed), test fixtures.
- **Takes in:** `session` (an `AsyncSession`), `default_prompt_name` (from `DEFAULT_PROMPT_NAME`, or `None`).
- **What it does:** keeps the session, builds a `PromptRepository` on it, and remembers the default prompt name.
- **Gives back:** the service.

#### `PromptService.list_prompts()` · `backend/app/prompts/service.py`

- **Called by:** `list_prompts` route. **Calls:** `PromptRepository.list_prompts`, `latest_versions`, `active_versions`, `_summary`.
- **Takes in:** `include_archived` (true/false).
- **What it does:**
  1. Loads the prompts.
  2. In two more queries (not one per prompt), loads each prompt's highest version number and its active version.
  3. Builds one summary per prompt.
- **Gives back:** `PromptList`, e.g. `{"data": [{"name": "alpha", "active_version": 1, "latest_version": 2, "active_variables": ["x"], ...}]}`.
- **Can fail with:** database errors.
- **Debug tips:** `active_variables` is what the chat page uses to show variable inputs.

#### `PromptService.get_prompt()` · `backend/app/prompts/service.py`

- **Called by:** `get_prompt` route, `seed`. **Calls:** `_require_prompt`, `_detail`.
- **Takes in:** `name`. **What it does:** finds the prompt and builds its full detail. **Gives back:** `PromptDetail`.
- **Can fail with:** `PromptNotFoundError` (404). Archived prompts are still returned (marked `archived`).

#### `PromptService.get_version()` · `backend/app/prompts/service.py`

- **Called by:** `get_prompt_version` route. **Calls:** `_require_prompt`, `_require_version`, `_version_out`.
- **Takes in:** `name`, `version`. **Gives back:** `PromptVersionOut` (with `is_active`).
- **Can fail with:** `PromptNotFoundError`, `PromptVersionNotFoundError` (404).

#### `PromptService.create_prompt()` · `backend/app/prompts/service.py`

- **Called by:** `create_prompt` route, `seed`. **Calls:** `PromptRepository.get_prompt`, `_new_version`, `PromptRepository.add`, `_commit`, `_detail`.
- **Takes in:** `body` (`PromptCreate`), `created_by` (who made it; `"seed"` from the seed script, empty until Layer 5 adds users).
- **What it does:**
  1. If the name is taken → conflict.
  2. Makes the prompt row with `active_version = 1`. Its id is set right away, so the version can point to it.
  3. Makes version 1 from the template (variables detected automatically).
  4. Saves both in one commit.
  5. Reads back the detail.
- **Gives back:** `PromptDetail` with one active version.
- **Can fail with:** `PromptConflictError` (409), whether the name was found taken, or two creates raced (the database's unique rule catches it in `_commit`).
- **Debug tips:** the name rule (lowercase words with hyphens) is checked earlier by the request schema, giving 422.

#### `PromptService.update_prompt()` · `backend/app/prompts/service.py`

- **Called by:** `update_prompt` route. **Calls:** `_require_prompt`, `_commit`, `_detail`.
- **Takes in:** `name`, `body` (`PromptUpdate` with `description`).
- **What it does:** changes the description, commits, returns the detail. `updated_at` updates automatically.
- **Gives back:** `PromptDetail`.
- **Can fail with:** `PromptNotFoundError`.
- **Debug tips:** the description is the only field that changes in place; templates always become new versions.

#### `PromptService.add_version()` · `backend/app/prompts/service.py`

- **Called by:** `create_prompt_version` route. **Calls:** `_require_prompt(writable=True)`, `PromptRepository.latest_versions`, `_new_version`, `PromptRepository.add`, `_commit`, `_version_out`.
- **Takes in:** `name`, `body` (`template`, `note`, `activate`), `created_by`.
- **What it does:**
  1. Finds the prompt; archived → refuses.
  2. Next number = highest existing + 1.
  3. Saves the new version (variables detected).
  4. Only if `activate` is true does it become active. Otherwise the old version keeps serving chat.
  5. Touches `updated_at`, commits.
- **Gives back:** `PromptVersionOut`, e.g. `{"version": 3, "is_active": true, "variables": ["team"], ...}`.
- **Can fail with:** `PromptNotFoundError` (404), `PromptArchivedError` (409), `PromptConflictError` (409) if two saves pick the same number at once.
- **Debug tips:** saving without activating is how you stage a change; activate it once it's reviewed.

#### `PromptService.activate()` · `backend/app/prompts/service.py`

- **Called by:** `activate_prompt_version` route. **Calls:** `_require_prompt(writable=True)`, `_require_version`, `_commit`, `_detail`.
- **Takes in:** `name`, `version` (number).
- **What it does:** checks that the version exists, sets it as active, commits. Chat requests without a pinned version use it from the next request.
- **Gives back:** `PromptDetail`.
- **Can fail with:** `PromptNotFoundError`, `PromptVersionNotFoundError` (404), `PromptArchivedError` (409).
- **Debug tips:** **rollback** = activate an older number. Nothing is deleted, so you can roll forward again.

#### `PromptService.archive()` · `backend/app/prompts/service.py`

- **Called by:** `archive_prompt` route. **Calls:** `_require_prompt`, `_commit`, `_detail`.
- **Takes in:** `name`.
- **What it does:** stamps `archived_at` with the current time (once; archiving again changes nothing), commits.
- **Gives back:** `PromptDetail` with `archived: true`.
- **Can fail with:** `PromptNotFoundError`.
- **Debug tips:**
  - Archived prompts disappear from lists, can't be used in chat (409) and can't get new versions.
  - All versions stay in the database for audit.
  - There's no un-archive endpoint yet.

#### `PromptService.render()` · `backend/app/prompts/service.py`

- **Called by:** `render_prompt` route. **Calls:** `_render`.
- **Takes in:** `name`, `body` (`version` optional, `variables`).
- **What it does:** renders exactly as chat would, but returns the text instead of calling a model.
- **Gives back:** `RenderResponse`, e.g. `{"name": "support", "version": 1, "text": "You help Acme customers.", "variables": ["company"]}`.
- **Can fail with:** the same errors as `_render`.

#### `PromptService.resolve_for_chat()` · `backend/app/prompts/service.py`

- **Called by:** `ChatService._build_system`. **Calls:** `_render`.
- **Takes in:**
  - `ref`: the request's `prompt` field (`PromptRef`), or `None`;
  - `has_system`: whether the request has its own `system` text.
- **What it does:**
  1. `ref` given → renders that prompt (pinned or active version) with its variables.
  2. No `ref`, no `system`, and `DEFAULT_PROMPT_NAME` set → renders the default prompt, with no variables.
  3. Otherwise → returns nothing ("no managed prompt").
  4. Always ends the read transaction afterwards, so no database connection is held while the model is answering (which can take a while when streaming).
- **Gives back:** `RenderedPrompt(name, version, text, variables)` or `None`.
- **Can fail with:**
  - `PromptNotFoundError`, `PromptVersionNotFoundError` (404);
  - `PromptArchivedError` (409);
  - `PromptVariableError` (400).
- **Debug tips:** a default prompt **with variables** fails every request that doesn't send a prompt, because it's rendered with no values. Keep the default prompt free of variables.

#### `PromptService._render()` · `backend/app/prompts/service.py`

- **Called by:** `render`, `resolve_for_chat`. **Calls:** `_require_prompt`, `_require_version`, `template.render`.
- **Takes in:** `name`, `version` (number or `None` for active), `variables` (name → value).
- **What it does:**
  1. Finds the prompt; archived → refuses.
  2. Picks the version: the pinned one, or the active one.
  3. Loads it.
  4. Fills in the template.
- **Gives back:** `RenderedPrompt`.
- **Can fail with:**
  - `PromptNotFoundError`;
  - `PromptArchivedError`;
  - `PromptVersionNotFoundError` (also when a prompt somehow has no active version);
  - `PromptVariableError`.
- **Debug tips:** pinned versions don't need to be active. This is how you A/B test or compare versions in evals.

#### `PromptService._require_prompt()` · `backend/app/prompts/service.py`

- **Called by:** almost every service method. **Calls:** `PromptRepository.get_prompt`.
- **Takes in:** `name`, and `writable` (true when the caller is about to change the prompt).
- **What it does:** loads the prompt; missing → error; `writable` and archived → error.
- **Gives back:** the `Prompt` row.
- **Can fail with:** `PromptNotFoundError` (404), `PromptArchivedError` (409).

#### `PromptService._require_version()` · `backend/app/prompts/service.py`

- **Called by:** `get_version`, `activate`, `_render`. **Calls:** `PromptRepository.get_version`.
- **Takes in:** `prompt` (a `Prompt` row), `version` (number).
- **What it does:** loads that version of that prompt, or raises.
- **Gives back:** the `PromptVersion` row.
- **Can fail with:** `PromptVersionNotFoundError` (404), naming the prompt and number.

#### `PromptService._detail()` · `backend/app/prompts/service.py`

- **Called by:** `get_prompt`, `create_prompt`, `update_prompt`, `activate`, `archive`. **Calls:** `PromptRepository.list_versions`, `_summary`, `_version_out`.
- **Takes in:** `prompt` (a `Prompt` row).
- **What it does:** loads all its versions (newest first), finds the active one, and builds the summary plus the version list.
- **Gives back:** `PromptDetail`.
- **Can fail with:** database errors.

#### `PromptService._commit()` · `backend/app/prompts/service.py`

- **Called by:** every write method.
- **Takes in:** nothing.
- **What it does:** commits the session. If the database rejects it because of a unique rule (duplicate name, or the same version number saved twice at once), it undoes the attempt and raises a clear conflict instead.
- **Gives back:** nothing.
- **Can fail with:** `PromptConflictError` (409) "Another change to this prompt happened at the same time. Please retry."
- **Debug tips:** other database errors pass through unchanged and show as a 500 with a traceback in the server log.

#### `_new_version()` · `backend/app/prompts/service.py`

- **Called by:** `create_prompt`, `add_version`. **Calls:** `extract_variables`.
- **Takes in:** `prompt`, `number`, `template`, `note`, `created_by`.
- **What it does:** builds an unsaved `PromptVersion` row. Its `variables` list is detected from the template.
- **Gives back:** the `PromptVersion` (saved later by `_commit`).

#### `_summary()` · `backend/app/prompts/service.py`

- **Called by:** `list_prompts`, `_detail`.
- **Takes in:** `prompt` (row), `latest` (highest version number), `active` (the active version row or `None`).
- **What it does:** copies the fields the API shows into a `PromptSummary`. `active_variables` comes from the active version; `archived` is true when `archived_at` is set.
- **Gives back:** `PromptSummary`.

#### `_version_out()` · `backend/app/prompts/service.py`

- **Called by:** `get_version`, `add_version`, `_detail`.
- **Takes in:** `version` (row), `prompt` (row).
- **What it does:** copies the version's fields into `PromptVersionOut` and sets `is_active` (this number equals the prompt's `active_version`).
- **Gives back:** `PromptVersionOut`.

### 13.6 Prompt repository (the SQL)

#### `PromptRepository.__init__()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService.__init__`. **Takes in:** `session`. **What it does:** keeps the session for its queries.

#### `PromptRepository.list_prompts()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService.list_prompts`.
- **Takes in:** `include_archived`.
- **What it does:** `SELECT` all prompts ordered by name; without `include_archived`, only rows where `archived_at` is empty.
- **Gives back:** a list of `Prompt` rows.

#### `PromptRepository.get_prompt()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService.create_prompt`, `_require_prompt`.
- **Takes in:** `name`. **What it does:** finds the prompt with that exact name (indexed, fast). **Gives back:** a `Prompt` row or `None`.

#### `PromptRepository.get_version()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService._require_version`.
- **Takes in:** `prompt_id`, `version`. **What it does:** finds that one version. **Gives back:** a `PromptVersion` row or `None`.

#### `PromptRepository.list_versions()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService._detail`.
- **Takes in:** `prompt_id`. **What it does:** all versions of one prompt, highest number first. **Gives back:** a list of `PromptVersion` rows.

#### `PromptRepository.latest_versions()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService.list_prompts`, `PromptService.add_version`.
- **Takes in:** `prompt_ids` (a list).
- **What it does:** one grouped query: the highest version number per prompt. An empty list skips the query.
- **Gives back:** a dictionary `{prompt_id: highest_number}`.

#### `PromptRepository.active_versions()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService.list_prompts`.
- **Takes in:** `prompt_ids`.
- **What it does:** one query joining each prompt to the version whose number equals its `active_version`.
- **Gives back:** a dictionary `{prompt_id: PromptVersion}`.

#### `PromptRepository.add()` · `backend/app/prompts/repository.py`

- **Called by:** `PromptService.create_prompt`, `PromptService.add_version`.
- **Takes in:** `row`, a new `Prompt` or `PromptVersion`.
- **What it does:** stages the row in the session. It is written to the database when the service commits, so unique-rule errors surface in `_commit`, where they become 409s.
- **Gives back:** nothing.

### 13.7 Templates

#### `extract_variables()` · `backend/app/prompts/template.py`

- **Called by:** `render`, `_new_version` (so every saved version records its variables).
- **Takes in:** `template`, the prompt text.
- **What it does:**
  1. Finds every `{{ name }}`. Spaces inside the braces are optional. A name is letters, digits and underscores, and can't start with a digit.
  2. Keeps each name once, in order of first appearance.
- **Gives back:** `"Hi {{ name }}, welcome to {{company}}. Bye {{name}}."` → `["name", "company"]`.
- **Can fail with:** nothing.
- **Debug tips:** `{{ 1abc }}`, `{{ a-b }}` and single braces `{ x }` are **not** variables; they stay as literal text. The frontend has an identical rule (`extractVariables` in `frontend/lib/template.ts`).

#### `render()` · `backend/app/prompts/template.py`

- **Called by:** `PromptService._render`. **Calls:** `extract_variables`.
- **Takes in:** `template`, and `variables` (name → text/number/true-false).
- **What it does:**
  1. Lists every variable the template needs that has no value. Any missing → one error naming **all** of them.
  2. Replaces each `{{ name }}` with its value as text. Extra values are ignored; everything else in the template is left exactly as written. There are no loops, conditions or code, so a template can't run anything.
- **Gives back:** `render("Hi {{ name }} ({{ age }})", {"name": "Ada", "age": 36})` → `"Hi Ada (36)"`.
- **Can fail with:** `PromptVariableError` → 400 `prompt_variable_missing`, e.g. "Missing values for prompt variables: company, region".
- **Debug tips:** values are inserted as-is. A value that contains `{{ x }}` is not rendered again.

### 13.8 Chat integration

#### `ChatService._build_system()` · `backend/app/services/chat_service.py`

- **Called by:** `ChatService.complete`, `ChatService.stream` (before the provider is called). **Calls:** `PromptService.resolve_for_chat`.
- **Takes in:** `request`, the `ChatRequest`.
- **What it does:**
  1. No prompt service (no database):
     - the request names a prompt → error;
     - otherwise → the request's `system` as is.
  2. Asks `resolve_for_chat` which managed prompt applies.
  3. None applies → the request's `system` as is.
  4. One applies → final system prompt = rendered prompt text, then a blank line, then the request's `system` (if any).
- **Gives back:** `(final_system_text, PromptUsed or None)`, e.g. `("You help Acme customers.\n\nReply in French.", PromptUsed(name="support", version=1))`.
- **Can fail with:**
  - `PromptStoreUnavailableError` (503);
  - the prompt errors from `resolve_for_chat` (404/409/400).
  These happen before the provider is called, so a stream fails with a normal HTTP error.
- **Debug tips:** see the final text with `"model": "mock/echo-system"`.

#### `_prompt_label()` · `backend/app/services/chat_service.py`

- **Called by:** `ChatService.complete`, `ChatService.stream` (for logs).
- **Takes in:** `prompt`, a `PromptUsed` or `None`.
- **Gives back:** `"support-agent@v3"` or `None`.
- **Debug tips:** search logs for `"prompt": "support-agent@v3"` to find every reply that used that version.

### 13.9 Frontend: Prompts page and helpers

#### `Prompts()` · `frontend/app/prompts/page.tsx`

- **Where:** the `/prompts` route. **Called by:** Next.js. **Calls:** `PromptsPage`.
- **What it does:** sets the tab title "Prompts · Enterprise AI Platform" and renders the Prompts page.

#### `NavBar()` · `frontend/components/nav.tsx`

- **Where:** top bar (client component). **Called by:** `RootLayout`, on every page.
- **Takes in:** nothing; reads the current path with `usePathname()`.
- **What it does:** shows the app name and the **Chat**, **Prompts** and **Knowledge** links. The current page's link is highlighted and marked `aria-current="page"`.
- **Debug tips:** Next.js keeps a page you left mounted (hidden). That's why Chat keeps your draft and selections when you come back.

#### `PromptsPage()` · `frontend/components/prompts/prompts-page.tsx`

- **Called by:** `Prompts`. **Calls:** `useAdminKey`, `listPrompts`, `errorMessage`, `NewPromptForm`, `PromptDetailPanel`.
- **Takes in:** nothing. State:
  - `prompts`, `loading`, `loadError`;
  - `showArchived`;
  - `selected` (prompt name);
  - `creating`;
  - `refreshKey` (bumped to reload).
- **What it does:**
  1. **Load-list effect** (on first view, when "Show archived" changes, and after `reload`): `listPrompts()`. Results arriving after the page changed again are ignored.
  2. Renders:
     - the header: title, `AdminKeyInput`, **New prompt**;
     - any error banner;
     - the list on the left: name, `vN of M` or "archived", description;
     - on the right: the new-prompt form, the selected prompt's detail, or a hint.
- **Can fail with:** nothing thrown; errors are shown in the banner (via `errorMessage`).
- **Debug tips:** "The prompt store isn't configured" means the backend has no `DATABASE_URL`.

#### `reload()` · `frontend/components/prompts/prompts-page.tsx`

- **Called by:** `PromptDetailPanel` (after each change, as `onChanged`), `NewPromptForm` (after creating).
- **What it does:** bumps `refreshKey`, which re-runs the load-list effect, so the list shows the latest versions and archive states.

#### `NewPromptForm()` · `frontend/components/prompts/prompts-page.tsx`

- **Called by:** `PromptsPage` when "New prompt" is clicked. **Calls:** `submit`, `extractVariables`, `VariableChips`.
- **Takes in:** `adminKey`, `onCreated(name)`, `onCancel()`.
- **What it does:** a form with name, description, template and version note. The browser checks the name format (lowercase words with hyphens). While you type the template, the detected variables are shown live.
- **Debug tips:** the server checks the name again (422 if it's invalid).

#### `submit()` · `frontend/components/prompts/prompts-page.tsx`

- **Called by:** the New prompt form's submit. **Calls:** `createPrompt`, `errorMessage`.
- **Takes in:** `e`, the form event.
- **What it does:** stops the page from reloading, sends `createPrompt` with the admin key, then calls `onCreated(name)`, which opens the new prompt and refreshes the list. On failure, shows the error under the form (e.g. "A prompt named ... already exists.").

#### `PromptDetailPanel()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** `PromptsPage` for the selected prompt (rebuilt fresh when another prompt is selected). **Calls:** `getPrompt`, `run`, `saveVersion`, `VersionList`, `VariableChips`, `RenderPreview`, `VersionEditor`.
- **Takes in:** `name`, `adminKey`, `onChanged` (the page's `reload`).
- **What it does:**
  1. **Load-detail effect** (first view and after each successful change): `getPrompt(name)`. On first load it views the active version.
  2. Header: name, **Archive** (asks for confirmation), description with **Edit**, "Active vN · Latest vM", and the JSON snippet to use this prompt in chat.
  3. `VersionList`, then the viewed version's template, variables and `RenderPreview`, then `VersionEditor` (hidden for archived prompts).
- **Can fail with:** nothing thrown; errors appear under the header.

#### `run()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** the Archive, Save description and Make active / Roll back actions, and `saveVersion`.
- **Takes in:** `action`, a function that makes one API call.
- **What it does:**
  1. Marks the panel busy (buttons disabled) and clears the old error.
  2. Runs the action.
  3. **Success:** reloads the panel, tells the page to reload the list, and returns the action's result.
  4. **Failure:** shows the error and returns `undefined`.
- **Gives back:** the result, or `undefined` on failure.
- **Debug tips:** every change goes through here, so the panel and the list can't disagree after a change.

#### `saveVersion()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** `VersionEditor`'s **Save as new version**. **Calls:** `run`, `addPromptVersion`.
- **Takes in:** `template`, `note`, `activate`.
- **What it does:** saves the version, then switches the view to the new version number.
- **Gives back:** `true` if saved, `false` if it failed.

#### `VersionList()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** `PromptDetailPanel`.
- **Takes in:**
  - `versions`, `activeVersion`;
  - `viewing` (highlighted row);
  - `canChange` (false while busy or archived);
  - `onView(n)`, `onActivate(n)`.
- **What it does:** one row per version: number, note, local time, and either an **Active** badge or a button. The button reads **Roll back to vN** for older versions and **Make active** for newer ones. Each row also has a **View** button.

#### `RenderPreview()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** `PromptDetailPanel`, for the viewed version (reset when the version changes). **Calls:** `preview`.
- **Takes in:** `name`, `version` (the `PromptVersion`).
- **What it does:** one input per variable, a **Render vN** button, and the rendered text or an error.

#### `preview()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** the **Render vN** button. **Calls:** `renderPrompt`, `errorMessage`.
- **What it does:** sends the typed values for that exact version, then shows the returned text. On error (e.g. missing variables), it shows the message and hides any old text.

#### `VersionEditor()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** `PromptDetailPanel` (reset whenever the viewed version changes). **Calls:** `extractVariables`, `VariableChips`, `onSave` (`saveVersion`).
- **Takes in:** `startFrom` (the viewed version), `busy`, `onSave(template, note, activate)`.
- **What it does:**
  1. Shows a template box pre-filled with the viewed version, the detected variables (live), a note field, and "Make it active right away" (on by default).
  2. Save is disabled until the template differs from the starting version.
- **Debug tips:** to stage a change without affecting chat, untick "Make it active right away".

#### `VariableChips()` · `frontend/components/prompts/prompt-detail.tsx`

- **Called by:** `NewPromptForm`, `PromptDetailPanel`, `VersionEditor`.
- **Takes in:** `names`, a list of variable names.
- **What it does:** shows them as small code labels, or "No variables."

#### `errorMessage()` · `frontend/components/prompts/messages.ts`

- **Called by:** every Prompts-page component that shows an error.
- **Takes in:** `err`, anything thrown by an API call.
- **What it does:** turns it into one clear sentence:
  - `prompt_store_unavailable` → "set DATABASE_URL in backend/.env";
  - `admin_auth_required` → "enter the admin key at the top of the page";
  - any other server error → its message;
  - a network failure → "Can't reach the backend...".
- **Gives back:** the message text.

#### `useAdminKey()` · `frontend/components/prompts/admin-key.tsx`

- **Called by:** `PromptsPage`. **Calls:** React's `useSyncExternalStore` with `subscribe` and `readKey`; returns `setKey`.
- **Takes in:** nothing.
- **What it does:** keeps the admin key in **sessionStorage**: it survives reloads but is forgotten when the tab closes. During server rendering the key is empty, which avoids a mismatch between server and browser.
- **Gives back:** `[key, setKey]`.
- **Debug tips:** the key is never sent for reads, only for changes (as `X-Admin-Key`).

#### `subscribe()` · `frontend/components/prompts/admin-key.tsx`

- **Called by:** React (through `useAdminKey`).
- **Takes in:** `onChange`, a function React wants called when the key changes.
- **What it does:** listens for storage changes and for this app's own "admin key changed" event.
- **Gives back:** a function that stops listening.

#### `readKey()` · `frontend/components/prompts/admin-key.tsx`

- **Called by:** React (through `useAdminKey`), on every render.
- **What it does:** reads the key from sessionStorage, or from an in-memory copy if storage is blocked (e.g. strict privacy settings).
- **Gives back:** the key, or `""`.

#### `setKey()` · `frontend/components/prompts/admin-key.tsx`

- **Called by:** `AdminKeyInput` (through `useAdminKey`).
- **Takes in:** `value`, the new key (empty = forget it).
- **What it does:** saves it in memory and in sessionStorage (or removes it), then fires the change event so the page re-renders with it.

#### `AdminKeyInput()` · `frontend/components/prompts/admin-key.tsx`

- **Called by:** `PromptsPage`.
- **Takes in:** `value`, `onChange`.
- **What it does:** a password field labelled "Admin key", hinting it's only needed when the backend sets `ADMIN_API_KEY`.

#### `extractVariables()` · `frontend/lib/template.ts`

- **Called by:** `NewPromptForm`, `VersionEditor`.
- **Takes in:** `template`.
- **What it does:** the same rule as the backend's `extract_variables`: `{{ name }}` placeholders, each name once, in order.
- **Gives back:** e.g. `["company", "product"]`.
- **Debug tips:** if the live chips and the saved version's variables ever differ, the two rules have drifted. Keep them identical.

#### `apiRequest()` · `frontend/lib/api.ts`

- **Called by:** `getModels` and every prompt API function below. **Calls:** `toApiError` on failure.
- **Takes in:**
  - `path` (e.g. `/v1/prompts`);
  - `options`: `method`, `body` (sent as JSON), `adminKey` (sent as `X-Admin-Key`), `signal`.
- **What it does:**
  1. Builds the request: JSON content type only when there's a `body`; a `form` (file upload) is sent as multipart, and the browser sets its content type; admin header only when a key is given.
  2. Sends it.
  3. Not OK → throws `ApiError`.
  4. Otherwise parses the JSON reply.
- **Gives back:** the parsed reply.
- **Can fail with:** `ApiError`, network errors, `AbortError`.

#### `listPrompts()` · `frontend/lib/api.ts`

- **Called by:** `PromptsPage`, `Chat`.
- **Takes in:** `includeArchived`, `signal`.
- **What it does:** `GET /v1/prompts` (with `?include_archived=true` when asked).
- **Gives back:** `{data: PromptSummary[]}`.

#### `getPrompt()` · `frontend/lib/api.ts`

- **Called by:** `PromptDetailPanel`. **Takes in:** `name`. **What it does:** `GET /v1/prompts/{name}` (the name is URL-encoded). **Gives back:** `PromptDetail`.

#### `createPrompt()` · `frontend/lib/api.ts`

- **Called by:** `submit`.
- **Takes in:** `{name, description, template, note}` and `adminKey`.
- **What it does:** `POST /v1/prompts`. **Gives back:** the new `PromptDetail`.

#### `updatePromptDescription()` · `frontend/lib/api.ts`

- **Called by:** the description **Save** in `PromptDetailPanel`.
- **Takes in:** `name`, `description`, `adminKey`.
- **What it does:** `PATCH /v1/prompts/{name}`. **Gives back:** `PromptDetail`.

#### `addPromptVersion()` · `frontend/lib/api.ts`

- **Called by:** `saveVersion`.
- **Takes in:** `name`, `{template, note, activate}`, `adminKey`.
- **What it does:** `POST /v1/prompts/{name}/versions`. **Gives back:** the new `PromptVersion`.

#### `activatePromptVersion()` · `frontend/lib/api.ts`

- **Called by:** `VersionList`'s Make active / Roll back (through `run`).
- **Takes in:** `name`, `version`, `adminKey`.
- **What it does:** `POST /v1/prompts/{name}/activate`. **Gives back:** `PromptDetail`.

#### `archivePrompt()` · `frontend/lib/api.ts`

- **Called by:** the **Archive** button (through `run`).
- **Takes in:** `name`, `adminKey`.
- **What it does:** `DELETE /v1/prompts/{name}`. **Gives back:** `PromptDetail` (archived).

#### `renderPrompt()` · `frontend/lib/api.ts`

- **Called by:** `preview`.
- **Takes in:** `name`, `{version?, variables}`.
- **What it does:** `POST /v1/prompts/{name}/render`. **Gives back:** `{name, version, text, variables}`.

### 13.10 Prompt tests

Run them on SQLite (default) or real Postgres:
`TEST_DATABASE_URL=postgresql+asyncpg://eap:eap@localhost:5432/eap_test uv run pytest`.

#### `database()` · `backend/tests/conftest.py`

- **What it does:** a pytest fixture. Creates a `Database` on `TEST_DATABASE_URL` (default: in-memory SQLite), drops and recreates the tables, hands it to the test, then drops the tables and closes it.
- **Debug tips:** never point it at a database you care about.

#### `prompt_service()` · `backend/tests/conftest.py`

- **What it does:** a pytest fixture. A `PromptService` on a fresh session of the test database.

#### `api_factory()` · `backend/tests/conftest.py`

- **What it does:** a pytest fixture that gives the test a `factory` for building full apps on the test database.
- **Used as:** `async with api_factory(admin_api_key="x") as api:` → `api` is an HTTP client.

#### `factory()` · `backend/tests/conftest.py`

- **Takes in:** settings overrides (e.g. `default_prompt_name="support"`).
- **What it does:** an inner helper of `api_factory`: builds the app with `make_settings(...)` and the test database, wrapped in an `AppClient`.

#### `AppClient.__init__()` · `backend/tests/conftest.py`

- **Takes in:** `app`. **What it does:** keeps the app until the test enters the `async with` block.

#### `AppClient.__aenter__()` · `backend/tests/conftest.py`

- **What it does:** runs the app's startup (`lifespan`), then gives an `httpx.AsyncClient` that calls the app directly in memory.
- **Gives back:** the client.
- **Debug tips:** it runs in the test's own event loop, which async database drivers need.

#### `AppClient.__aexit__()` · `backend/tests/conftest.py`

- **What it does:** closes the client and runs the app's shutdown.

#### `test_extract_variables_in_order_without_duplicates()` · `backend/tests/test_prompt_template.py`

- **Proves:** names come back in first-seen order, once each, with or without spaces inside the braces.

#### `test_extract_ignores_things_that_are_not_variables()` · `backend/tests/test_prompt_template.py`

- **Proves:** `{{ 1abc }}`, `{{ a-b }}`, `{ x }` and `{{}}` aren't variables; `{{ ok_1 }}` is.

#### `test_render_fills_values_and_ignores_extras()` · `backend/tests/test_prompt_template.py`

- **Proves:** values (including numbers) are filled in and unused values are ignored.

#### `test_render_leaves_non_variable_braces_alone()` · `backend/tests/test_prompt_template.py`

- **Proves:** JSON-like braces in a template are left untouched.

#### `test_render_missing_variable_names_all_missing()` · `backend/tests/test_prompt_template.py`

- **Proves:** the error names every missing variable, not just the first.

#### `test_create_makes_version_1_active()` · `backend/tests/test_prompt_service.py`

- **Proves:** a new prompt has version 1, active, with detected variables and `created_by` saved.

#### `test_duplicate_name_conflicts()` · `backend/tests/test_prompt_service.py`

- **Proves:** creating the same name twice raises `PromptConflictError`.

#### `test_new_versions_count_up_and_activate_only_when_asked()` · `backend/tests/test_prompt_service.py`

- **Proves:**
  - Versions number 2, 3.
  - Only `activate=True` switches the active version.
  - The detail lists versions newest first.

#### `test_activate_older_version_is_a_rollback()` · `backend/tests/test_prompt_service.py`

- **Proves:** activating v1 after v2 makes chat render v1's text again.

#### `test_activate_missing_version()` · `backend/tests/test_prompt_service.py`

- **Proves:** activating a version that doesn't exist raises `PromptVersionNotFoundError`.

#### `test_unknown_prompt()` · `backend/tests/test_prompt_service.py`

- **Proves:** an unknown name raises `PromptNotFoundError`.

#### `test_update_description()` · `backend/tests/test_prompt_service.py`

- **Proves:** the description can be changed.

#### `test_archive_hides_and_blocks_use_and_changes()` · `backend/tests/test_prompt_service.py`

- **Proves:** an archived prompt:
  - is hidden from the default list, but shown with `include_archived`;
  - can't get new versions;
  - can't be used in chat.

#### `test_render_specific_version_and_missing_variable()` · `backend/tests/test_prompt_service.py`

- **Proves:**
  - Rendering a pinned old version works.
  - Rendering with no version uses the active one.
  - Missing values raise `PromptVariableError`.

#### `test_default_prompt_applies_only_without_system()` · `backend/tests/test_prompt_service.py`

- **Proves:** `DEFAULT_PROMPT_NAME` applies when a request has no `system`, and is skipped when it has one.

#### `test_no_prompt_and_no_default_returns_none()` · `backend/tests/test_prompt_service.py`

- **Proves:** with no prompt and no default, chat gets no managed prompt.

#### `test_list_summaries()` · `backend/tests/test_prompt_service.py`

- **Proves:** list summaries show the right active version, latest version and active variables per prompt, sorted by name.

#### `test_seed_is_idempotent()` · `backend/tests/test_prompt_service.py`

- **Proves:** the seed creates both examples once; a second run creates nothing.

#### `parse_sse()` · `backend/tests/test_prompts_api.py`

- **What it does:** turns an SSE body into a list of event dictionaries (same as the one in `test_api.py`).

#### `test_prompt_lifecycle()` · `backend/tests/test_prompts_api.py`

- **Proves:** through HTTP, each step works and returns the right data: create, new version, activate, roll back, read a version, edit description, render, list, archive.

#### `test_errors_have_clear_codes()` · `backend/tests/test_prompts_api.py`

- **Proves:** each error has the right status and code:
  - duplicate → 409 `prompt_conflict`;
  - unknown → 404 `prompt_not_found`;
  - bad version → 404 `prompt_version_not_found`;
  - missing variable → 400 `prompt_variable_missing`;
  - bad name → 422.

#### `test_admin_key_required_for_writes_when_configured()` · `backend/tests/test_prompts_api.py`

- **Proves:** with `ADMIN_API_KEY` set:
  - a write with a missing or wrong key → 401;
  - the right key works;
  - reads need no key.

#### `test_without_database_prompts_return_503_and_chat_still_works()` · `backend/tests/test_prompts_api.py`

- **Proves:** with no database:
  - prompt endpoints → 503;
  - a chat that names a prompt → 503;
  - plain chat still works.

#### `test_chat_uses_rendered_prompt_then_extra_system()` · `backend/tests/test_prompts_api.py`

- **Proves:** the model receives the rendered prompt, a blank line, then the request's `system`; the reply records `{name, version}`.

#### `test_chat_with_pinned_version_and_missing_variable()` · `backend/tests/test_prompts_api.py`

- **Proves:**
  - A pinned old version is used even when a newer one is active.
  - No version uses the active one.
  - Missing variables → 400.

#### `test_default_prompt_applies_when_request_has_no_system()` · `backend/tests/test_prompts_api.py`

- **Proves:**
  - The default prompt is used when a request has no `system`.
  - A request with its own `system` gets only that, and no `prompt` on the reply.

#### `test_stream_reports_prompt_and_errors_before_stream()` · `backend/tests/test_prompts_api.py`

- **Proves:**
  - Streaming `start` and `done` events carry `prompt`.
  - The streamed text is the rendered prompt.
  - An unknown prompt on the streaming route is a normal 404, not a broken stream.

#### `test_health_reports_database()` · `backend/tests/test_prompts_api.py`

- **Proves:** `/health` reports `database: "ok"` when the database answers.

---

## 14. Knowledge bases / RAG (Layer 3)

Documents go into **collections**. Each document is parsed to text, split into passages ("chunks"),
and each chunk is turned into 768 numbers (an "embedding") that captures its meaning. A chat question
is embedded the same way. The closest passages, by meaning **and** by shared words, are added to the
system prompt as numbered sources. The order things run in is in
[WORKFLOW_CODE_MAP.md § 14](WORKFLOW_CODE_MAP.md#14-knowledge-bases--rag-layer-3).

How the pieces split up:

| Piece | File | Job |
|---|---|---|
| Routes | `backend/app/api/v1/collections.py` | receive requests, pass them on |
| Rules | `backend/app/rag/service.py` | collections, documents, search, chat context |
| SQL | `backend/app/rag/repository.py` | every query |
| Parsers | `backend/app/rag/parsers.py` | file → text |
| Chunking | `backend/app/rag/chunking.py` | text → passages |
| Embedders | `backend/app/rag/embeddings.py` | text → vectors |
| Retrieval helpers | `backend/app/rag/retrieval.py` | merging results, the Sources block |
| Runtime | `backend/app/rag/runtime.py` | embedder, worker, availability |

### 14.1 Availability, worker and startup

#### `RagRuntime.__init__()` · `backend/app/rag/runtime.py`

- **Called by:** `lifespan` (once per app).
- **Takes in:** `database` (or `None`), `embedder` (or `None`, from `build_embedder`), `settings`.
- **What it does:** keeps them. Creates an `IngestionWorker` only when there is both a database and an embedder.
- **Gives back:** the runtime, stored on `app.state.rag`.
- **Debug tips:** one runtime per app. Every request's `RagService` shares its embedder and worker.

#### `RagRuntime.start()` · `backend/app/rag/runtime.py`

- **Called by:** `lifespan` at startup. **Calls:** `IngestionWorker.start`.
- **What it does:** starts the worker if there is one.
- **Can fail with:** never stops startup; worker problems are logged.

#### `RagRuntime.stop()` · `backend/app/rag/runtime.py`

- **Called by:** `lifespan` at shutdown. **Calls:** `IngestionWorker.stop`, the embedder's `aclose`.
- **What it does:** stops the worker tasks, then closes the embedder's network client.
- **Debug tips:** a document being processed at that moment stays `processing`, and is picked up again on the next start.

#### `RagRuntime.check()` · `backend/app/rag/runtime.py`

- **Called by:** `require_rag_service` (every collections route), `ChatService._retrieve`, `RagRuntime.status`, `IngestionWorker.start`. **Calls:** `Database.has_pgvector`.
- **What it does:** checks, in order, and stops at the first problem:
  1. A database is configured.
  2. It's PostgreSQL.
  3. An embedder is configured.
  4. The `vector` extension is installed. This one is checked against the database once; after it passes, it isn't asked again.
- **Gives back:** nothing when everything is fine.
- **Can fail with:** `RagUnavailableError` → **503** `rag_unavailable`. The message says exactly which step failed and how to fix it.
- **Debug tips:** the quickest diagnosis is `GET /health` (`"rag"`), then any `/v1/collections` call for the reason.

#### `RagRuntime.status()` · `backend/app/rag/runtime.py`

- **Called by:** `health`. **Calls:** `check`.
- **Gives back:**
  - `"disabled"`: no database or no embedder;
  - `"ok"`: `check` passed;
  - `"unavailable"`: `check` failed.

#### `RagRuntime.enqueue()` · `backend/app/rag/runtime.py`

- **Called by:** `RagService._add`, `reindex_document`, `reindex_collection`.
- **Takes in:** `document_id`.
- **What it does:** hands the id to the worker's queue (does nothing if there's no worker).

#### `IngestionWorker.__init__()` · `backend/app/rag/runtime.py`

- **Called by:** `RagRuntime.__init__`. **Takes in:** `runtime`.
- **What it does:** creates an empty queue of document ids and an empty task list.

#### `IngestionWorker.start()` · `backend/app/rag/runtime.py`

- **Called by:** `RagRuntime.start`. **Calls:** `_run`, `RagRuntime.check`, `RagRepository.unfinished_document_ids`, `enqueue`.
- **What it does:**
  1. Starts `RAG_INGEST_CONCURRENCY` background tasks (default 2), each running `_run`.
  2. If RAG is usable, finds documents still `pending`/`processing` (interrupted by the last shutdown) and queues them again.
- **Can fail with:** never raises. If RAG isn't usable yet, it logs `ingestion worker: not re-queuing documents (...)`.
- **Debug tips:** after a restart, look for `re-queued N unfinished documents` in the log.

#### `IngestionWorker.enqueue()` · `backend/app/rag/runtime.py`

- **Called by:** `RagRuntime.enqueue`, `start`. **Takes in:** `document_id`. **What it does:** puts it on the queue (never blocks).

#### `IngestionWorker._run()` · `backend/app/rag/runtime.py`

- **Called by:** each background task, forever. **Calls:** `RagService.process_document`.
- **What it does:** waits for the next id, opens a **new database session** for that job, processes the document, and marks the queue item done. Any unexpected error is logged (`ingestion failed for document ...`) and the loop carries on.
- **Debug tips:** expected failures (bad embedder key, model mismatch) don't show up here; they're saved on the document as `failed` with an `error`.

#### `IngestionWorker.drain()` · `backend/app/rag/runtime.py`

- **Called by:** scripts or tests that must wait for indexing to finish.
- **What it does:** waits until every queued document has been processed.

#### `IngestionWorker.stop()` · `backend/app/rag/runtime.py`

- **Called by:** `RagRuntime.stop`. **What it does:** cancels the background tasks and waits for them to end.

#### `Database.is_postgres()` · `backend/app/db/session.py`

- **Called by:** `RagRuntime.check`, `has_pgvector`, `_tables`, `create_all`. It's a read-only property, used as `db.is_postgres`.
- **Gives back:** `True` if the engine talks to PostgreSQL.

#### `Database.has_pgvector()` · `backend/app/db/session.py`

- **Called by:** `RagRuntime.check`.
- **What it does:** on Postgres, asks `pg_extension` whether `vector` is installed.
- **Gives back:** `True`/`False` (always `False` on other databases).
- **Can fail with:** a connection error, which `check` turns into 503 "Can't reach the database".

#### `Database._tables()` · `backend/app/db/session.py`

- **Called by:** `create_all`, `drop_all` (tests).
- **What it does:** loads both model modules, so every table is known. On Postgres it returns every table; otherwise it leaves out tables marked `postgres_only` (the three knowledge-base tables, which need pgvector types).
- **Gives back:** a list of tables in dependency order.

### 14.2 Embedders

#### `build_embedder()` · `backend/app/rag/embeddings.py`

- **Called by:** `lifespan` (only when there's a database).
- **Takes in:** `settings`.
- **What it does:**
  1. Picks the provider: `EMBEDDING_PROVIDER` if set; otherwise the first configured of OpenAI (key), Gemini (key), Ollama (URL), mock (`ENABLE_MOCK_PROVIDER`).
  2. Picks the model: `EMBEDDING_MODEL`, or the provider's default (`text-embedding-3-small`, `gemini-embedding-001`, `nomic-embed-text`, `hash`).
  3. Creates that embedder and logs `embedding model: <id>`.
- **Gives back:** an `Embedder`, or `None`. `None` comes with a log line saying why: no provider at all, an unknown name, or a chosen provider that's missing its key.
- **Debug tips:** adding an OpenAI key later switches the automatic choice from mock to OpenAI. Existing collections then need re-indexing (409 `embedding_model_mismatch`). Set `EMBEDDING_PROVIDER` explicitly in production.

#### `Embedder.__init__()` · `backend/app/rag/embeddings.py`

- **Called by:** every embedder's constructor. **Takes in:** `model`.
- **What it does:** stores the model name and the fixed `dimensions` (768).

#### `Embedder.id()` · `backend/app/rag/embeddings.py`

- **Read-only property** (`embedder.id`). **Called by:** `RagService` (stored on collections, compared on every search and indexing job), logs.
- **Gives back:** `"<provider>/<model>@<dimensions>"`, e.g. `"openai/text-embedding-3-small@768"`.

#### `Embedder.embed()` · `backend/app/rag/embeddings.py`

- **Called by:** `RagService.process_document` (`kind="document"`), `RagService.search` (`kind="query"`). **Calls:** the provider's `_embed_batch`, `normalize`.
- **Takes in:**
  - `texts`: a list of strings;
  - `kind`: `"document"` or `"query"`. Some providers embed questions and passages slightly differently.
- **What it does:**
  1. Sends the texts in batches of `batch_size` (64; Ollama 32).
  2. Checks that each batch returned one vector per text, each with exactly 768 numbers.
  3. Scales every vector to length 1.
- **Gives back:** one vector (768 floats) per text, in the same order.
- **Can fail with:**
  - the provider's mapped errors (`ProviderAuthError`, `ProviderRateLimitError`...);
  - `ProviderError` "returned N numbers, expected 768" when the model can't produce 768 dimensions.
- **Debug tips:** an indexing job that fails this way stores the message on the document's `error`.

#### `Embedder._embed_batch()` · `backend/app/rag/embeddings.py`

- **The contract** each embedder fills in.
- **Takes in:** one batch of texts and the `kind`.
- **Gives back:** the provider's raw vectors.

#### `Embedder.aclose()` · `backend/app/rag/embeddings.py`

- **Called by:** `RagRuntime.stop`. Does nothing by default (the mock has nothing to close).

#### `OpenAIEmbedder.__init__()` · `backend/app/rag/embeddings.py`

- **Takes in:** `api_key`, `model`, `timeout_s`, `max_retries`, `base_url` (Azure/compatible servers), `http_client` (tests).
- **What it does:** creates an official async OpenAI client, the same way the chat adapter does.

#### `OpenAIEmbedder._embed_batch()` · `backend/app/rag/embeddings.py`

- **What it does:** calls `embeddings.create(model, input=texts, dimensions=768)` and sorts the results by their `index`, so the order always matches the input.
- **Can fail with:** SDK errors, mapped by the OpenAI chat adapter's `_map_error` (same codes as chat).

#### `OpenAIEmbedder.aclose()` · `backend/app/rag/embeddings.py`

- **What it does:** closes the OpenAI client.

#### `GeminiEmbedder.__init__()` · `backend/app/rag/embeddings.py`

- **Takes in:** `api_key`, `model`, `timeout_s`, `max_retries`, `http_client`.
- **What it does:** creates a `google-genai` client. As in the chat adapter, the timeout is converted to milliseconds and attempts = retries + 1.

#### `GeminiEmbedder._embed_batch()` · `backend/app/rag/embeddings.py`

- **What it does:** calls `embed_content` with `output_dimensionality=768`, and `task_type` `RETRIEVAL_QUERY` for questions or `RETRIEVAL_DOCUMENT` for passages, then reads each embedding's `values`.
- **Can fail with:** errors mapped by the Gemini chat adapter's `_map_error`.
- **Debug tips:** Gemini doesn't normalize reduced-size vectors; `embed` does that for it.

#### `GeminiEmbedder.aclose()` · `backend/app/rag/embeddings.py`

- **What it does:** closes the async Gemini client.

#### `OllamaEmbedder.__init__()` · `backend/app/rag/embeddings.py`

- **Takes in:** `base_url`, `model`, `timeout_s`, `http_client`.
- **What it does:** uses the given HTTP client or creates one for the Ollama server.

#### `OllamaEmbedder._embed_batch()` · `backend/app/rag/embeddings.py`

- **What it does:**
  1. For `nomic-embed*` models, adds the prefixes the model was trained with: `search_query: ` for questions, `search_document: ` for passages.
  2. Sends `POST /api/embed` with `{model, input}` and reads `embeddings`.
- **Can fail with:**
  - HTTP errors via the Ollama adapter's `_raise_for_status`, e.g. 404 → "pull the model first";
  - network errors via its `_map_error`.

#### `OllamaEmbedder.aclose()` · `backend/app/rag/embeddings.py`

- **What it does:** closes the HTTP client.

#### `MockEmbedder.__init__()` · `backend/app/rag/embeddings.py`

- **Takes in:** `model` (default `"hash"`; tests use `"hash-v2"` to simulate a model change).

#### `MockEmbedder._embed_batch()` · `backend/app/rag/embeddings.py`

- **What it does:** `_vector` for each text. No network.

#### `MockEmbedder._vector()` · `backend/app/rag/embeddings.py`

- **Takes in:** `text`.
- **What it does:**
  1. Splits the text into lowercase words.
  2. Hashes each word to one of the 768 positions and adds +1 or −1 there.
  3. Texts that share words get similar vectors. Words with the same meaning but different spelling do **not**; it's not a real model.
  4. An empty text gets a single 1 so the vector is never all zeros (cosine distance can't be computed for a zero vector).
- **Gives back:** a 768-number vector (not yet normalized).

#### `normalize()` · `backend/app/rag/embeddings.py`

- **Called by:** `Embedder.embed`.
- **Takes in:** a vector.
- **What it does:** divides every number by the vector's length, so the length becomes 1. A zero vector is returned unchanged.
- **Gives back:** e.g. `[3, 4]` → `[0.6, 0.8]`.

### 14.3 Parsing files

#### `detect_type()` · `backend/app/rag/parsers.py`

- **Called by:** `extract_text`.
- **Takes in:** `filename`, and `content_type` (what the browser said).
- **What it does:**
  1. Looks the extension up first: `.txt .text .md .markdown .pdf .docx .html .htm`.
  2. If that doesn't match, tries the content type, ignoring `; charset=...`.
- **Gives back:** a MIME type, e.g. `"text/markdown"`.
- **Can fail with:** `UnsupportedFileTypeError` → **415**, naming the file and the accepted types.

#### `extract_text()` · `backend/app/rag/parsers.py`

- **Called by:** `RagService.add_file`, in a worker thread so a big file doesn't block other requests. **Calls:** `detect_type`, one `_from_*` reader, `normalize_text`.
- **Takes in:** `filename`, `content_type`, `data` (the raw bytes).
- **What it does:**
  1. Picks the reader for the type.
  2. Gets a title and text from it.
  3. Cleans the text.
  4. Rejects empty text, with a hint about scans for PDFs.
  5. Rejects more than 5,000,000 characters.
  6. If there's no title, uses the file name without its extension. Titles are cut to 300 characters.
- **Gives back:** `ExtractedDocument(title, text, mime_type)`.
- **Can fail with:** `UnsupportedFileTypeError` (415), `DocumentParseError` (422).

#### `normalize_text()` · `backend/app/rag/parsers.py`

- **Called by:** `extract_text`, `RagService.add_text`.
- **What it does:**
  1. Turns Windows/Mac line ends into `\n`.
  2. Removes NUL characters (Postgres can't store them in text).
  3. Strips trailing spaces from each line.
  4. Collapses 3+ newlines into one blank line.
  5. Trims the ends.
- **Gives back:** e.g. `"a  \n\n\n\nb\r\n"` → `"a\n\nb"`.

#### `_decode()` · `backend/app/rag/parsers.py`

- **Called by:** the text, Markdown and HTML readers.
- **What it does:** decodes the bytes as UTF-8, dropping a BOM. If that fails, uses Latin-1, which never fails and suits old Windows text files.

#### `_from_text()` · `backend/app/rag/parsers.py`

- **Gives back:** no title, and the decoded text.

#### `_from_markdown()` · `backend/app/rag/parsers.py`

- **Gives back:** the first `# Heading` as the title (if there is one), and the decoded text, markdown kept as-is.

#### `_from_pdf()` · `backend/app/rag/parsers.py`

- **What it does:**
  1. Opens the PDF with `pypdf`. If it's encrypted, tries an empty password.
  2. Extracts each page's text and joins pages with blank lines.
  3. Uses the PDF's metadata title, if set.
- **Can fail with:** `DocumentParseError` for:
  - a password-protected PDF;
  - an unreadable or corrupt file.
- **Debug tips:** scanned PDFs are images with no text layer. They pass here but produce empty text, which `extract_text` rejects with a hint.

#### `_from_docx()` · `backend/app/rag/parsers.py`

- **What it does:**
  1. Opens the Word file with `python-docx`.
  2. Takes every paragraph's text, then every table row as `cell | cell | cell`.
  3. Uses the document's title property, if set.
- **Can fail with:** `DocumentParseError` when the file isn't a valid `.docx` (it's a zip inside).
- **Debug tips:** text in headers, footers and text boxes isn't read.

#### `_from_html()` · `backend/app/rag/parsers.py`

- **What it does:** runs the `_HTMLText` parser over the decoded page.
- **Gives back:** the `<title>`, and the visible text.

#### `_HTMLText.__init__()` · `backend/app/rag/parsers.py`

- **What it does:** sets up an HTML parser that converts entities (`&pound;` → `£`), with an empty list of text parts, no title, and a counter for skipped regions.

#### `_HTMLText.handle_starttag()` · `backend/app/rag/parsers.py`

- **What it does:**
  - `<script>`, `<style>`, `<noscript>`, `<template>`, `<svg>` → start skipping text;
  - `<title>` → start collecting the title;
  - block tags (`p`, `div`, `li`, headings...) → add a line break, so the text keeps its paragraphs.

#### `_HTMLText.handle_endtag()` · `backend/app/rag/parsers.py`

- **What it does:** the matching end of each case in `handle_starttag`.

#### `_HTMLText.handle_data()` · `backend/app/rag/parsers.py`

- **What it does:** ignores text inside skipped regions, adds title text to `title`, and keeps everything else.

#### `_HTMLText.text()` · `backend/app/rag/parsers.py`

- **Gives back:** the collected text with runs of spaces collapsed on each line (blank-line cleanup happens in `normalize_text`).

### 14.4 Chunking

#### `split_text()` · `backend/app/rag/chunking.py`

- **Called by:** `RagService.process_document`. **Calls:** `_fit`, `_tail`.
- **Takes in:**
  - `text`;
  - `size`: maximum characters per chunk (`RAG_CHUNK_SIZE`, 1200);
  - `overlap`: characters repeated between chunks (`RAG_CHUNK_OVERLAP`, 200, capped at half of `size`).
- **What it does:**
  1. Splits the text into paragraphs (at blank lines).
  2. Makes sure each paragraph fits (`_fit`).
  3. Packs paragraphs into chunks until the next one wouldn't fit.
  4. Starts each new chunk with the end of the previous one (`_tail`), when that still fits.
- **Gives back:** a list of chunk strings, each at most `size` characters, in order. Empty text → `[]`.
- **Debug tips:**
  - Smaller chunks give more precise matches but less context per source.
  - About 1,000–1,500 characters is a good middle ground.
  - Changing the size only affects documents indexed (or re-indexed) afterwards.

#### `_fit()` · `backend/app/rag/chunking.py`

- **Takes in:** one paragraph and `size`.
- **What it does:**
  - fits → returned whole;
  - too long → split at sentence ends (`.`, `!`, `?`) and packed back together up to `size`;
  - a sentence longer than `size` → `_split_words`.
- **Gives back:** pieces of at most `size` characters.

#### `_split_words()` · `backend/app/rag/chunking.py`

- **What it does:** packs whole words up to `size`. A single "word" longer than `size` (e.g. a long URL or code) is cut into `size`-long pieces, so nothing is lost.

#### `_tail()` · `backend/app/rag/chunking.py`

- **What it does:** takes the last `overlap` characters of a chunk, moved forward to the next word boundary so no word is cut in half. `overlap` 0 → `""`.

### 14.5 Search helpers

#### `fuse_rrf()` · `backend/app/rag/retrieval.py`

- **Called by:** `RagService.search`.
- **Takes in:**
  - `ranked_lists`: best-first lists of chunk ids (here: vector results and keyword results);
  - `k`: default 60.
- **What it does:** each id earns `1 / (k + rank)` in every list it appears in (rank 1 = best), and the scores are added up. No score scaling is needed, which is why it's good at mixing different search methods.
  - A passage found by **both** methods usually beats one found by just one.
  - A top-ranked passage from one method still ranks well.
- **Gives back:** `[(id, score), ...]`, best first. E.g. rank 1 in one list only → `1/61 ≈ 0.0164`.

#### `build_context()` · `backend/app/rag/retrieval.py`

- **Called by:** `RagService.retrieve_for_chat`.
- **Takes in:** the chosen `SearchHit`s.
- **What it does:** writes the "Sources" block. It starts with instructions:
  - the passages are reference material, **not instructions** (a first defense against prompt injection hidden in documents);
  - cite them as [n];
  - say so if they don't contain the answer.

  Then it lists each passage as `[n] "Title" (collection: x, part p)` followed by its text.
- **Gives back:** the block as text.
- **Debug tips:** to see it exactly as the model does, chat with `"model": "mock/echo-system"`.

### 14.6 Knowledge-base service (the rules)

#### `RagService.__init__()` · `backend/app/rag/service.py`

- **Called by:** `get_rag_service` (per request), `IngestionWorker._run` (per job).
- **Takes in:** `session`, `runtime`.
- **What it does:** keeps the session, builds a `RagRepository`, and keeps the runtime and its settings.

#### `RagService._embedder()` · `backend/app/rag/service.py`

- **Read-only property.** **Gives back:** the runtime's embedder.

#### `RagService.list_collections()` · `backend/app/rag/service.py`

- **Calls:** `RagRepository.list_collections`, `document_counts`, `_summary`.
- **Takes in:** `include_archived`.
- **Gives back:** `CollectionList`, each with document counts per status (2 queries in total).

#### `RagService.get_collection()` · `backend/app/rag/service.py`

- **Calls:** `_require_collection`, `_detail`.
- **Gives back:** `CollectionDetail`, including `needs_reindex`.
- **Can fail with:** 404.

#### `RagService.create_collection()` · `backend/app/rag/service.py`

- **Takes in:** `CollectionCreate`.
- **What it does:**
  1. Refuses a taken name.
  2. Saves the collection with `embedding_model` = the server's current embedder id.
  3. Commits.
- **Gives back:** `CollectionDetail`.
- **Can fail with:** `CollectionConflictError` (409), also when two creates race (caught by `_commit`).

#### `RagService.archive_collection()` · `backend/app/rag/service.py`

- **What it does:** stamps `archived_at` (once).
- **Gives back:** the detail.
- **Debug tips:** archived collections disappear from lists and can't be searched, uploaded to or re-indexed (409). Their data is kept.

#### `RagService.reindex_collection()` · `backend/app/rag/service.py`

- **What it does:**
  1. Sets the collection's `embedding_model` to the server's current one.
  2. Resets every document to `pending`.
  3. Commits, then queues them all.
- **Gives back:** the detail (**202**). Documents become `ready` as the worker gets through them.
- **When to use:** after changing `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`, or the chunk settings.

#### `RagService.list_documents()` · `backend/app/rag/service.py`

- **Gives back:** `DocumentList`, newest first.
- **Can fail with:** 404 for an unknown collection.

#### `RagService.get_document()` · `backend/app/rag/service.py`

- **Takes in:** collection name, document id.
- **Gives back:** `DocumentOut` (status, error, chunk count).
- **Can fail with:** 404 if the document isn't in **that** collection.

#### `RagService.add_file()` · `backend/app/rag/service.py`

- **Called by:** `upload_documents` (once per file). **Calls:** `_require_collection(writable)`, `extract_text` (in a thread), `_add`.
- **Takes in:** collection name, `filename`, `content_type`, `data` (bytes).
- **What it does:**
  1. Refuses files over `RAG_MAX_UPLOAD_MB`.
  2. Extracts the text off the main thread.
  3. Saves the document.
- **Gives back:** `DocumentOut` with status `pending`.
- **Can fail with:** 404, 409 (archived or duplicate), 413, 415, 422.

#### `RagService.add_text()` · `backend/app/rag/service.py`

- **Called by:** `add_text_document`.
- **Takes in:** collection name, `DocumentTextCreate`.
- **What it does:** cleans the pasted text, then saves it like a file (type `text/plain`, size = UTF-8 bytes).

#### `RagService.delete_document()` · `backend/app/rag/service.py`

- **What it does:** deletes the document. The database's cascade rule deletes its chunks too.
- **Gives back:** nothing (**204**).
- **Debug tips:** a document deleted while queued is skipped by the worker (`process_document` finds nothing).

#### `RagService.reindex_document()` · `backend/app/rag/service.py`

- **What it does:** resets the document to `pending` (clearing its error), commits, and queues it.
- **Gives back:** `DocumentOut` (**202**).

#### `RagService.process_document()` · `backend/app/rag/service.py`

- **Called by:** `IngestionWorker._run`, in its own session. **Calls:** `split_text`, `Embedder.embed`, `RagRepository.replace_chunks`.
- **Takes in:** `document_id`.
- **What it does:**
  1. Loads the document; gone → stop quietly.
  2. Sets `processing` and commits, so the UI can show it.
  3. Checks the collection's embedding model equals the server's.
  4. Splits the text, embeds all chunks (`kind="document"`), and replaces the old chunks with the new ones.
  5. Sets `ready` and `chunk_count`, and commits, all in one transaction, so search never sees half a document.
  6. **Any error:** rolls back; sets `failed`, the reason in `error`, and `chunk_count` 0; commits; logs `document failed: ...`.
- **Gives back:** nothing.
- **Debug tips:** read the document's `error` field in the Knowledge page or `GET .../documents/{id}`.

#### `RagService.search_collection()` · `backend/app/rag/service.py`

- **Called by:** the test-search route.
- **What it does:** runs `search` on one collection with the request's `top_k` (or `RAG_TOP_K`).
- **Gives back:** `SearchResponse` (query, collection, embedding model, hits).

#### `RagService.search()` · `backend/app/rag/service.py`

- **Called by:** `search_collection`, `retrieve_for_chat`. **Calls:** `_require_collection`, `Embedder.embed`, `RagRepository.vector_search`, `RagRepository.keyword_search`, `fuse_rrf`, `_round`.
- **Takes in:** collection `names` (duplicates ignored), `query`, `top_k`.
- **What it does:**
  1. Loads each collection; unknown → 404, archived → 409.
  2. Each collection's embedding model must match the server's → otherwise 409 telling you to re-index.
  3. Embeds the question (`kind="query"`).
  4. Runs vector search and keyword search, each limited to `RAG_CANDIDATES` (only `ready` documents).
  5. Merges them with `fuse_rrf` and keeps the top `top_k`.
- **Gives back:** a list of `SearchHit`. Each has its merged `score`, plus `vector_similarity` and/or `keyword_score`, showing which method found it.
- **Debug tips:**
  - `keyword_score: null` means no shared words: it was found by meaning only.
  - Vector search always returns its nearest passages, even weak ones. Judge relevance by `vector_similarity`.

#### `RagService.retrieve_for_chat()` · `backend/app/rag/service.py`

- **Called by:** `ChatService._retrieve`. **Calls:** `search`, `build_context`.
- **Takes in:** `ref` (`RagRef`), `query` (the user's last message).
- **What it does:**
  1. Searches with `ref.top_k` (or `RAG_TOP_K`).
  2. **Always** ends the read transaction afterwards, so no database connection is held during the LLM call.
  3. Turns hits into citations numbered from 1, each with a 300-character snippet.
  4. Builds the Sources block.
- **Gives back:** `RetrievedContext(hits, context, citations)`; with no hits, an empty context and no citations.

#### `RagService._add()` · `backend/app/rag/service.py`

- **Called by:** `add_file`, `add_text`. **Calls:** `RagRepository.find_by_hash`, `RagRepository.add`, `_commit`, `RagRuntime.enqueue`, `_document_out`.
- **What it does:**
  1. Hashes the extracted text (SHA-256). The same text already in the collection → 409, even if it came in a different file format.
  2. Saves a `pending` document, keeping the text for re-indexing, and touches the collection's `updated_at`.
  3. Commits, then queues the document.
- **Gives back:** `DocumentOut`.
- **Can fail with:** `DuplicateDocumentError` (409), also when two identical uploads race (unique rule in `_commit`).

#### `RagService._require_collection()` · `backend/app/rag/service.py`

- **Takes in:** `name`, `writable` (true for uploads, re-index and search).
- **Gives back:** the `Collection` row.
- **Can fail with:** `CollectionNotFoundError` (404); `CollectionConflictError` (409) when `writable` and archived.

#### `RagService._require_document()` · `backend/app/rag/service.py`

- **Takes in:** a collection row, a document id.
- **Gives back:** the `Document` row.
- **Can fail with:** `DocumentNotFoundError` (404) if it doesn't exist or belongs to another collection.

#### `RagService._detail()` · `backend/app/rag/service.py`

- **What it does:** builds the summary with its counts, adds the server's embedding model, and sets `needs_reindex` when they differ.

#### `RagService._commit()` · `backend/app/rag/service.py`

- **Takes in:** `duplicate_message` (for document saves).
- **What it does:** commits. If a unique rule is broken, rolls back and raises a clear conflict:
  - with `duplicate_message` → `DuplicateDocumentError` (document saves);
  - without it → `CollectionConflictError` (collection names).

#### `_reset()` · `backend/app/rag/service.py`

- **What it does:** sets a document back to `pending` with no error (before it's queued again).

#### `_round()` · `backend/app/rag/service.py`

- **What it does:** rounds a score to 6 decimals, keeping `None` as `None`.

#### `_summary()` · `backend/app/rag/service.py`

- **Takes in:** a collection row and its `{status: count}`.
- **Gives back:** `CollectionSummary` (missing statuses count as 0).

#### `_document_out()` · `backend/app/rag/service.py`

- **Gives back:** the API view of a document row. The stored text and hash are left out.

### 14.7 Knowledge-base repository (the SQL)

#### `RagRepository.__init__()` · `backend/app/rag/repository.py`

- **Takes in:** `session`. **What it does:** keeps it.

#### `RagRepository.list_collections()` · `backend/app/rag/repository.py`

- **What it does:** collections ordered by name; archived ones only if asked.

#### `RagRepository.get_collection()` · `backend/app/rag/repository.py`

- **What it does:** finds a collection by exact name (indexed).
- **Gives back:** the row or `None`.

#### `RagRepository.document_counts()` · `backend/app/rag/repository.py`

- **What it does:** one grouped query counting documents per (collection, status).
- **Gives back:** `{collection_id: {"ready": 3, "failed": 1}}`.

#### `RagRepository.list_documents()` · `backend/app/rag/repository.py`

- **What it does:** a collection's documents, newest first.

#### `RagRepository.get_document()` · `backend/app/rag/repository.py`

- **What it does:** a document by id, or `None`.

#### `RagRepository.find_by_hash()` · `backend/app/rag/repository.py`

- **What it does:** a document in the collection with the same content hash, or `None` (used for duplicate detection).

#### `RagRepository.unfinished_document_ids()` · `backend/app/rag/repository.py`

- **What it does:** ids of documents still `pending`/`processing`, oldest first (re-queued at startup).

#### `RagRepository.replace_chunks()` · `backend/app/rag/repository.py`

- **What it does:** deletes the document's existing chunks and stages the new ones. Both happen in the caller's transaction, so they take effect together on commit.

#### `RagRepository.vector_search()` · `backend/app/rag/repository.py`

- **Takes in:** collection ids, the question's `embedding`, `limit`.
- **What it does:** orders chunks of `ready` documents in those collections by **cosine distance** to the question (`<=>`, using the HNSW index).
- **Gives back:** `ChunkMatch(chunk, document_title, collection_name, score)`, where `score` = similarity = 1 − distance (1 = identical direction).
- **Debug tips:** HNSW is an approximate index: very fast, and occasionally misses a true nearest passage.

#### `RagRepository.keyword_search()` · `backend/app/rag/repository.py`

- **Takes in:** collection ids, the question text, `limit`.
- **What it does:**
  1. Turns the question into a full-text query. English stemming applies, so "warranties" matches "warranty", and stop words like "how", "is", "the" are dropped.
  2. Joins the words with **OR** (`plainto_tsquery`, then `&` → `|`). Postgres's own parsers require every word, which would miss a passage that lacks one of them.
  3. Keeps chunks whose `search_vector` matches, ranked by `ts_rank_cd`, which rewards more and closer matches.
- **Gives back:** `ChunkMatch` list, `score` = the rank.
- **Debug tips:** a question made only of stop words matches nothing here, so results come from vector search alone.

#### `RagRepository.add()` · `backend/app/rag/repository.py`

- **What it does:** stages a new collection or document (written on commit).

#### `RagRepository.delete()` · `backend/app/rag/repository.py`

- **What it does:** deletes a document row; Postgres deletes its chunks (`ON DELETE CASCADE`).

### 14.8 Request dependencies, routes and chat

#### `get_rag_service()` · `backend/app/api/deps.py`

- **Called by:** FastAPI, for `require_rag_service` and `get_chat_service`.
- **Takes in:** `request`, `session` (from `get_db_session`).
- **Gives back:** a `RagService` on this request's session, or `None` when there's no database or no embedder.
- **Debug tips:** it shares the session with the prompt service in the same request.

#### `require_rag_service()` · `backend/app/api/deps.py`

- **Called by:** FastAPI, for every `/v1/collections` route.
- **What it does:** runs `RagRuntime.check()`, then gives back the service.
- **Can fail with:** **503** `rag_unavailable` with the exact reason.

#### `ChatService._retrieve()` · `backend/app/services/chat_service.py`

- **Called by:** `ChatService.complete`, `ChatService.stream`, right after `_build_system` and before the provider is called. **Calls:** `RagRuntime.check`, `RagService.retrieve_for_chat`.
- **Takes in:** the `ChatRequest`, and the `system` text built so far.
- **What it does:**
  1. No `rag` in the request → returns `system` unchanged and no citations.
  2. Checks RAG is usable (503 with the reason otherwise).
  3. Searches with the **last user message** as the question.
  4. Hits → appends the Sources block after the system text (or uses it alone).
  5. No hits → leaves the system text alone and returns `citations: []`.
- **Gives back:** `(system_text, citations or None)`.
- **Can fail with:** 503, 404 (unknown collection), 409 (archived or model mismatch). These all happen before the LLM call, so streams fail with normal HTTP errors.
- **Debug tips:**
  - Only the last message is searched. A follow-up like "and for managers?" may retrieve poorly.
  - Query rewriting is a future improvement.

#### `list_collections()` · `backend/app/api/v1/collections.py`

- **Route:** `GET /v1/collections?include_archived=false`. **Gives back:** `CollectionList`.

#### `create_collection()` · `backend/app/api/v1/collections.py`

- **Route:** `POST /v1/collections` (admin).
- **Takes in:** `{name, description}`.
- **Gives back:** **201** `CollectionDetail`.
- **Errors:** 422 bad name, 409 taken, 401.

#### `get_collection()` · `backend/app/api/v1/collections.py`

- **Route:** `GET /v1/collections/{name}`. **Gives back:** `CollectionDetail`.
- **Errors:** 404.

#### `archive_collection()` · `backend/app/api/v1/collections.py`

- **Route:** `DELETE /v1/collections/{name}` (admin). **Gives back:** the detail with `archived: true`.

#### `reindex_collection()` · `backend/app/api/v1/collections.py`

- **Route:** `POST /v1/collections/{name}/reindex` (admin). **Gives back:** **202** detail.

#### `list_documents()` · `backend/app/api/v1/collections.py`

- **Route:** `GET /v1/collections/{name}/documents`. **Gives back:** `DocumentList`.

#### `upload_documents()` · `backend/app/api/v1/collections.py`

- **Route:** `POST /v1/collections/{name}/documents` (admin), multipart with one or more `files` fields.
- **What it does:**
  1. Reads each file and calls `RagService.add_file`.
  2. **Several files:** a refused file is listed in `errors` (`filename`, `code`, `message`) and the others continue.
  3. **One file, or an unknown collection:** the error is returned as a normal error status.
- **Gives back:** **202** `UploadResult`.
- **Debug tips:** `curl -F files=@a.pdf -F files=@b.md localhost:8000/v1/collections/hr/documents`.

#### `add_text_document()` · `backend/app/api/v1/collections.py`

- **Route:** `POST /v1/collections/{name}/documents/text` (admin), `{title, text}`. **Gives back:** **202** `DocumentOut`.

#### `get_document()` · `backend/app/api/v1/collections.py`

- **Route:** `GET /v1/collections/{name}/documents/{id}`. **Gives back:** `DocumentOut` (poll this for status).

#### `delete_document()` · `backend/app/api/v1/collections.py`

- **Route:** `DELETE /v1/collections/{name}/documents/{id}` (admin). **Gives back:** **204**, no body.

#### `reindex_document()` · `backend/app/api/v1/collections.py`

- **Route:** `POST /v1/collections/{name}/documents/{id}/reindex` (admin). **Gives back:** **202** `DocumentOut`.

#### `search_collection()` · `backend/app/api/v1/collections.py`

- **Route:** `POST /v1/collections/{name}/search`, `{query, top_k?}`.
- **Gives back:** `SearchResponse`. No LLM is called.
- **Debug tips:** the fastest way to see why an answer used (or missed) a passage.

#### `upgrade()` · `backend/migrations/versions/0002_create_rag.py`

- **Called by:** `alembic upgrade head`.
- **What it does:**
  1. Enables the `vector` extension (needs a user allowed to create extensions).
  2. Creates `collections` (unique name), `documents` (unique per collection + content hash), and `chunks`. Chunks hold a `vector(768)` column and a `tsvector` column that Postgres computes from the text.
  3. Adds indexes: HNSW (cosine) on embeddings, GIN on the full-text column, and plain indexes on the ids.
- **Can fail with:**
  - "permission denied to create extension" → run it as a superuser once;
  - "could not open extension control file" → install pgvector on the server.

#### `downgrade()` · `backend/migrations/versions/0002_create_rag.py`

- **What it does:** drops the three tables (all knowledge-base data is lost). The `vector` extension is left installed.

### 14.9 Frontend

#### `Knowledge()` · `frontend/app/knowledge/page.tsx`

- **What it does:** the `/knowledge` route. It sets the tab title and renders `KnowledgePage`.

#### `KnowledgePage()` · `frontend/components/knowledge/knowledge-page.tsx`

- **Calls:** `useAdminKey`, `listCollections`, `NewCollectionForm`, `CollectionPanel`.
- **What it does:** the same layout as the Prompts page:
  - the header: title, admin key, **New collection**;
  - an error banner;
  - the list on the left: name, `ready/total`, or "archived", description; plus "Show archived";
  - the selected collection's panel on the right.

  The load-list effect re-runs when "Show archived" changes and after `reload`.

#### `reload()` · `frontend/components/knowledge/knowledge-page.tsx`

- **Called by:** `CollectionPanel` (after changes and while polling), and after creating a collection.
- **What it does:** bumps a counter that re-runs the load-list effect, so counts stay current.

#### `NewCollectionForm()` · `frontend/components/knowledge/knowledge-page.tsx`

- **Takes in:** `adminKey`, `onCreated(name)`, `onCancel()`.
- **What it does:** name (format checked by the browser) and description fields.

#### `submit()` · `frontend/components/knowledge/knowledge-page.tsx`

- **What it does:** `createCollection`, then opens the new collection. On failure, shows the error (e.g. name taken).

#### `CollectionPanel()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** `name`, `adminKey`, `onChanged` (the page's `reload`).
- **What it does:**
  1. **Load effect:** `getCollection` + `listDocuments` together, on first view and after every change.
  2. **Polling effect:** while any document is `pending`/`processing`, reloads every 2 seconds and refreshes the list. It stops by itself when everything is `ready`/`failed`.
  3. Header:
     - name and **Archive**;
     - counts, the embedding model, and the JSON snippet for chat;
     - a yellow **Re-index collection** banner when `needs_reindex`.
  4. Then `UploadBox`, `PasteTextForm` (hidden for archived collections), `DocumentTable`, `SearchTester`.

#### `run()` · `frontend/components/knowledge/collection-panel.tsx`

- **What it does:** like the Prompts page's `run`:
  1. Marks the panel busy and runs the action.
  2. **Success:** refreshes the panel and the list, and returns the result.
  3. **Failure:** shows the error and returns `undefined`.

#### `UploadBox()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** `busy`, `onUpload(files)`.
- **What it does:** a drop zone and a **Choose files** button (a hidden file input that accepts `.txt .md .markdown .pdf .docx .html .htm`, several at once). After an upload, refused files are listed with their reasons.

#### `upload()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** a list of `File`s.
- **What it does:** clears old errors, calls `onUpload`, shows any refused files, and resets the file input (so the same file can be picked again).

#### `onDrop()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** the drop event.
- **What it does:** stops the browser from opening the file, ends the highlight, and uploads the dropped files.

#### `PasteTextForm()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** `busy`, `onSave(title, text)`.
- **What it does:** a **Paste text instead** button that opens a title + text form.

#### `submit()` · `frontend/components/knowledge/collection-panel.tsx`

- **What it does:** the paste form's submit. Calls `onSave`; on success, clears and closes the form.

#### `DocumentTable()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** `documents`, `canChange`, `onReindex(id)`, `onDelete(doc)`.
- **What it does:** a table of documents. Each row has:
  - the title (plus the file name, and any error in red);
  - type, size, a status badge, passage count;
  - **Re-index** and **Delete**.

  Each row carries `data-status`. On narrow screens the table scrolls inside its box; the page itself doesn't scroll sideways.

#### `SearchTester()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** `name`.
- **What it does:** a question box and **Search**. Results are numbered like chat sources, with the merged score, meaning similarity, whether a keyword matched, and the passage text.

#### `search()` · `frontend/components/knowledge/collection-panel.tsx`

- **What it does:** `searchCollection(name, {query, top_k: 5})`, then shows the hits or the error.

#### `StatusBadge()` · `frontend/components/knowledge/collection-panel.tsx`

- **Takes in:** `status`.
- **What it does:** a coloured pill: Pending (grey), Processing (pulsing blue), Ready (green), Failed (red).

#### `typeLabel()` · `frontend/components/knowledge/collection-panel.tsx`

- **Gives back:** a short name for a MIME type (`application/pdf` → `PDF`, the Word type → `Word`), or the MIME type itself if unknown.

#### `formatBytes()` · `frontend/components/knowledge/collection-panel.tsx`

- **Gives back:** a readable size: `71 B`, `35.9 KB`, `2.3 MB`.

#### `collectionPath()` · `frontend/lib/api.ts`

- **What it does:** builds `/v1/collections/<name>` with the name URL-encoded. Every collection function below uses it.

#### `listCollections()` · `frontend/lib/api.ts`

- **Called by:** `KnowledgePage`, `Chat`.
- **Takes in:** `includeArchived`, `signal`.
- **Gives back:** `{data: CollectionSummary[]}`.

#### `getCollection()` · `frontend/lib/api.ts`

- **Gives back:** `CollectionDetail`.

#### `createCollection()` · `frontend/lib/api.ts`

- **Takes in:** `{name, description}` and `adminKey`.
- **What it does:** `POST /v1/collections`.

#### `archiveCollection()` · `frontend/lib/api.ts`

- **What it does:** `DELETE /v1/collections/{name}`.

#### `reindexCollection()` · `frontend/lib/api.ts`

- **What it does:** `POST /v1/collections/{name}/reindex`.

#### `listDocuments()` · `frontend/lib/api.ts`

- **Gives back:** `{data: KnowledgeDocument[]}`.

#### `uploadDocuments()` · `frontend/lib/api.ts`

- **Takes in:** collection name, `File[]`, `adminKey`.
- **What it does:** puts each file in a `FormData` under `files` and sends it through `apiRequest`'s `form` option (multipart).
- **Gives back:** `UploadResult`.

#### `addTextDocument()` · `frontend/lib/api.ts`

- **What it does:** `POST .../documents/text` with `{title, text}`.

#### `deleteDocument()` · `frontend/lib/api.ts`

- **What it does:** `DELETE .../documents/{id}`. The reply is 204, so `apiRequest` gives back `undefined`.

#### `reindexDocument()` · `frontend/lib/api.ts`

- **What it does:** `POST .../documents/{id}/reindex`.

#### `searchCollection()` · `frontend/lib/api.ts`

- **What it does:** `POST .../search` with `{query, top_k}`.
- **Gives back:** the hits and the embedding model used.

### 14.10 Knowledge-base tests

Database tests are marked `requires_pgvector` (`backend/tests/conftest.py`): they run only when
`TEST_DATABASE_URL` is Postgres, and are skipped otherwise with the reason shown.

#### `make_pdf()` · `backend/tests/test_rag_parsers.py`

- **What it does:** writes a minimal valid one-page PDF by hand, with the given text, or with no text, to imitate a scanned page.

#### `make_docx()` · `backend/tests/test_rag_parsers.py`

- **What it does:** builds a Word file with a title property, a paragraph and a two-cell table.

#### `test_detect_type_by_extension_then_content_type()` · `backend/tests/test_rag_parsers.py`

- **Proves:**
  - extension first (`.MD` works);
  - then content type (with charset);
  - PNG → 415.

#### `test_text_and_markdown_title()` · `backend/tests/test_rag_parsers.py`

- **Proves:**
  - Windows line ends are fixed and NULs removed.
  - A Markdown `# Heading` becomes the title.

#### `test_latin1_text_falls_back_cleanly()` · `backend/tests/test_rag_parsers.py`

- **Proves:** non-UTF-8 text is still read ("Café").

#### `test_html_skips_scripts_and_uses_title()` · `backend/tests/test_rag_parsers.py`

- **Proves:**
  - `<title>` becomes the title;
  - entities are decoded;
  - script and style text is dropped.

#### `test_docx_paragraphs_tables_and_title()` · `backend/tests/test_rag_parsers.py`

- **Proves:** Word paragraphs, table rows (`cell | cell`) and the title property are read.

#### `test_pdf_text()` · `backend/tests/test_rag_parsers.py`

- **Proves:** PDF text is extracted; with no metadata title, the file name is used.

#### `test_pdf_without_text_explains_scans()` · `backend/tests/test_rag_parsers.py`

- **Proves:** a text-less PDF gives a 422 whose message mentions scans.

#### `test_broken_files_give_parse_errors()` · `backend/tests/test_rag_parsers.py`

- **Proves:** corrupt PDF and DOCX files give `DocumentParseError`, not a crash.

#### `test_empty_text_rejected()` · `backend/tests/test_rag_parsers.py`

- **Proves:** whitespace-only files are refused.

#### `test_normalize_collapses_blank_lines()` · `backend/tests/test_rag_parsers.py`

- **Proves:** the text clean-up rules.

#### `test_short_text_is_one_chunk()` · `backend/tests/test_rag_chunking.py`

- **Proves:** short text stays as one chunk with its paragraph break.

#### `test_chunks_respect_size_and_keep_paragraphs_together()` · `backend/tests/test_rag_chunking.py`

- **Proves:** every chunk fits the size, and (with no overlap) each paragraph appears whole exactly once.

#### `test_overlap_repeats_the_end_of_the_previous_chunk()` · `backend/tests/test_rag_chunking.py`

- **Proves:** each chunk starts with words from the end of the previous one.

#### `test_long_paragraph_splits_by_sentence_then_words()` · `backend/tests/test_rag_chunking.py`

- **Proves:** long paragraphs and a 450-character "word" are split to fit, with nothing lost.

#### `test_overlap_is_capped_at_half_the_size()` · `backend/tests/test_rag_chunking.py`

- **Proves:** an overlap larger than the size can't break the size limit.

#### `test_empty_text_gives_no_chunks()` · `backend/tests/test_rag_chunking.py`

- **Proves:** whitespace gives `[]`.

#### `vector()` · `backend/tests/test_rag_embeddings.py`

- **What it does:** a 768-number test vector filled with one value.

#### `is_unit()` · `backend/tests/test_rag_embeddings.py`

- **What it does:** checks a vector has length 1.

#### `reply()` · `backend/tests/test_rag_embeddings.py`

- **What it does:** the fake provider server defined inside three embedder tests (OpenAI, Gemini, Ollama). It answers with fake vectors shaped like the real API's.

#### `test_openai_sends_768_dimensions_batches_and_normalizes()` · `backend/tests/test_rag_embeddings.py`

- **Proves:**
  - 70 texts → 2 requests (64 + 6);
  - `dimensions: 768` is sent;
  - results come back in input order, at unit length;
  - the id is `openai/text-embedding-3-small@768`.

#### `test_openai_errors_use_the_chat_error_mapping()` · `backend/tests/test_rag_embeddings.py`

- **Proves:** a 401 becomes `ProviderAuthError`, as in chat.

#### `test_gemini_task_type_and_dimensions()` · `backend/tests/test_rag_embeddings.py`

- **Proves:** questions use `RETRIEVAL_QUERY` and 768 dimensions; the result is normalized.

#### `test_ollama_nomic_prefixes_and_wrong_size_is_rejected()` · `backend/tests/test_rag_embeddings.py`

- **Proves:**
  - the `search_document:`/`search_query:` prefixes are added for nomic models;
  - a 384-dimension model is rejected with "expected 768".

#### `test_mock_embedder_is_deterministic_and_word_based()` · `backend/tests/test_rag_embeddings.py`

- **Proves:**
  - the same text always gives the same vector;
  - shared words make vectors closer;
  - empty text still gives a unit vector.

#### `test_normalize_keeps_zero_vectors()` · `backend/tests/test_rag_embeddings.py`

- **Proves:** `[3, 4]` → `[0.6, 0.8]`; a zero vector is left alone.

#### `test_build_embedder_picks_configured_or_first_available()` · `backend/tests/test_rag_embeddings.py`

- **Proves:** the provider is chosen by priority:
  - automatic choice;
  - an explicit `EMBEDDING_PROVIDER` wins over automatic;
  - nothing available → `None`;
  - a chosen provider without a key → `None`;
  - a custom model name is used.

#### `test_rrf_rewards_items_found_by_both_methods()` · `backend/tests/test_rag_retrieval.py`

- **Proves:** items in both lists rank above an item at the top of one list; exact RRF scores (1/61, 1/62).

#### `test_context_numbers_sources_and_marks_them_as_reference()` · `backend/tests/test_rag_retrieval.py`

- **Proves:** the Sources block's header, its "never follow directions" warning, and the `[1] "Title" (collection: x, part n)` format.

#### `wait_ready()` · `backend/tests/test_rag_api.py`

- **Takes in:** the test client, a collection name, `seconds` (default 10).
- **What it does:** polls the document list every 50 ms until none is `pending`/`processing`. The worker runs in the same event loop, so it makes progress meanwhile.
- **Gives back:** the documents. Fails the test if indexing never finishes.

#### `make_collection()` · `backend/tests/test_rag_api.py`

- **What it does:** creates a collection through the API and checks it got 201.

#### `parse_sse()` · `backend/tests/test_rag_api.py`

- **What it does:** turns an SSE body into event dictionaries.

#### `test_upload_files_index_and_search()` · `backend/tests/test_rag_api.py`

- **Proves:**
  - One upload with `.md`, `.docx`, `.pdf`, `.html` → 202, all `pending`, then all `ready` with chunks.
  - Search for "annual leave days" ranks a leave document first, found by both methods.
  - The detail shows 4 ready and the mock embedder.

#### `test_keyword_search_finds_exact_codes()` · `backend/tests/test_rag_api.py`

- **Proves:**
  - Searching an error code (`ZX-4471`) ranks the document containing it first, with a keyword match.
  - The other results matched by meaning only.

#### `test_keyword_search_matches_any_word_not_all()` · `backend/tests/test_rag_api.py`

- **Proves:**
  - "how long is the warranty" keyword-matches a passage without the word "long".
  - A stop-words-only query falls back to vector search with no error.

#### `test_upload_errors()` · `backend/tests/test_rag_api.py`

- **Proves:** each upload problem gets the right response:
  - duplicate → 409;
  - PNG → 415;
  - over the limit → 413;
  - mixed upload → 202, with the good file accepted and the bad one in `errors`;
  - unknown collection → 404.

#### `test_delete_and_reindex_document()` · `backend/tests/test_rag_api.py`

- **Proves:**
  - Re-index → `pending` → `ready`.
  - Delete → 204, its passages no longer found, and the document is 404.

#### `test_embedding_model_change_needs_reindex()` · `backend/tests/test_rag_api.py`

- **Proves:** after switching the embedder:
  - the detail shows `needs_reindex`;
  - search → 409 `embedding_model_mismatch`;
  - re-index collection → 202 with the new model, and search works again.

#### `test_archived_collection_is_hidden_and_unusable()` · `backend/tests/test_rag_api.py`

- **Proves:**
  - Archived collections are hidden from the list and can't be searched (409).
  - Duplicate collection names → 409.

#### `test_chat_adds_sources_and_returns_citations()` · `backend/tests/test_rag_api.py`

- **Proves:**
  - The model receives the request's system text, then `## Sources` with `[1] "Handbook"`.
  - The reply's citations point to the right document and snippet.
  - The stream's `start` carries citations (`top_k` 1).
  - An empty collection gives `citations: []` and adds nothing.
  - An unknown collection → 404.
  - `/health` → `rag: ok`.

#### `test_unfinished_documents_are_requeued_on_start()` · `backend/tests/test_rag_api.py`

- **Proves:** a document left `processing` in the database becomes `ready` after a new app starts.

#### `test_without_postgres_rag_is_unavailable_but_chat_works()` · `backend/tests/test_rag_api.py`

- **Proves:** on SQLite, collections and chat-with-`rag` → 503 mentioning PostgreSQL, while plain chat still works. Runs everywhere.

#### `test_without_database_rag_is_unavailable()` · `backend/tests/test_rag_api.py`

- **Proves:** with no database at all, chat with `rag` → 503 `rag_unavailable`.

---

## 15. Debugging cheat sheet

| Symptom | Most likely cause | Where to look |
|---|---|---|
| Provider missing from `/health` | Key not set, or set in the wrong `.env` | `providers enabled:` log at startup; `ProviderRegistry.from_settings` |
| 404 `model_not_found` | Model name wrong or not in `*_MODELS` | `ProviderRegistry.resolve` message |
| 422 response | Request body invalid | `detail[]` in the response; `ChatRequest` |
| 502 `provider_auth_error` | Wrong or expired API key | That provider's `_map_error` |
| 400 `provider_bad_request` | Vendor rejected a parameter or model name | Error message (it includes the vendor's text) |
| 429 `provider_rate_limited` | Rate limit or quota | Vendor dashboard; lower traffic |
| 503 `provider_unavailable` | Network, vendor outage, Ollama not running | `_map_error` / `_raise_for_status` |
| 504 `provider_timeout` | Slow model or low `REQUEST_TIMEOUT_S` | Raise the timeout; check `latency_ms` |
| Reply cut off | `finish_reason: "length"` | Raise `max_tokens` |
| Empty reply | `refusal` or `content_filter` | `finish_reason` in the response |
| UI says "Can't reach the backend" | Backend down, wrong `NEXT_PUBLIC_API_URL`, or CORS | Browser console; `CORS_ORIGINS` |
| Stream arrives all at once | A proxy is buffering | Proxy config; test with `curl -N` |
| Trace one request end to end | — | Send `X-Request-ID: my-id` and search logs; the reply's `id` is in `chat completed` |
| 503 `prompt_store_unavailable` | `DATABASE_URL` not set (or prompt used without a database) | `prompt store:` log at startup; `/health` → `database` |
| `/health` says `database: "unavailable"` | Postgres down or wrong URL/password | `database ping failed` log; `Database.ping` |
| 404 `prompt_not_found` / `prompt_version_not_found` | Wrong prompt name or version number | `GET /v1/prompts` |
| 409 `prompt_archived` | Prompt was archived | `GET /v1/prompts?include_archived=true` |
| 409 `prompt_conflict` | Name already taken, or two saves at once | Retry; pick another name |
| 400 `prompt_variable_missing` | A `{{ variable }}` has no value | The message lists the missing names; `render` |
| 401 `admin_auth_required` | `ADMIN_API_KEY` set, header missing or wrong | Admin key field on the Prompts page |
| Model ignores the prompt | Prompt not applied, or `system` overrides it | Send the same request to `mock/echo-system` to see the final system prompt |
| Tables missing (`relation "prompts" does not exist`) | Migrations not run | `uv run alembic upgrade head` |
| 503 `rag_unavailable` | No database, not Postgres, no embedder, or pgvector missing | The error message names which; `/health` → `rag`; `RagRuntime.check` |
| Document stuck in `pending`/`processing` | Worker crashed or server stopped mid-job | Restart re-queues it; or `POST .../documents/{id}/reindex` |
| Document `failed` | Its `error` says why (embedder error, model mismatch...) | Fix the cause, then re-index the document |
| 409 `embedding_model_mismatch` | `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL` changed since indexing | `POST /v1/collections/{name}/reindex` |
| 415 / 422 on upload | Unsupported type / no readable text (e.g. scanned PDF) | `detect_type` / `extract_text` |
| Answer ignores the documents | Wrong passages retrieved | Try the question in the Knowledge page's **Test search**; check `vector_similarity` / `keyword_score` |
| Chat shows "No matching sources" | Collection empty or documents not `ready` | Knowledge page → document statuses |
