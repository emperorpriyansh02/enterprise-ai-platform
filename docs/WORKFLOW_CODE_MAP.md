# Workflow → Code Map

This file answers one question: **"When X happens, which functions run, and in what order?"**

Each section is one workflow. Steps are numbered in the order they run. Indented steps happen
*inside* the step above them. Every function in the project appears here at least once.

For a plain-language explanation of any function (what it takes in, what it gives back, how it
fails, how to debug it), look it up by name in [FUNCTION_GUIDE.md](FUNCTION_GUIDE.md). Both
files use the same section numbers.

> Paths are relative to the repo root. `backend/app/...` is Python (FastAPI),
> `frontend/...` is TypeScript (Next.js).

---

## Contents

1. [App startup](#1-app-startup)
2. [Every HTTP request](#2-every-http-request)
3. [Health check — `GET /health`](#3-health-check--get-health)
4. [List models — `GET /v1/models`](#4-list-models--get-v1models)
5. [Chat, one reply — `POST /v1/chat`](#5-chat-one-reply--post-v1chat)
6. [Chat, streaming — `POST /v1/chat/stream`](#6-chat-streaming--post-v1chatstream)
7. [Provider adapters](#7-provider-adapters)
8. [Errors](#8-errors)
9. [Logging](#9-logging)
10. [Shutdown](#10-shutdown)
11. [Frontend](#11-frontend)
12. [Tests](#12-tests)
13. [Prompts (Layer 2)](#13-prompts-layer-2)

---

## The big picture

```
Browser ──► Next.js chat UI ──► FastAPI route ──► ChatService ──► ProviderRegistry.resolve()
                                                       │
                                                       ├──► PromptService (managed prompt from Postgres, Layer 2)
                                                       ▼
                                          provider adapter (Anthropic / OpenAI / Gemini / Ollama / Mock)
                                                       │
                                                       ▼
                                          vendor SDK or HTTP call ──► the LLM
```

The reply travels back the same way: the adapter turns the vendor's answer into our own shape,
`ChatService` adds an id and timing, and the route sends it to the browser.

---

## 1. App startup

**What happens:** you run `uv run uvicorn app.main:app`. Python imports `backend/app/main.py`,
which builds the app. When the server starts, it creates one client per enabled LLM provider.

1. `create_app()` · `backend/app/main.py` — runs once, at import time (`app = create_app()`).
   1. `get_settings()` · `backend/app/core/config.py` — reads environment variables and `backend/.env` into a `Settings` object (cached, so it's read only once).
      1. `Settings._split_csv()` · `backend/app/core/config.py` — turns comma lists like `gpt-5,gpt-5-mini` into Python lists.
      2. `Settings._empty_to_none()` · `backend/app/core/config.py` — turns blank values like `OPENAI_BASE_URL=` into "not set".
   2. `configure_logging()` · `backend/app/core/logging.py` — switches all logs to one-JSON-object-per-line.
   3. Builds the FastAPI object, adds CORS and the `request_context` middleware (see section 2).
   4. `register_error_handlers()` · `backend/app/core/errors.py` — tells FastAPI how to turn our errors into JSON responses.
   5. Adds the routes: `/health` and `/v1/...` (from `backend/app/api/v1/routes.py`).
2. The server starts → FastAPI runs `lifespan()` · `backend/app/main.py` (defined inside `create_app`).
   1. `ProviderRegistry.from_settings()` · `backend/app/llm/registry.py` — for each provider that has a key (or URL), creates its adapter:
      1. `AnthropicProvider.__init__()` · `backend/app/llm/providers/anthropic_provider.py` — if `ANTHROPIC_API_KEY` is set.
      2. `OpenAIProvider.__init__()` · `backend/app/llm/providers/openai_provider.py` — if `OPENAI_API_KEY` is set.
      3. `GeminiProvider.__init__()` · `backend/app/llm/providers/gemini_provider.py` — if `GEMINI_API_KEY` is set.
      4. `OllamaProvider.__init__()` · `backend/app/llm/providers/ollama_provider.py` — if `OLLAMA_BASE_URL` is set.
      5. `MockProvider.__init__()` · `backend/app/llm/providers/mock_provider.py` — if `ENABLE_MOCK_PROVIDER=true`.
      6. Each of those first calls `LLMProvider.__init__()` · `backend/app/llm/base.py` — stores the provider's allowed model list.
   2. `ProviderRegistry.__init__()` · `backend/app/llm/registry.py` — stores the adapters by name, plus the default model.
   3. Saves the registry on `app.state.registry`, where every request can find it.
   4. Logs `providers enabled: ...` — the first line to check if a provider seems missing.
   5. If `DATABASE_URL` is set: `Database.__init__()` · `backend/app/db/session.py` — creates the connection pool (no connection is opened yet). Saved on `app.state.database` (or `None`).
   6. Logs `prompt store: enabled` or `prompt store: disabled (no DATABASE_URL)`.
3. The app now waits for requests.

---

## 2. Every HTTP request

**What happens:** every request (any URL) passes through the same wrappers before and after its route.

1. `request_context()` · `backend/app/main.py` — the outermost wrapper (defined inside `create_app`).
   1. Takes the `X-Request-ID` header, or makes a new random id.
   2. Starts a timer.
   3. Passes the request on → CORS check (browser origin allowed?) → the matching route function.
      - If the route raises one of our errors, `_handle_app_error()` · `backend/app/core/errors.py` turns it into a JSON error response (section 8).
   4. Adds `X-Request-ID` to the response headers.
   5. Logs one `request` line: method, path, status, latency.
2. The response goes back to the caller.

---

## 3. Health check — `GET /health`

**What happens:** a quick "is the server up, and which providers are on?" check.

1. `request_context()` · `backend/app/main.py` (section 2).
2. `get_registry()` · `backend/app/api/deps.py` — FastAPI calls it to fetch the registry from `app.state`.
3. `health()` · `backend/app/api/v1/routes.py` — the route.
   1. `ProviderRegistry.provider_names()` · `backend/app/llm/registry.py` — the list of enabled provider names.
   2. `Database.ping()` · `backend/app/db/session.py` — runs `SELECT 1` if a database is configured.
4. Returns `{"status": "ok", "providers": [...], "database": "ok" | "unavailable" | "disabled"}`.

---

## 4. List models — `GET /v1/models`

**What happens:** the frontend asks which models it may offer in the dropdown.

1. `get_registry()` · `backend/app/api/deps.py` — fetch the registry.
2. `list_models()` · `backend/app/api/v1/routes.py` — the route.
   1. `ProviderRegistry.default_model()` · `backend/app/llm/registry.py` — the configured default, or the first available model if the default isn't enabled.
      1. `ProviderRegistry.catalog()` · `backend/app/llm/registry.py` — builds the full list (`"provider/model"` ids).
   2. `ProviderRegistry.catalog()` · `backend/app/llm/registry.py` — again, for the response body.
3. Returns a `ModelList`: `{"default": "...", "data": [{id, provider, model}, ...]}`.

---

## 5. Chat, one reply — `POST /v1/chat`

**What happens:** the caller sends messages and waits for the whole answer in one JSON response.

1. `request_context()` · `backend/app/main.py` (section 2).
2. FastAPI checks the JSON body against `ChatRequest` (`backend/app/schemas/chat.py`).
   1. `ChatRequest._first_message_from_user()` · `backend/app/schemas/chat.py` — rejects a conversation that doesn't start with a user message.
   2. Any rule broken → 422 response; nothing below runs.
3. `get_chat_service()` · `backend/app/api/deps.py` — builds a `ChatService`. FastAPI first resolves what it needs:
   1. `get_db_session()` · `backend/app/api/deps.py` — opens one database session for this request (or gives `None` without a database).
   2. `get_prompt_service()` · `backend/app/api/deps.py` — wraps that session in a `PromptService` (or `None`), using `get_settings_from_app()` · `backend/app/api/deps.py` for `DEFAULT_PROMPT_NAME`.
   3. `get_registry()` · `backend/app/api/deps.py`
   4. `ChatService.__init__()` · `backend/app/services/chat_service.py` — keeps the registry and the prompt service.
4. `chat()` · `backend/app/api/v1/routes.py` — the route; hands off to the service.
5. `ChatService.complete()` · `backend/app/services/chat_service.py` — the main work:
   1. `ProviderRegistry.resolve()` · `backend/app/llm/registry.py` — `"anthropic/claude-opus-5-5"` → (Anthropic adapter, `"claude-opus-5-5"`). Unknown or not-allowed model → 404.
      - No model in the request → uses `ProviderRegistry.default_model()` · `backend/app/llm/registry.py`.
   2. `ChatService._build_system()` · `backend/app/services/chat_service.py` — works out the final system prompt: the managed prompt (if the request names one, or a default applies), then the request's own `system` text. Details in section 13.3.
   3. `_new_id()` · `backend/app/services/chat_service.py` — makes the reply id `chat_<random>`.
   4. Starts a timer.
   5. `_to_llm_request()` · `backend/app/services/chat_service.py` — converts the public `ChatRequest` into the internal `LLMRequest` (model name without the `provider/` prefix, final system prompt).
   6. `provider.complete(...)` — the chosen adapter's `complete` (section 7), e.g. `AnthropicProvider.complete()` · `backend/app/llm/providers/anthropic_provider.py`. It calls the vendor and returns an `LLMResult`.
   7. `_ms_since()` · `backend/app/services/chat_service.py` — how long the call took.
   8. Logs `chat completed` with provider, model, prompt (`_prompt_label()` · `backend/app/services/chat_service.py` → `support-agent@v3`), tokens, latency, finish reason.
   9. Builds the `ChatResponse`, including `prompt: {name, version}` when a managed prompt was used.
6. FastAPI turns the `ChatResponse` into JSON and sends it.

If anything in step 5 raises an error, go to section 8.

---

## 6. Chat, streaming — `POST /v1/chat/stream`

**What happens:** same as section 5, but the answer arrives piece by piece as server-sent events
(`start` → many `delta` → `done`, or `error`).

1. `request_context()` · `backend/app/main.py`, body validation with `ChatRequest._first_message_from_user()` · `backend/app/schemas/chat.py`, and `get_chat_service()` · `backend/app/api/deps.py` — same as section 5, steps 1–3.
2. `chat_stream()` · `backend/app/api/v1/routes.py` — the route.
   1. Calls `ChatService.stream()` · `backend/app/services/chat_service.py`, which gives back an event generator (nothing runs yet).
   2. **Pulls the first event right away.** That makes the generator run until its first `yield`:
      1. `ProviderRegistry.resolve()` · `backend/app/llm/registry.py` — pick the adapter (404 if unknown).
      2. `ChatService._build_system()` · `backend/app/services/chat_service.py` — final system prompt (section 13.3). An unknown prompt or missing variable fails *here*, as a normal HTTP error.
      3. `_new_id()` · `backend/app/services/chat_service.py` and start a timer.
      4. `_to_llm_request()` · `backend/app/services/chat_service.py` — convert the request.
      5. `provider.stream(...)` — the adapter's `stream` (section 7), e.g. `OpenAIProvider.stream()` · `backend/app/llm/providers/openai_provider.py`.
      6. Waits for the adapter's **first chunk**. This is when the vendor is actually contacted, so a bad key or rate limit fails *here* and becomes a normal HTTP error (section 8).
      7. Yields the `start` event (with `prompt` if a managed prompt was used).
   3. Wraps the rest in an `EventSourceResponse` (server-sent events, with a keep-alive ping every 15 s).
3. `_sse()` · `backend/app/api/v1/routes.py` — turns each event into an SSE `data:` line, while `ChatService.stream()` · `backend/app/services/chat_service.py` keeps running:
   1. For every text chunk → yields a `delta` event.
   2. When the adapter's final chunk arrives (`done=True`) → `_ms_since()` · `backend/app/services/chat_service.py`, logs `chat stream completed`, yields the `done` event, stops.
   3. If the adapter fails mid-stream → logs `chat stream failed`, yields an `error` event, stops. (The HTTP status is already 200 at this point, so the error travels inside the stream.)

---

## 7. Provider adapters

Every adapter follows the same contract, defined in `backend/app/llm/base.py`:

- `LLMProvider.__init__()` · `backend/app/llm/base.py` — store the allowed model list.
- `LLMProvider.complete()` · `backend/app/llm/base.py` — one full reply.
- `LLMProvider.stream()` · `backend/app/llm/base.py` — text pieces, then one final chunk.
- `LLMProvider.aclose()` · `backend/app/llm/base.py` — close network clients at shutdown.

### 7.1 Anthropic Claude — `complete` and `stream`

1. `AnthropicProvider.complete()` · `backend/app/llm/providers/anthropic_provider.py`
   1. `AnthropicProvider._params()` · `backend/app/llm/providers/anthropic_provider.py` — builds the SDK arguments: messages, system, `max_tokens` (16,000), effort, refusal fallback; drops `temperature`.
   2. Calls the Anthropic SDK (`client.beta.messages.create`).
   3. Joins the text blocks, maps the stop reason, reads token usage → `LLMResult`.
   4. On an SDK error → `_map_error()` · `backend/app/llm/providers/anthropic_provider.py` → our error class (section 8).
2. `AnthropicProvider.stream()` · `backend/app/llm/providers/anthropic_provider.py`
   1. `AnthropicProvider._params()` · `backend/app/llm/providers/anthropic_provider.py` — same, with `max_tokens` 64,000.
   2. Opens the SDK stream; yields each text piece as a `StreamChunk`.
   3. Reads the final message → yields the final chunk (stop reason, usage, model).
   4. Errors → `_map_error()` · `backend/app/llm/providers/anthropic_provider.py`.
3. Shutdown: `AnthropicProvider.aclose()` · `backend/app/llm/providers/anthropic_provider.py`.

### 7.2 OpenAI — `complete` and `stream`

1. `OpenAIProvider.complete()` · `backend/app/llm/providers/openai_provider.py`
   1. `OpenAIProvider._params()` · `backend/app/llm/providers/openai_provider.py` — system prompt becomes the first message; `max_tokens` → `max_completion_tokens`; `temperature` only if set.
   2. Calls `client.chat.completions.create`.
   3. Takes the first choice; detects refusals; `_usage()` · `backend/app/llm/providers/openai_provider.py` reads token counts → `LLMResult`.
   4. Errors → `_map_error()` · `backend/app/llm/providers/openai_provider.py`.
2. `OpenAIProvider.stream()` · `backend/app/llm/providers/openai_provider.py`
   1. `OpenAIProvider._params()` · `backend/app/llm/providers/openai_provider.py` plus `stream=True` and "include usage".
   2. For each chunk: yield text pieces, remember finish reason; the last chunk carries usage → `_usage()` · `backend/app/llm/providers/openai_provider.py`.
   3. Yields the final chunk. Errors → `_map_error()` · `backend/app/llm/providers/openai_provider.py`.
3. Shutdown: `OpenAIProvider.aclose()` · `backend/app/llm/providers/openai_provider.py`.

### 7.3 Google Gemini — `complete` and `stream`

1. `GeminiProvider.complete()` · `backend/app/llm/providers/gemini_provider.py`
   1. `GeminiProvider._params()` · `backend/app/llm/providers/gemini_provider.py` — role `assistant` → `model`; system prompt → `system_instruction`; `max_tokens` → `max_output_tokens`.
   2. Calls `client.aio.models.generate_content`.
   3. `_text()` · `backend/app/llm/providers/gemini_provider.py` → reply text; `_finish_reason()` · `backend/app/llm/providers/gemini_provider.py` → why it stopped; `_usage()` · `backend/app/llm/providers/gemini_provider.py` → tokens → `LLMResult`.
   4. Errors → `_map_error()` · `backend/app/llm/providers/gemini_provider.py`.
2. `GeminiProvider.stream()` · `backend/app/llm/providers/gemini_provider.py`
   1. `GeminiProvider._params()` · `backend/app/llm/providers/gemini_provider.py`, then `generate_content_stream`.
   2. For each chunk: `_text()` · `backend/app/llm/providers/gemini_provider.py` → yield piece; `_finish_reason()` · `backend/app/llm/providers/gemini_provider.py` and `_usage()` · `backend/app/llm/providers/gemini_provider.py` → remember.
   3. Yields the final chunk. Errors → `_map_error()` · `backend/app/llm/providers/gemini_provider.py`.
3. Shutdown: `GeminiProvider.aclose()` · `backend/app/llm/providers/gemini_provider.py`.

### 7.4 Ollama (local models) — `complete` and `stream`

1. `OllamaProvider.complete()` · `backend/app/llm/providers/ollama_provider.py`
   1. `OllamaProvider._payload()` · `backend/app/llm/providers/ollama_provider.py` — JSON body for `POST /api/chat`; `max_tokens` → `options.num_predict`.
   2. Sends the HTTP request.
   3. `_raise_for_status()` · `backend/app/llm/providers/ollama_provider.py` — turns 4xx/5xx into our errors (404 → "pull the model first").
   4. `_usage()` · `backend/app/llm/providers/ollama_provider.py` reads token counts → `LLMResult`.
   5. Network errors → `_map_error()` · `backend/app/llm/providers/ollama_provider.py`.
2. `OllamaProvider.stream()` · `backend/app/llm/providers/ollama_provider.py`
   1. `OllamaProvider._payload()` · `backend/app/llm/providers/ollama_provider.py` with `stream: true`.
   2. Opens a streaming request; on 4xx/5xx → `_raise_for_status()` · `backend/app/llm/providers/ollama_provider.py`.
   3. Reads one JSON object per line: yields text pieces; on the `done` line → `_usage()` · `backend/app/llm/providers/ollama_provider.py` → final chunk.
   4. Network errors → `_map_error()` · `backend/app/llm/providers/ollama_provider.py`.
3. Shutdown: `OllamaProvider.aclose()` · `backend/app/llm/providers/ollama_provider.py`.

### 7.5 Mock (echo) — `complete` and `stream`

No network. Used by tests and keyless demos.

Two models: `mock/echo` and `mock/echo-system`.

1. `MockProvider.complete()` · `backend/app/llm/providers/mock_provider.py`
   1. `MockProvider._reply()` · `backend/app/llm/providers/mock_provider.py` — `echo`: `"Echo: <last user message>"`; `echo-system`: the system prompt it received (to check what prompt reached the model).
   2. `MockProvider._usage()` · `backend/app/llm/providers/mock_provider.py` — fake token counts (word counts).
2. `MockProvider.stream()` · `backend/app/llm/providers/mock_provider.py`
   1. `MockProvider._reply()` · `backend/app/llm/providers/mock_provider.py`, split into words.
   2. Yields one word at a time (with a small delay), then the final chunk with `MockProvider._usage()` · `backend/app/llm/providers/mock_provider.py`.
3. Shutdown: uses the base `LLMProvider.aclose()` · `backend/app/llm/base.py` (nothing to close).

---

## 8. Errors

**What happens:** something fails. Every failure ends as the same JSON shape:
`{"error": {"code": "...", "message": "...", "provider": "..."}}`.

1. A vendor SDK or HTTP call raises its own exception inside an adapter.
2. The adapter's `_map_error` turns it into one of our classes:
   - `_map_error()` · `backend/app/llm/providers/anthropic_provider.py`
   - `_map_error()` · `backend/app/llm/providers/openai_provider.py`
   - `_map_error()` · `backend/app/llm/providers/gemini_provider.py`
   - `_map_error()` · `backend/app/llm/providers/ollama_provider.py` (plus `_raise_for_status()` · `backend/app/llm/providers/ollama_provider.py` for HTTP status codes)
3. Our error object is built by `AppError.__init__()` · `backend/app/core/errors.py` (all error classes share it).
4. Then one of two things:
   - **Before any reply was sent** (normal requests, and streams before the first chunk): FastAPI calls `_handle_app_error()` · `backend/app/core/errors.py` (registered by `register_error_handlers()` · `backend/app/core/errors.py`), which uses `AppError.to_dict()` · `backend/app/core/errors.py` and returns the HTTP status of that class (400/404/429/502/503/504).
   - **In the middle of a stream:** `ChatService.stream()` · `backend/app/services/chat_service.py` catches it, calls `AppError.to_dict()` · `backend/app/core/errors.py`, and sends an `error` event.
5. Request validation problems (bad JSON, wrong field types, first message not from user) never reach our code: FastAPI returns 422 with a `detail` list.

| Error class | HTTP status | `code` |
|---|---|---|
| `ModelNotFoundError` | 404 | `model_not_found` |
| `ProviderBadRequestError` | 400 | `provider_bad_request` |
| `ProviderRateLimitError` | 429 | `provider_rate_limited` |
| `ProviderAuthError` | 502 | `provider_auth_error` |
| `ProviderError` | 502 | `provider_error` |
| `ProviderUnavailableError` | 503 | `provider_unavailable` |
| `ProviderTimeoutError` | 504 | `provider_timeout` |
| `PromptNotFoundError` | 404 | `prompt_not_found` |
| `PromptVersionNotFoundError` | 404 | `prompt_version_not_found` |
| `PromptConflictError` | 409 | `prompt_conflict` |
| `PromptArchivedError` | 409 | `prompt_archived` |
| `PromptVariableError` | 400 | `prompt_variable_missing` |
| `PromptStoreUnavailableError` | 503 | `prompt_store_unavailable` |
| `AdminAuthError` | 401 | `admin_auth_required` |

---

## 9. Logging

1. At startup, `configure_logging()` · `backend/app/core/logging.py` installs the JSON formatter on the root logger.
2. Any `logger.info(...)` in the app → `JsonFormatter.format()` · `backend/app/core/logging.py` writes one JSON line to stdout, including extra fields such as `request_id`, `provider`, `model`, `latency_ms`, `input_tokens`.

Log lines you'll see per chat: `request` (from `request_context()` · `backend/app/main.py`) and
`chat completed` / `chat stream completed` / `chat stream failed` (from `ChatService.complete()` · `backend/app/services/chat_service.py`
and `ChatService.stream()` · `backend/app/services/chat_service.py`).

---

## 10. Shutdown

1. The server stops (Ctrl+C or container stop) → `lifespan()` · `backend/app/main.py` continues after its `yield`.
2. `ProviderRegistry.aclose()` · `backend/app/llm/registry.py` — closes each adapter, one by one; a failure in one is logged and doesn't stop the others:
   - `AnthropicProvider.aclose()` · `backend/app/llm/providers/anthropic_provider.py`
   - `OpenAIProvider.aclose()` · `backend/app/llm/providers/openai_provider.py`
   - `GeminiProvider.aclose()` · `backend/app/llm/providers/gemini_provider.py`
   - `OllamaProvider.aclose()` · `backend/app/llm/providers/ollama_provider.py`
   - Mock uses `LLMProvider.aclose()` · `backend/app/llm/base.py` (does nothing).
3. `Database.aclose()` · `backend/app/db/session.py` — closes the database connection pool (only if the app opened it).

---

## 11. Frontend

### 11.1 Page load

1. Next.js renders `RootLayout()` · `frontend/app/layout.tsx` — the `<html>`/`<body>` shell, fonts, and the top bar `NavBar()` · `frontend/components/nav.tsx` (Chat | Prompts links).
2. Inside it, `Home()` · `frontend/app/page.tsx` — the `/` page, which renders the chat.
3. `Chat()` · `frontend/components/chat.tsx` — the chat component; sets up its state (models, turns, input...).
4. Right after the first render, the **load-models effect** inside `Chat()` · `frontend/components/chat.tsx` runs:
   1. `getModels()` · `frontend/lib/api.ts` — `GET /v1/models`.
      - Not OK → `toApiError()` · `frontend/lib/api.ts` → throws an `ApiError` · `frontend/lib/api.ts`.
      - It goes through `apiRequest()` · `frontend/lib/api.ts`, the shared JSON request helper.
   2. Success → fills the model dropdown and selects the default model.
   3. Failure → shows the red "Can't reach the backend" (or the server's message) banner.
5. The **load-prompts effect** inside `Chat()` · `frontend/components/chat.tsx` runs too:
   1. `listPrompts()` · `frontend/lib/api.ts` → `apiRequest()` · `frontend/lib/api.ts` — `GET /v1/prompts`.
   2. Success → fills the Prompt dropdown. Failure (no database) → the dropdown shows "Unavailable"; chat still works.

### 11.2 Sending a message

1. The user presses Enter (or clicks Send) → `send()` · `frontend/components/chat.tsx`.
   1. Stops early if a prompt is selected and any of its variables is empty (the hint under the box names them). Otherwise builds the history from earlier turns (skipping failed ones) and adds the new user message.
   2. `nextKey()` · `frontend/components/chat.tsx` (twice) — unique keys for the new user turn and the empty assistant turn.
   3. Adds both turns to the screen; clears the input; marks the chat busy.
   4. `streamChat()` · `frontend/lib/api.ts` — `POST /v1/chat/stream`, with `prompt: {name, variables}` when a prompt is selected.
      1. Not OK → `toApiError()` · `frontend/lib/api.ts` → throws `ApiError` · `frontend/lib/api.ts`.
      2. Reads the response body piece by piece; splits it at blank lines.
      3. `parseEvent()` · `frontend/lib/api.ts` — one SSE block → one event object.
      4. Calls the event handler inside `send()` · `frontend/components/chat.tsx` for each event:
         - `start` → `updateTurn()` · `frontend/components/chat.tsx` saves provider/model/prompt.
         - `delta` → `updateTurn()` · `frontend/components/chat.tsx` appends the text.
         - `done` → `updateTurn()` · `frontend/components/chat.tsx` saves usage, latency, finish reason.
         - `error` → `updateTurn()` · `frontend/components/chat.tsx` shows the error under the reply.
   5. When the stream ends (or fails) → `updateTurn()` · `frontend/components/chat.tsx` marks the turn finished; busy is cleared.
2. The **auto-scroll effect** inside `Chat()` · `frontend/components/chat.tsx` scrolls to the newest message every time the turns change.

### 11.3 Stop and New chat

1. Stop button → `stop()` · `frontend/components/chat.tsx` — aborts the running request; `send()` · `frontend/components/chat.tsx` sees the abort and marks the reply "Stopped."
2. New chat button → `newChat()` · `frontend/components/chat.tsx` — calls `stop()` · `frontend/components/chat.tsx`, then clears all turns.

---

## 12. Tests

Run with `cd backend && uv run pytest`. No API keys or network needed.

### 12.1 Shared helpers (run before/inside tests)

1. `make_settings()` · `backend/tests/conftest.py` — settings with all real keys switched off and the mock model as default.
2. `client()` · `backend/tests/conftest.py` — a pytest fixture: a running test app (startup + shutdown included).
3. `llm_request()` · `backend/tests/conftest.py` — a pytest fixture: a ready-made `LLMRequest`.
4. `sse()` · `backend/tests/conftest.py` — builds a fake server-sent-events body.
5. `Recorder.__init__()` · `backend/tests/conftest.py` → `Recorder.client()` · `backend/tests/conftest.py` — a fake HTTP client that never touches the network; every request goes to `Recorder.__call__()` · `backend/tests/conftest.py`, which saves it and returns a canned response. `Recorder.last_json()` · `backend/tests/conftest.py` reads back the last request body.
6. Database helpers (Layer 2), see section 13.6: `database()` · `backend/tests/conftest.py`, `prompt_service()` · `backend/tests/conftest.py`, `api_factory()` · `backend/tests/conftest.py`.

### 12.2 Provider adapter tests (adapter → fake HTTP → our result)

Each file first builds an adapter wired to a fake HTTP client, then checks the request we sent
and the result we produced.

- Anthropic:
  `make_provider()` · `backend/tests/test_anthropic_provider.py` →
  `test_complete_maps_request_and_response()` · `backend/tests/test_anthropic_provider.py`,
  `test_fallback_only_for_supported_models()` · `backend/tests/test_anthropic_provider.py`,
  `test_refusal_is_reported()` · `backend/tests/test_anthropic_provider.py`,
  `test_stream_yields_deltas_then_final()` · `backend/tests/test_anthropic_provider.py`,
  `test_errors_are_mapped()` · `backend/tests/test_anthropic_provider.py`
- OpenAI:
  `chunk()` · `backend/tests/test_openai_provider.py` (builds a fake stream chunk),
  `make_provider()` · `backend/tests/test_openai_provider.py` →
  `test_complete_maps_request_and_response()` · `backend/tests/test_openai_provider.py`,
  `test_refusal()` · `backend/tests/test_openai_provider.py`,
  `test_stream()` · `backend/tests/test_openai_provider.py`,
  `test_errors_are_mapped()` · `backend/tests/test_openai_provider.py`
- Gemini:
  `response()` · `backend/tests/test_gemini_provider.py` (builds a fake Gemini reply),
  `make_provider()` · `backend/tests/test_gemini_provider.py` →
  `test_complete_maps_roles_and_system()` · `backend/tests/test_gemini_provider.py`,
  `test_safety_block_maps_to_content_filter()` · `backend/tests/test_gemini_provider.py`,
  `test_stream()` · `backend/tests/test_gemini_provider.py`,
  `test_errors_are_mapped()` · `backend/tests/test_gemini_provider.py`
- Ollama:
  `make_provider()` · `backend/tests/test_ollama_provider.py` (with inner `record()` · `backend/tests/test_ollama_provider.py`) →
  `test_complete()` · `backend/tests/test_ollama_provider.py`,
  `test_stream_reads_ndjson()` · `backend/tests/test_ollama_provider.py`,
  `test_missing_model_gives_helpful_error()` · `backend/tests/test_ollama_provider.py`,
  `test_unreachable_server()` · `backend/tests/test_ollama_provider.py` (with inner `refuse()` · `backend/tests/test_ollama_provider.py`)

### 12.3 Registry tests

`test_only_configured_providers_are_enabled()` · `backend/tests/test_registry.py`,
`test_csv_lists_and_blank_values_from_env()` · `backend/tests/test_registry.py`,
`test_resolve()` · `backend/tests/test_registry.py`,
`test_resolve_rejects_unknown_or_disallowed()` · `backend/tests/test_registry.py`,
`test_default_model_falls_back_to_first_available()` · `backend/tests/test_registry.py`

### 12.4 API tests (HTTP request → whole app → mock provider)

`test_health()` · `backend/tests/test_api.py`,
`test_models()` · `backend/tests/test_api.py`,
`test_chat()` · `backend/tests/test_api.py`,
`test_chat_uses_default_model_when_omitted()` · `backend/tests/test_api.py`,
`test_unknown_model_is_404()` · `backend/tests/test_api.py`,
`test_first_message_must_be_user()` · `backend/tests/test_api.py`,
`parse_sse()` · `backend/tests/test_api.py` (helper) →
`test_stream()` · `backend/tests/test_api.py`,
`test_stream_unknown_model_is_http_error()` · `backend/tests/test_api.py`,
`test_stream_mid_stream_failure_sends_error_event()` · `backend/tests/test_api.py` (uses a fake provider whose `FailingProvider.stream()` · `backend/tests/test_api.py` fails halfway)

### 12.5 Docs coverage test (keeps these two docs complete)

1. `_all_functions()` · `backend/tests/test_docs_coverage.py` — walks the code:
   1. `_python_functions()` · `backend/tests/test_docs_coverage.py` — reads each Python file's function names (with inner helper `visit()` · `backend/tests/test_docs_coverage.py`).
   2. Regex scan for TypeScript functions.
2. `test_every_function_is_documented()` · `backend/tests/test_docs_coverage.py` — for each doc, uses `_mentioned()` · `backend/tests/test_docs_coverage.py` to check every function appears next to its file.
3. `test_scanner_finds_functions()` · `backend/tests/test_docs_coverage.py` — sanity check that the scanner itself works.

If you add a function and forget to document it, this test fails and lists what's missing.

---

## 13. Prompts (Layer 2)

Managed, versioned system prompts stored in Postgres. Code lives in `backend/app/prompts/`
(rules and storage), `backend/app/db/` (database connection), `backend/app/api/v1/prompts.py`
(routes), and `frontend/components/prompts/` (the Prompts page).

```
Prompts page ──► /v1/prompts routes ──► PromptService (rules) ──► PromptRepository (SQL) ──► Postgres
Chat request ──► ChatService._build_system ──► PromptService.resolve_for_chat ──► template.render
```

### 13.1 Setting up the database (once, from the command line)

1. `uv run alembic upgrade head` (in `backend/`) runs `backend/migrations/env.py`:
   1. `run_migrations_online()` · `backend/migrations/env.py` → `run_async_migrations()` · `backend/migrations/env.py`.
      1. `database_url()` · `backend/migrations/env.py` — reads `DATABASE_URL` from settings (fails clearly if it's missing).
      2. Opens a connection and calls `do_run_migrations()` · `backend/migrations/env.py`, which runs every migration not yet applied:
         1. `upgrade()` · `backend/migrations/versions/0001_create_prompts.py` — creates the `prompts` and `prompt_versions` tables.
   2. With `--sql`, `run_migrations_offline()` · `backend/migrations/env.py` prints the SQL instead of running it (also uses `database_url()` · `backend/migrations/env.py`).
   3. `uv run alembic downgrade base` runs `downgrade()` · `backend/migrations/versions/0001_create_prompts.py`, which drops both tables.
2. Optional: `uv run python -m app.prompts.seed` →
   1. `main()` · `backend/app/prompts/seed.py` — reads settings, opens the database (`Database.__init__()` · `backend/app/db/session.py`, then `Database.session()` · `backend/app/db/session.py`).
   2. `seed()` · `backend/app/prompts/seed.py` — for each example prompt: `PromptService.get_prompt()` · `backend/app/prompts/service.py`; if it's missing, `PromptService.create_prompt()` · `backend/app/prompts/service.py`.
   3. Closes the database with `Database.aclose()` · `backend/app/db/session.py`.

### 13.2 Admin API requests (`/v1/prompts/...`)

Every prompts route first gets its dependencies from `backend/app/api/deps.py`:

1. `get_db_session()` · `backend/app/api/deps.py` — one session per request, via `Database.session()` · `backend/app/db/session.py`. `None` when no database is configured.
2. `get_prompt_service()` · `backend/app/api/deps.py` — builds `PromptService.__init__()` · `backend/app/prompts/service.py` (which builds `PromptRepository.__init__()` · `backend/app/prompts/repository.py`). Reads `DEFAULT_PROMPT_NAME` through `get_settings_from_app()` · `backend/app/api/deps.py`.
3. `require_prompt_service()` · `backend/app/api/deps.py` — no database → 503 `prompt_store_unavailable`, and the route never runs.
4. Write routes only: `require_admin()` · `backend/app/api/deps.py` — if `ADMIN_API_KEY` is set, the `X-Admin-Key` header must match, or 401 `admin_auth_required`.

Then, per route:

- **List** `GET /v1/prompts` → `list_prompts()` · `backend/app/api/v1/prompts.py` → `PromptService.list_prompts()` · `backend/app/prompts/service.py`
  1. `PromptRepository.list_prompts()` · `backend/app/prompts/repository.py` — the prompt rows (archived ones only if asked).
  2. `PromptRepository.latest_versions()` · `backend/app/prompts/repository.py` — highest version number per prompt (one query).
  3. `PromptRepository.active_versions()` · `backend/app/prompts/repository.py` — the active version row per prompt (one query).
  4. `_summary()` · `backend/app/prompts/service.py` — one `PromptSummary` per prompt.
- **Create** `POST /v1/prompts` → `create_prompt()` · `backend/app/api/v1/prompts.py` → `PromptService.create_prompt()` · `backend/app/prompts/service.py`
  1. `PromptRepository.get_prompt()` · `backend/app/prompts/repository.py` — name taken → 409.
  2. New `Prompt` row (active version 1) and its first version from `_new_version()` · `backend/app/prompts/service.py`, which calls `extract_variables()` · `backend/app/prompts/template.py`.
  3. `PromptRepository.add()` · `backend/app/prompts/repository.py` (twice), then `PromptService._commit()` · `backend/app/prompts/service.py` (a duplicate at the same instant → 409).
  4. `PromptService._detail()` · `backend/app/prompts/service.py` — reads back the versions (`PromptRepository.list_versions()` · `backend/app/prompts/repository.py`) and builds the reply with `_summary()` · `backend/app/prompts/service.py` and `_version_out()` · `backend/app/prompts/service.py`.
- **Get one** `GET /v1/prompts/{name}` → `get_prompt()` · `backend/app/api/v1/prompts.py` → `PromptService.get_prompt()` · `backend/app/prompts/service.py` → `PromptService._require_prompt()` · `backend/app/prompts/service.py` (404 if missing) → `PromptService._detail()` · `backend/app/prompts/service.py`.
- **Edit description** `PATCH /v1/prompts/{name}` → `update_prompt()` · `backend/app/api/v1/prompts.py` → `PromptService.update_prompt()` · `backend/app/prompts/service.py` → `PromptService._require_prompt()` · `backend/app/prompts/service.py` → `PromptService._commit()` · `backend/app/prompts/service.py` → `PromptService._detail()` · `backend/app/prompts/service.py`.
- **Archive** `DELETE /v1/prompts/{name}` → `archive_prompt()` · `backend/app/api/v1/prompts.py` → `PromptService.archive()` · `backend/app/prompts/service.py` — sets `archived_at`, commits, returns the detail.
- **Get a version** `GET /v1/prompts/{name}/versions/{n}` → `get_prompt_version()` · `backend/app/api/v1/prompts.py` → `PromptService.get_version()` · `backend/app/prompts/service.py` → `PromptService._require_version()` · `backend/app/prompts/service.py` (`PromptRepository.get_version()` · `backend/app/prompts/repository.py`; 404 if missing) → `_version_out()` · `backend/app/prompts/service.py`.
- **New version** `POST /v1/prompts/{name}/versions` → `create_prompt_version()` · `backend/app/api/v1/prompts.py` → `PromptService.add_version()` · `backend/app/prompts/service.py`
  1. `PromptService._require_prompt()` · `backend/app/prompts/service.py` with `writable=True` (archived → 409).
  2. Next number = `PromptRepository.latest_versions()` · `backend/app/prompts/repository.py` + 1.
  3. `_new_version()` · `backend/app/prompts/service.py` → `PromptRepository.add()` · `backend/app/prompts/repository.py`; if `activate`, it becomes active.
  4. `PromptService._commit()` · `backend/app/prompts/service.py` → `_version_out()` · `backend/app/prompts/service.py`.
- **Activate / roll back** `POST /v1/prompts/{name}/activate` → `activate_prompt_version()` · `backend/app/api/v1/prompts.py` → `PromptService.activate()` · `backend/app/prompts/service.py` → `PromptService._require_prompt()` · `backend/app/prompts/service.py` → `PromptService._require_version()` · `backend/app/prompts/service.py` → commit → `PromptService._detail()` · `backend/app/prompts/service.py`.
- **Preview** `POST /v1/prompts/{name}/render` → `render_prompt()` · `backend/app/api/v1/prompts.py` → `PromptService.render()` · `backend/app/prompts/service.py` → `PromptService._render()` · `backend/app/prompts/service.py` (same path as chat, 13.3 steps 3–6).

New rows get their timestamps from `utcnow()` · `backend/app/prompts/models.py`.

### 13.3 Chat with a managed prompt

Inside `ChatService.complete()` / `ChatService.stream()` (sections 5 and 6):

1. `ChatService._build_system()` · `backend/app/services/chat_service.py`
   - No database and the request names a prompt → 503 `prompt_store_unavailable`. No database and no prompt → keeps the request's `system` as is.
2. `PromptService.resolve_for_chat()` · `backend/app/prompts/service.py` — decides which prompt applies:
   - the request's `prompt` if given;
   - otherwise `DEFAULT_PROMPT_NAME`, but only if the request has no `system` of its own;
   - otherwise none (returns nothing; the request's `system` is used as is).
3. `PromptService._render()` · `backend/app/prompts/service.py`
   1. `PromptService._require_prompt()` · `backend/app/prompts/service.py` → `PromptRepository.get_prompt()` · `backend/app/prompts/repository.py` — 404 if unknown; archived → 409.
   2. Picks the pinned version, or the active one.
   3. `PromptService._require_version()` · `backend/app/prompts/service.py` → `PromptRepository.get_version()` · `backend/app/prompts/repository.py` — 404 if that version doesn't exist.
4. `render()` · `backend/app/prompts/template.py` — fills `{{ variables }}`; uses `extract_variables()` · `backend/app/prompts/template.py` to find any missing (→ 400 `prompt_variable_missing`).
5. Back in `resolve_for_chat`, the read transaction is ended, so no database connection is held while the model streams.
6. Back in `_build_system`: final system prompt = rendered prompt, then a blank line, then the request's `system` (if any). Also returns `{name, version}`, which goes on the reply and into the logs via `_prompt_label()` · `backend/app/services/chat_service.py`.

### 13.4 Frontend: the Prompts page (`/prompts`)

1. `Prompts()` · `frontend/app/prompts/page.tsx` — the route; renders `PromptsPage()` · `frontend/components/prompts/prompts-page.tsx`.
2. `PromptsPage()` · `frontend/components/prompts/prompts-page.tsx`
   1. `useAdminKey()` · `frontend/components/prompts/admin-key.tsx` — reads the saved admin key through `subscribe()` · `frontend/components/prompts/admin-key.tsx` and `readKey()` · `frontend/components/prompts/admin-key.tsx`.
   2. **Load-list effect** → `listPrompts()` · `frontend/lib/api.ts` → `apiRequest()` · `frontend/lib/api.ts`; errors pass through `errorMessage()` · `frontend/components/prompts/messages.ts`.
   3. Shows `AdminKeyInput()` · `frontend/components/prompts/admin-key.tsx`; typing calls `setKey()` · `frontend/components/prompts/admin-key.tsx`, which saves it for this browser tab.
   4. After any change, `reload()` · `frontend/components/prompts/prompts-page.tsx` re-runs the load-list effect.
3. **New prompt** button → `NewPromptForm()` · `frontend/components/prompts/prompts-page.tsx`
   1. While typing the template, `extractVariables()` · `frontend/lib/template.ts` feeds `VariableChips()` · `frontend/components/prompts/prompt-detail.tsx`.
   2. Create → `submit()` · `frontend/components/prompts/prompts-page.tsx` → `createPrompt()` · `frontend/lib/api.ts` → opens the new prompt.
4. Clicking a prompt → `PromptDetailPanel()` · `frontend/components/prompts/prompt-detail.tsx`
   1. **Load-detail effect** → `getPrompt()` · `frontend/lib/api.ts`.
   2. `VersionList()` · `frontend/components/prompts/prompt-detail.tsx` — "View" picks a version; "Make active" / "Roll back to vN" → `run()` · `frontend/components/prompts/prompt-detail.tsx` → `activatePromptVersion()` · `frontend/lib/api.ts`.
   3. The selected version's template, `VariableChips()` · `frontend/components/prompts/prompt-detail.tsx`, and `RenderPreview()` · `frontend/components/prompts/prompt-detail.tsx`: "Render vN" → `preview()` · `frontend/components/prompts/prompt-detail.tsx` → `renderPrompt()` · `frontend/lib/api.ts`.
   4. `VersionEditor()` · `frontend/components/prompts/prompt-detail.tsx` — "Save as new version" → `saveVersion()` · `frontend/components/prompts/prompt-detail.tsx` → `run()` · `frontend/components/prompts/prompt-detail.tsx` → `addPromptVersion()` · `frontend/lib/api.ts`.
   5. Edit description → `run()` · `frontend/components/prompts/prompt-detail.tsx` → `updatePromptDescription()` · `frontend/lib/api.ts`.
   6. Archive → confirm dialog → `run()` · `frontend/components/prompts/prompt-detail.tsx` → `archivePrompt()` · `frontend/lib/api.ts`.
   7. After each successful `run`, the panel reloads and the list refreshes (`reload()` · `frontend/components/prompts/prompts-page.tsx`).
5. All write calls send the admin key as `X-Admin-Key` (when one is entered).

### 13.5 Frontend: prompts in Chat

1. The load-prompts effect in `Chat()` · `frontend/components/chat.tsx` fills the Prompt dropdown (`listPrompts()` · `frontend/lib/api.ts`).
2. Choosing a prompt shows one input per variable of its active version.
3. `send()` · `frontend/components/chat.tsx` sends `prompt: {name, variables}`; the reply's footer shows `prompt <name> v<N>`.

### 13.6 Prompt tests

Fixtures (`backend/tests/conftest.py`):
`database()` · `backend/tests/conftest.py` (empty tables on SQLite, or Postgres via `TEST_DATABASE_URL`; uses `Database.create_all()` · `backend/app/db/session.py` and `Database.drop_all()` · `backend/app/db/session.py`) →
`prompt_service()` · `backend/tests/conftest.py` →
`api_factory()` · `backend/tests/conftest.py` (its inner `factory()` · `backend/tests/conftest.py` builds an `AppClient`: `AppClient.__init__()` · `backend/tests/conftest.py`, `AppClient.__aenter__()` · `backend/tests/conftest.py` starts the app, `AppClient.__aexit__()` · `backend/tests/conftest.py` stops it).

- Template (`backend/tests/test_prompt_template.py`):
  `test_extract_variables_in_order_without_duplicates()` · `backend/tests/test_prompt_template.py`,
  `test_extract_ignores_things_that_are_not_variables()` · `backend/tests/test_prompt_template.py`,
  `test_render_fills_values_and_ignores_extras()` · `backend/tests/test_prompt_template.py`,
  `test_render_leaves_non_variable_braces_alone()` · `backend/tests/test_prompt_template.py`,
  `test_render_missing_variable_names_all_missing()` · `backend/tests/test_prompt_template.py`
- Service (`backend/tests/test_prompt_service.py`):
  `test_create_makes_version_1_active()` · `backend/tests/test_prompt_service.py`,
  `test_duplicate_name_conflicts()` · `backend/tests/test_prompt_service.py`,
  `test_new_versions_count_up_and_activate_only_when_asked()` · `backend/tests/test_prompt_service.py`,
  `test_activate_older_version_is_a_rollback()` · `backend/tests/test_prompt_service.py`,
  `test_activate_missing_version()` · `backend/tests/test_prompt_service.py`,
  `test_unknown_prompt()` · `backend/tests/test_prompt_service.py`,
  `test_update_description()` · `backend/tests/test_prompt_service.py`,
  `test_archive_hides_and_blocks_use_and_changes()` · `backend/tests/test_prompt_service.py`,
  `test_render_specific_version_and_missing_variable()` · `backend/tests/test_prompt_service.py`,
  `test_default_prompt_applies_only_without_system()` · `backend/tests/test_prompt_service.py`,
  `test_no_prompt_and_no_default_returns_none()` · `backend/tests/test_prompt_service.py`,
  `test_list_summaries()` · `backend/tests/test_prompt_service.py`,
  `test_seed_is_idempotent()` · `backend/tests/test_prompt_service.py`
- API + chat (`backend/tests/test_prompts_api.py`):
  `parse_sse()` · `backend/tests/test_prompts_api.py` (helper),
  `test_prompt_lifecycle()` · `backend/tests/test_prompts_api.py`,
  `test_errors_have_clear_codes()` · `backend/tests/test_prompts_api.py`,
  `test_admin_key_required_for_writes_when_configured()` · `backend/tests/test_prompts_api.py`,
  `test_without_database_prompts_return_503_and_chat_still_works()` · `backend/tests/test_prompts_api.py`,
  `test_chat_uses_rendered_prompt_then_extra_system()` · `backend/tests/test_prompts_api.py`,
  `test_chat_with_pinned_version_and_missing_variable()` · `backend/tests/test_prompts_api.py`,
  `test_default_prompt_applies_when_request_has_no_system()` · `backend/tests/test_prompts_api.py`,
  `test_stream_reports_prompt_and_errors_before_stream()` · `backend/tests/test_prompts_api.py`,
  `test_health_reports_database()` · `backend/tests/test_prompts_api.py`
