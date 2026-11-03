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
14. [Knowledge bases / RAG (Layer 3)](#14-knowledge-bases--rag-layer-3)
15. [MCP tools (Layer 4)](#15-mcp-tools-layer-4)

---

## The big picture

```
Browser ──► Next.js chat UI ──► FastAPI route ──► ChatService ──► ProviderRegistry.resolve()
                                                       │
                                                       ├──► PromptService (managed prompt from Postgres, Layer 2)
                                                       ├──► RagService (cited passages from pgvector, Layer 3)
                                                       ├──► MCPService → Toolbox (allowed MCP tools, Layer 4)
                                                       ▼
                                          provider adapter (Anthropic / OpenAI / Gemini / Ollama / Mock)
                                                       │
                                                       ▼
                                          vendor SDK or HTTP call ──► the LLM
                                                       │  (asks for tools?)
                                                       ▼
                                   Toolbox ──► MCPManager ──► MCP servers ──► results back to the LLM
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
   7. With a database: `build_embedder()` · `backend/app/rag/embeddings.py` picks the embedding provider (section 14.1) and logs `embedding model: ...`.
   8. `RagRuntime.__init__()` · `backend/app/rag/runtime.py` → saved on `app.state.rag`; then `RagRuntime.start()` · `backend/app/rag/runtime.py` starts the background indexing worker (section 14.2).
   9. `MCPManager.__init__()` · `backend/app/mcp/manager.py` → saved on `app.state.mcp`. MCP servers are connected on first use (section 15.1).
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
   3. `RagRuntime.status()` · `backend/app/rag/runtime.py` — knowledge bases `ok`, `unavailable` or `disabled`.
4. Returns `{"status": "ok", "providers": [...], "database": "ok" | "unavailable" | "disabled", "rag": "ok" | "unavailable" | "disabled", "tools": <same as database>}`.

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
   3. `get_rag_service()` · `backend/app/api/deps.py` — a `RagService` on the same session (or `None` without a database or embedder).
   4. `get_mcp_service()` · `backend/app/api/deps.py` — an `MCPService` on the same session (or `None` without a database).
   5. `get_registry()` · `backend/app/api/deps.py`
   6. `ChatService.__init__()` · `backend/app/services/chat_service.py` — keeps the registry, the prompt, RAG and MCP services, the RAG runtime, `MCP_MAX_TOOL_ROUNDS`, and `release_db` (closes the session).
4. `chat()` · `backend/app/api/v1/routes.py` — the route; hands off to the service.
5. `ChatService.complete()` · `backend/app/services/chat_service.py` — the main work:
   1. `ChatService._prepare()` · `backend/app/services/chat_service.py` — everything before the first model call:
      1. `ProviderRegistry.resolve()` · `backend/app/llm/registry.py` — `"anthropic/claude-opus-5-5"` → (Anthropic adapter, `"claude-opus-5-5"`). Unknown or not-allowed model → 404.
         - No model in the request → uses `ProviderRegistry.default_model()` · `backend/app/llm/registry.py`.
      2. `ChatService._build_system()` · `backend/app/services/chat_service.py` — works out the final system prompt: the managed prompt (if the request names one, or a default applies), then the request's own `system` text. Details in section 13.3.
      3. `ChatService._retrieve()` · `backend/app/services/chat_service.py` — if the request has `rag`, searches those collections and adds a numbered "Sources" block to the system prompt. Details in section 14.4.
      4. `ChatService._toolbox()` · `backend/app/services/chat_service.py` — if the request has `tools`, loads the enabled tools of those MCP servers. Details in section 15.5.
      5. `release_db` closes the request's database session (nothing below needs it).
      6. `_to_llm_request()` · `backend/app/services/chat_service.py` — converts the public `ChatRequest` into the internal `LLMRequest` (model name without the `provider/` prefix, final system prompt, tools).
   2. `_new_id()` · `backend/app/services/chat_service.py` — makes the reply id `chat_<random>`. Starts a timer.
   3. `provider.complete(...)` — the chosen adapter's `complete` (section 7), e.g. `AnthropicProvider.complete()` · `backend/app/llm/providers/anthropic_provider.py`. It calls the vendor and returns an `LLMResult`.
   4. If the model asked for tools: run them and call the model again, up to `MCP_MAX_TOOL_ROUNDS` times (section 15.5). Tokens are summed with `_add_usage()` · `backend/app/services/chat_service.py`.
   5. `_ms_since()` · `backend/app/services/chat_service.py` — how long it all took.
   6. `ChatService._log()` · `backend/app/services/chat_service.py` — logs `chat completed` with provider, model, prompt (`_prompt_label()` · `backend/app/services/chat_service.py` → `support-agent@v3`), sources, tool calls, tokens, latency, finish reason.
   7. Builds the `ChatResponse`, including `prompt: {name, version}` when a managed prompt was used, `citations` when `rag` was used, and `tool_calls` when `tools` was used.
6. FastAPI turns the `ChatResponse` into JSON and sends it.

If anything in step 5 raises an error, go to section 8.

---

## 6. Chat, streaming — `POST /v1/chat/stream`

**What happens:** same as section 5, but the answer arrives piece by piece as server-sent events
(`start` → many `delta`, with `tool_call`/`tool_result` pairs when tools run → `done`, or `error`).

1. `request_context()` · `backend/app/main.py`, body validation with `ChatRequest._first_message_from_user()` · `backend/app/schemas/chat.py`, and `get_chat_service()` · `backend/app/api/deps.py` — same as section 5, steps 1–3.
2. `chat_stream()` · `backend/app/api/v1/routes.py` — the route.
   1. Calls `ChatService.stream()` · `backend/app/services/chat_service.py`, which gives back an event generator (nothing runs yet).
   2. **Pulls the first event right away.** That makes the generator run until its first `yield`:
      1. `ChatService._prepare()` · `backend/app/services/chat_service.py` — the same preparation as section 5: `ProviderRegistry.resolve()` · `backend/app/llm/registry.py` (404 if unknown), `ChatService._build_system()` · `backend/app/services/chat_service.py` (an unknown prompt or missing variable fails *here*, as a normal HTTP error), `ChatService._retrieve()` · `backend/app/services/chat_service.py` (an unknown collection fails here too), `ChatService._toolbox()` · `backend/app/services/chat_service.py` (an unknown MCP server too), then `_to_llm_request()` · `backend/app/services/chat_service.py`.
      2. `_new_id()` · `backend/app/services/chat_service.py` and start a timer.
      3. `provider.stream(...)` — the adapter's `stream` (section 7), e.g. `OpenAIProvider.stream()` · `backend/app/llm/providers/openai_provider.py`.
      4. Waits for the adapter's **first chunk**. This is when the vendor is actually contacted, so a bad key or rate limit fails *here* and becomes a normal HTTP error (section 8).
      5. Yields the `start` event (with `prompt` if a managed prompt was used, and `citations` if `rag` was used).
   3. Wraps the rest in an `EventSourceResponse` (server-sent events, with a keep-alive ping every 15 s).
3. `_sse()` · `backend/app/api/v1/routes.py` — turns each event into an SSE `data:` line, while `ChatService.stream()` · `backend/app/services/chat_service.py` keeps running:
   1. For every text chunk → yields a `delta` event.
   2. A final chunk with tool calls (and `ChatService._wants_tools()` · `backend/app/services/chat_service.py` says yes) → `tool_call` events, run the tools, `tool_result` events, then the adapter's stream starts again (section 15.5).
   3. When the adapter's final chunk arrives (`done=True`) → `_ms_since()` · `backend/app/services/chat_service.py`, logs `chat stream completed` (`ChatService._log()` · `backend/app/services/chat_service.py`), yields the `done` event, stops.
   4. If the adapter fails mid-stream → logs `chat stream failed`, yields an `error` event, stops. (The HTTP status is already 200 at this point, so the error travels inside the stream.)

---

## 7. Provider adapters

Every adapter follows the same contract, defined in `backend/app/llm/base.py`:

- `LLMProvider.__init__()` · `backend/app/llm/base.py` — store the allowed model list.
- `LLMProvider.complete()` · `backend/app/llm/base.py` — one full reply.
- `LLMProvider.stream()` · `backend/app/llm/base.py` — text pieces, then one final chunk.
- `LLMProvider.aclose()` · `backend/app/llm/base.py` — close network clients at shutdown.

Tools (Layer 4): when `LLMRequest.tools` is set, `_params`/`_payload` also send the tools and replay
earlier tool rounds from `LLMRequest.turns`, and `complete`/`stream` return the model's `tool_calls`
(details per vendor in section 15.6).

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
| `CollectionNotFoundError` | 404 | `collection_not_found` |
| `CollectionConflictError` | 409 | `collection_conflict` |
| `DocumentNotFoundError` | 404 | `document_not_found` |
| `DuplicateDocumentError` | 409 | `duplicate_document` |
| `UnsupportedFileTypeError` | 415 | `unsupported_file_type` |
| `FileTooLargeError` | 413 | `file_too_large` |
| `DocumentParseError` | 422 | `document_parse_error` |
| `EmbeddingModelMismatchError` | 409 | `embedding_model_mismatch` |
| `RagUnavailableError` | 503 | `rag_unavailable` |

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
2. `MCPManager.aclose()` · `backend/app/mcp/manager.py` — closes every MCP connection and stops stdio server processes (section 15.1).
3. `ProviderRegistry.aclose()` · `backend/app/llm/registry.py` — closes each adapter, one by one; a failure in one is logged and doesn't stop the others:
   - `AnthropicProvider.aclose()` · `backend/app/llm/providers/anthropic_provider.py`
   - `OpenAIProvider.aclose()` · `backend/app/llm/providers/openai_provider.py`
   - `GeminiProvider.aclose()` · `backend/app/llm/providers/gemini_provider.py`
   - `OllamaProvider.aclose()` · `backend/app/llm/providers/ollama_provider.py`
   - Mock uses `LLMProvider.aclose()` · `backend/app/llm/base.py` (does nothing).
4. `RagRuntime.stop()` · `backend/app/rag/runtime.py` — stops the indexing worker (`IngestionWorker.stop()` · `backend/app/rag/runtime.py`) and closes the embedder's client (e.g. `OpenAIEmbedder.aclose()` · `backend/app/rag/embeddings.py`).
5. `Database.aclose()` · `backend/app/db/session.py` — closes the database connection pool (only if the app opened it).

---

## 11. Frontend

### 11.1 Page load

1. Next.js renders `RootLayout()` · `frontend/app/layout.tsx` — the `<html>`/`<body>` shell, fonts, and the top bar `NavBar()` · `frontend/components/nav.tsx` (Chat | Prompts | Knowledge | Tools links).
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
6. The **load-collections effect** inside `Chat()` · `frontend/components/chat.tsx`: `listCollections()` · `frontend/lib/api.ts` → if there are any, a **Knowledge** button appears (section 14.6).
7. The **load-tools effect** inside `Chat()` · `frontend/components/chat.tsx`: `listAvailableTools()` · `frontend/lib/api.ts` → if any server has enabled tools, a **Tools** button appears (section 15.8).

### 11.2 Sending a message

1. The user presses Enter (or clicks Send) → `send()` · `frontend/components/chat.tsx`.
   1. Stops early if a prompt is selected and any of its variables is empty (the hint under the box names them). Otherwise builds the history from earlier turns (skipping failed ones) and adds the new user message.
   2. `nextKey()` · `frontend/components/chat.tsx` (twice) — unique keys for the new user turn and the empty assistant turn.
   3. Adds both turns to the screen; clears the input; marks the chat busy.
   4. `streamChat()` · `frontend/lib/api.ts` — `POST /v1/chat/stream`, with `prompt: {name, variables}` when a prompt is selected `rag: {collections}` when knowledge bases are ticked, and `tools: {servers}` when Tools servers are ticked.
      1. Not OK → `toApiError()` · `frontend/lib/api.ts` → throws `ApiError` · `frontend/lib/api.ts`.
      2. Reads the response body piece by piece; splits it at blank lines.
      3. `parseEvent()` · `frontend/lib/api.ts` — one SSE block → one event object.
      4. Calls the event handler inside `send()` · `frontend/components/chat.tsx` for each event:
         - `start` → `updateTurn()` · `frontend/components/chat.tsx` saves provider/model/prompt and the citations (shown as "Sources").
         - `delta` → `updateTurn()` · `frontend/components/chat.tsx` appends the text.
         - `tool_call` / `tool_result` → `updateTurn()` · `frontend/components/chat.tsx` adds / completes a line in the reply's tool list (`ToolTrace()` · `frontend/components/chat.tsx`).
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
7. `requires_pgvector` (in `backend/tests/conftest.py`) skips the knowledge-base tests unless `TEST_DATABASE_URL` is Postgres (section 14.7).

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

---

## 14. Knowledge bases / RAG (Layer 3)

Upload documents into collections; chat searches them and cites sources. Code lives in
`backend/app/rag/`, routes in `backend/app/api/v1/collections.py`, UI in `frontend/components/knowledge/`.

```
upload ──► parse ──► "pending" ──► worker: chunk ──► embed ──► chunks table (vector + full-text) ──► "ready"
chat with rag ──► embed question ──► vector search + keyword search ──► RRF merge ──► "Sources" in system prompt
```

### 14.1 Startup: choosing the embedder and starting the worker

1. In `lifespan()` · `backend/app/main.py`, with a database:
   1. `build_embedder()` · `backend/app/rag/embeddings.py` — `EMBEDDING_PROVIDER`, or the first configured of OpenAI, Gemini, Ollama, mock. Creates one of:
      - `OpenAIEmbedder.__init__()` · `backend/app/rag/embeddings.py`
      - `GeminiEmbedder.__init__()` · `backend/app/rag/embeddings.py`
      - `OllamaEmbedder.__init__()` · `backend/app/rag/embeddings.py`
      - `MockEmbedder.__init__()` · `backend/app/rag/embeddings.py`
      - each first calls `Embedder.__init__()` · `backend/app/rag/embeddings.py` (model name, 768 dimensions); `Embedder.id()` · `backend/app/rag/embeddings.py` gives the label stored on collections.
   2. `RagRuntime.__init__()` · `backend/app/rag/runtime.py` — keeps database + embedder + settings, and creates `IngestionWorker.__init__()` · `backend/app/rag/runtime.py`.
   3. `RagRuntime.start()` · `backend/app/rag/runtime.py` → `IngestionWorker.start()` · `backend/app/rag/runtime.py`:
      1. Starts `RAG_INGEST_CONCURRENCY` worker tasks running `IngestionWorker._run()` · `backend/app/rag/runtime.py`.
      2. `RagRuntime.check()` · `backend/app/rag/runtime.py` — database? Postgres? embedder? pgvector? (`Database.is_postgres()` · `backend/app/db/session.py`, `Database.has_pgvector()` · `backend/app/db/session.py`).
      3. `RagRepository.unfinished_document_ids()` · `backend/app/rag/repository.py` → each one `IngestionWorker.enqueue()` · `backend/app/rag/runtime.py` (documents interrupted by the last shutdown).

### 14.2 Adding documents (`POST /v1/collections/{name}/documents`, `.../documents/text`)

Every collections route first runs `get_db_session()` · `backend/app/api/deps.py` → `get_rag_service()` · `backend/app/api/deps.py` (`RagService.__init__()` · `backend/app/rag/service.py`, which creates `RagRepository.__init__()` · `backend/app/rag/repository.py`) → `require_rag_service()` · `backend/app/api/deps.py` (calls `RagRuntime.check()` · `backend/app/rag/runtime.py`; 503 with the reason if RAG can't work). Writes also run `require_admin()` · `backend/app/api/deps.py`.

1. Files: `upload_documents()` · `backend/app/api/v1/collections.py` — for each file:
   1. `RagService.add_file()` · `backend/app/rag/service.py`
      1. `RagService._require_collection()` · `backend/app/rag/service.py` (404 unknown, 409 archived) → `RagRepository.get_collection()` · `backend/app/rag/repository.py`.
      2. Size check (`RAG_MAX_UPLOAD_MB`, else 413).
      3. In a worker thread: `extract_text()` · `backend/app/rag/parsers.py`
         1. `detect_type()` · `backend/app/rag/parsers.py` — by extension, then content type (else 415).
         2. One reader: `_from_text()` · `backend/app/rag/parsers.py`, `_from_markdown()` · `backend/app/rag/parsers.py`, `_from_pdf()` · `backend/app/rag/parsers.py`, `_from_docx()` · `backend/app/rag/parsers.py`, or `_from_html()` · `backend/app/rag/parsers.py`.
            - Text readers use `_decode()` · `backend/app/rag/parsers.py`.
            - HTML uses the `_HTMLText` parser: `_HTMLText.__init__()` · `backend/app/rag/parsers.py`, `_HTMLText.handle_starttag()` · `backend/app/rag/parsers.py`, `_HTMLText.handle_data()` · `backend/app/rag/parsers.py`, `_HTMLText.handle_endtag()` · `backend/app/rag/parsers.py`, then `_HTMLText.text()` · `backend/app/rag/parsers.py`.
         3. `normalize_text()` · `backend/app/rag/parsers.py` — clean line ends, no NULs; empty → 422.
      4. `RagService._add()` · `backend/app/rag/service.py`
         1. SHA-256 of the text → `RagRepository.find_by_hash()` · `backend/app/rag/repository.py` (same text already there → 409).
         2. New `Document` row, status `pending` → `RagRepository.add()` · `backend/app/rag/repository.py` → `RagService._commit()` · `backend/app/rag/service.py`.
         3. `RagRuntime.enqueue()` · `backend/app/rag/runtime.py` → `IngestionWorker.enqueue()` · `backend/app/rag/runtime.py`.
         4. Returns `_document_out()` · `backend/app/rag/service.py`.
   2. A refused file (when several were sent) is listed in `errors`; the rest still go through. Reply: **202**.
2. Pasted text: `add_text_document()` · `backend/app/api/v1/collections.py` → `RagService.add_text()` · `backend/app/rag/service.py` → `normalize_text()` · `backend/app/rag/parsers.py` → `RagService._add()` · `backend/app/rag/service.py` (same steps).

### 14.3 Background indexing (the worker)

`IngestionWorker._run()` · `backend/app/rag/runtime.py` takes the next id from the queue, opens its own session, and calls:

1. `RagService.process_document()` · `backend/app/rag/service.py`
   1. `RagRepository.get_document()` · `backend/app/rag/repository.py` (deleted meanwhile → stop) → status `processing`, commit.
   2. The collection's embedding model must equal the server's (else `failed` with "re-index the collection").
   3. `split_text()` · `backend/app/rag/chunking.py` — paragraphs packed up to `RAG_CHUNK_SIZE`, with overlap:
      - `_fit()` · `backend/app/rag/chunking.py` splits a long paragraph by sentences;
      - `_split_words()` · `backend/app/rag/chunking.py` splits a long sentence by words;
      - `_tail()` · `backend/app/rag/chunking.py` takes the overlap from the previous chunk.
   4. `Embedder.embed()` · `backend/app/rag/embeddings.py` (`kind="document"`) — batches of 64 → `_embed_batch` of the chosen provider:
      `OpenAIEmbedder._embed_batch()` · `backend/app/rag/embeddings.py`, `GeminiEmbedder._embed_batch()` · `backend/app/rag/embeddings.py`, `OllamaEmbedder._embed_batch()` · `backend/app/rag/embeddings.py`, or `MockEmbedder._embed_batch()` · `backend/app/rag/embeddings.py` (→ `MockEmbedder._vector()` · `backend/app/rag/embeddings.py`).
      The base `Embedder._embed_batch()` · `backend/app/rag/embeddings.py` is the contract each one fills in. Every vector is checked (768 numbers) and scaled by `normalize()` · `backend/app/rag/embeddings.py`.
   5. `RagRepository.replace_chunks()` · `backend/app/rag/repository.py` — old chunks deleted, new ones staged → status `ready`, `chunk_count`, commit.
   6. Any error → roll back, status `failed` with the reason, commit.
2. `IngestionWorker.drain()` · `backend/app/rag/runtime.py` waits until the queue is empty (scripts and tests).

### 14.4 Search and chat

1. Test search: `search_collection()` · `backend/app/api/v1/collections.py` → `RagService.search_collection()` · `backend/app/rag/service.py` → `RagService.search()` · `backend/app/rag/service.py`.
2. Chat: `ChatService._retrieve()` · `backend/app/services/chat_service.py` (only when the request has `rag`)
   1. `RagRuntime.check()` · `backend/app/rag/runtime.py` — 503 with the reason if RAG can't work.
   2. Query = the last user message → `RagService.retrieve_for_chat()` · `backend/app/rag/service.py` → `RagService.search()` · `backend/app/rag/service.py`.
3. Inside `RagService.search()` · `backend/app/rag/service.py`:
   1. `RagService._require_collection()` · `backend/app/rag/service.py` for each name; each collection's embedding model must match the server's (else 409).
   2. `Embedder.embed()` · `backend/app/rag/embeddings.py` with `kind="query"` → the question's vector.
   3. `RagRepository.vector_search()` · `backend/app/rag/repository.py` — closest passages by meaning (`RAG_CANDIDATES`).
   4. `RagRepository.keyword_search()` · `backend/app/rag/repository.py` — passages sharing words (`RAG_CANDIDATES`).
   5. `fuse_rrf()` · `backend/app/rag/retrieval.py` — merges both lists; top `top_k`; scores rounded by `_round()` · `backend/app/rag/service.py`.
4. Back in `RagService.retrieve_for_chat()` · `backend/app/rag/service.py`: the read transaction ends (no DB connection held during the LLM call), citations are built, and `build_context()` · `backend/app/rag/retrieval.py` writes the "Sources" block.
5. Back in `ChatService._retrieve()` · `backend/app/services/chat_service.py`: the block is appended to the system prompt; no hits → nothing added, `citations: []`.

### 14.5 Managing collections and documents

- List `GET /v1/collections` → `list_collections()` · `backend/app/api/v1/collections.py` → `RagService.list_collections()` · `backend/app/rag/service.py` → `RagRepository.list_collections()` · `backend/app/rag/repository.py` + `RagRepository.document_counts()` · `backend/app/rag/repository.py` → `_summary()` · `backend/app/rag/service.py`.
- Create `POST /v1/collections` → `create_collection()` · `backend/app/api/v1/collections.py` → `RagService.create_collection()` · `backend/app/rag/service.py` (stores the embedder's id) → `RagService._commit()` · `backend/app/rag/service.py` → `RagService._detail()` · `backend/app/rag/service.py`.
- Details `GET /v1/collections/{name}` → `get_collection()` · `backend/app/api/v1/collections.py` → `RagService.get_collection()` · `backend/app/rag/service.py` → `RagService._detail()` · `backend/app/rag/service.py` (adds `needs_reindex`; uses `RagService._embedder()` · `backend/app/rag/service.py`).
- Archive `DELETE /v1/collections/{name}` → `archive_collection()` · `backend/app/api/v1/collections.py` → `RagService.archive_collection()` · `backend/app/rag/service.py`.
- Re-index a collection `POST /v1/collections/{name}/reindex` → `reindex_collection()` · `backend/app/api/v1/collections.py` → `RagService.reindex_collection()` · `backend/app/rag/service.py` (sets the current model, `_reset()` · `backend/app/rag/service.py` every document, queues them all).
- List documents → `list_documents()` · `backend/app/api/v1/collections.py` → `RagService.list_documents()` · `backend/app/rag/service.py` → `RagRepository.list_documents()` · `backend/app/rag/repository.py`.
- One document → `get_document()` · `backend/app/api/v1/collections.py` → `RagService.get_document()` · `backend/app/rag/service.py` → `RagService._require_document()` · `backend/app/rag/service.py`.
- Delete a document → `delete_document()` · `backend/app/api/v1/collections.py` → `RagService.delete_document()` · `backend/app/rag/service.py` → `RagRepository.delete()` · `backend/app/rag/repository.py` (its chunks are deleted by the database).
- Re-index a document → `reindex_document()` · `backend/app/api/v1/collections.py` → `RagService.reindex_document()` · `backend/app/rag/service.py` → `_reset()` · `backend/app/rag/service.py` → queue.
- Setting up the tables: `uv run alembic upgrade head` runs `upgrade()` · `backend/migrations/versions/0002_create_rag.py` (enables pgvector, creates the three tables and the vector + full-text indexes); `downgrade()` · `backend/migrations/versions/0002_create_rag.py` drops them. Tests create them with `Database.create_all()` · `backend/app/db/session.py`, which uses `Database._tables()` · `backend/app/db/session.py` to skip them on SQLite.
- Shutdown: `RagRuntime.stop()` · `backend/app/rag/runtime.py` → `IngestionWorker.stop()` · `backend/app/rag/runtime.py`, then the embedder's `aclose`: `Embedder.aclose()` · `backend/app/rag/embeddings.py` (mock), `OpenAIEmbedder.aclose()` · `backend/app/rag/embeddings.py`, `GeminiEmbedder.aclose()` · `backend/app/rag/embeddings.py`, `OllamaEmbedder.aclose()` · `backend/app/rag/embeddings.py`.

### 14.6 Frontend: the Knowledge page (`/knowledge`) and chat

1. `Knowledge()` · `frontend/app/knowledge/page.tsx` → `KnowledgePage()` · `frontend/components/knowledge/knowledge-page.tsx`
   1. Load-list effect → `listCollections()` · `frontend/lib/api.ts`; `reload()` · `frontend/components/knowledge/knowledge-page.tsx` re-runs it after changes.
   2. **New collection** → `NewCollectionForm()` · `frontend/components/knowledge/knowledge-page.tsx` → `submit()` · `frontend/components/knowledge/knowledge-page.tsx` → `createCollection()` · `frontend/lib/api.ts`.
2. Clicking a collection → `CollectionPanel()` · `frontend/components/knowledge/collection-panel.tsx`
   1. Load effect → `getCollection()` · `frontend/lib/api.ts` + `listDocuments()` · `frontend/lib/api.ts`; a polling effect reloads every 2 s while any document is `pending`/`processing`.
   2. Every change goes through `run()` · `frontend/components/knowledge/collection-panel.tsx` (busy state, error, refresh).
   3. `UploadBox()` · `frontend/components/knowledge/collection-panel.tsx` — "Choose files" or drop (`onDrop()` · `frontend/components/knowledge/collection-panel.tsx`) → `upload()` · `frontend/components/knowledge/collection-panel.tsx` → `uploadDocuments()` · `frontend/lib/api.ts` (multipart).
   4. `PasteTextForm()` · `frontend/components/knowledge/collection-panel.tsx` → `submit()` · `frontend/components/knowledge/collection-panel.tsx` → `addTextDocument()` · `frontend/lib/api.ts`.
   5. `DocumentTable()` · `frontend/components/knowledge/collection-panel.tsx` — rows use `typeLabel()` · `frontend/components/knowledge/collection-panel.tsx`, `formatBytes()` · `frontend/components/knowledge/collection-panel.tsx`, `StatusBadge()` · `frontend/components/knowledge/collection-panel.tsx`; Re-index → `reindexDocument()` · `frontend/lib/api.ts`; Delete → `deleteDocument()` · `frontend/lib/api.ts`.
   6. Re-index collection banner (when `needs_reindex`) → `reindexCollection()` · `frontend/lib/api.ts`; Archive → `archiveCollection()` · `frontend/lib/api.ts`.
   7. `SearchTester()` · `frontend/components/knowledge/collection-panel.tsx` → `search()` · `frontend/components/knowledge/collection-panel.tsx` → `searchCollection()` · `frontend/lib/api.ts`.
   8. All collection URLs are built by `collectionPath()` · `frontend/lib/api.ts`.
3. Chat: the load-collections effect in `Chat()` · `frontend/components/chat.tsx` fills the **Knowledge** picker; `send()` · `frontend/components/chat.tsx` adds `rag: {collections}`; the `start` event's citations show as a "Sources" list under the reply.

### 14.7 Knowledge-base tests

Run the database ones with `TEST_DATABASE_URL=postgresql+asyncpg://...`; on SQLite they're skipped.

- Parsers (`backend/tests/test_rag_parsers.py`): helpers `make_pdf()` · `backend/tests/test_rag_parsers.py` and `make_docx()` · `backend/tests/test_rag_parsers.py`;
  `test_detect_type_by_extension_then_content_type()` · `backend/tests/test_rag_parsers.py`,
  `test_text_and_markdown_title()` · `backend/tests/test_rag_parsers.py`,
  `test_latin1_text_falls_back_cleanly()` · `backend/tests/test_rag_parsers.py`,
  `test_html_skips_scripts_and_uses_title()` · `backend/tests/test_rag_parsers.py`,
  `test_docx_paragraphs_tables_and_title()` · `backend/tests/test_rag_parsers.py`,
  `test_pdf_text()` · `backend/tests/test_rag_parsers.py`,
  `test_pdf_without_text_explains_scans()` · `backend/tests/test_rag_parsers.py`,
  `test_broken_files_give_parse_errors()` · `backend/tests/test_rag_parsers.py`,
  `test_empty_text_rejected()` · `backend/tests/test_rag_parsers.py`,
  `test_normalize_collapses_blank_lines()` · `backend/tests/test_rag_parsers.py`
- Chunking (`backend/tests/test_rag_chunking.py`):
  `test_short_text_is_one_chunk()` · `backend/tests/test_rag_chunking.py`,
  `test_chunks_respect_size_and_keep_paragraphs_together()` · `backend/tests/test_rag_chunking.py`,
  `test_overlap_repeats_the_end_of_the_previous_chunk()` · `backend/tests/test_rag_chunking.py`,
  `test_long_paragraph_splits_by_sentence_then_words()` · `backend/tests/test_rag_chunking.py`,
  `test_overlap_is_capped_at_half_the_size()` · `backend/tests/test_rag_chunking.py`,
  `test_empty_text_gives_no_chunks()` · `backend/tests/test_rag_chunking.py`
- Embeddings (`backend/tests/test_rag_embeddings.py`): helpers `vector()` · `backend/tests/test_rag_embeddings.py`, `is_unit()` · `backend/tests/test_rag_embeddings.py`, and an inner fake server `reply()` · `backend/tests/test_rag_embeddings.py` in three tests;
  `test_openai_sends_768_dimensions_batches_and_normalizes()` · `backend/tests/test_rag_embeddings.py`,
  `test_openai_errors_use_the_chat_error_mapping()` · `backend/tests/test_rag_embeddings.py`,
  `test_gemini_task_type_and_dimensions()` · `backend/tests/test_rag_embeddings.py`,
  `test_ollama_nomic_prefixes_and_wrong_size_is_rejected()` · `backend/tests/test_rag_embeddings.py`,
  `test_mock_embedder_is_deterministic_and_word_based()` · `backend/tests/test_rag_embeddings.py`,
  `test_normalize_keeps_zero_vectors()` · `backend/tests/test_rag_embeddings.py`,
  `test_build_embedder_picks_configured_or_first_available()` · `backend/tests/test_rag_embeddings.py`
- Retrieval (`backend/tests/test_rag_retrieval.py`):
  `test_rrf_rewards_items_found_by_both_methods()` · `backend/tests/test_rag_retrieval.py`,
  `test_context_numbers_sources_and_marks_them_as_reference()` · `backend/tests/test_rag_retrieval.py`
- End to end (`backend/tests/test_rag_api.py`): helpers `wait_ready()` · `backend/tests/test_rag_api.py`, `make_collection()` · `backend/tests/test_rag_api.py`, `parse_sse()` · `backend/tests/test_rag_api.py`;
  `test_upload_files_index_and_search()` · `backend/tests/test_rag_api.py`,
  `test_keyword_search_finds_exact_codes()` · `backend/tests/test_rag_api.py`,
  `test_keyword_search_matches_any_word_not_all()` · `backend/tests/test_rag_api.py`,
  `test_upload_errors()` · `backend/tests/test_rag_api.py`,
  `test_delete_and_reindex_document()` · `backend/tests/test_rag_api.py`,
  `test_embedding_model_change_needs_reindex()` · `backend/tests/test_rag_api.py`,
  `test_archived_collection_is_hidden_and_unusable()` · `backend/tests/test_rag_api.py`,
  `test_chat_adds_sources_and_returns_citations()` · `backend/tests/test_rag_api.py`,
  `test_unfinished_documents_are_requeued_on_start()` · `backend/tests/test_rag_api.py`,
  `test_without_postgres_rag_is_unavailable_but_chat_works()` · `backend/tests/test_rag_api.py`,
  `test_without_database_rag_is_unavailable()` · `backend/tests/test_rag_api.py`

---

## 15. MCP tools (Layer 4)

Connect MCP servers, let an admin choose which tools models may use, and run the model ↔ tool loop.
Code lives in `backend/app/mcp/`, routes in `backend/app/api/v1/tools.py`, the loop in
`backend/app/services/chat_service.py`, UI in `frontend/components/tools/`.

```
register ──► connect (stdio process or HTTP) ──► list tools ──► mcp_tools rows, all OFF
admin switches tools ON ──► chat with tools: {servers}
chat ──► model ──► tool calls? ──► Toolbox ──► MCPManager ──► MCP server ──► results ──► model ──► ... ──► answer
```

### 15.1 Startup, connections and shutdown

1. In `lifespan()` · `backend/app/main.py`: `MCPManager.__init__()` · `backend/app/mcp/manager.py` → saved on `app.state.mcp`. Nothing connects yet.
2. The first time a request needs a server, `MCPManager._client()` · `backend/app/mcp/manager.py` (called by `MCPManager.list_tools()` · `backend/app/mcp/manager.py` or `MCPManager.call_tool()` · `backend/app/mcp/manager.py`):
   1. stdio while `MCP_ALLOW_STDIO=false` → 403.
   2. Takes the server's lock; reuses a live connection whose `ServerTarget.fingerprint()` · `backend/app/mcp/manager.py` still matches (`_Connection.alive()` · `backend/app/mcp/manager.py`); otherwise `MCPManager.disconnect()` · `backend/app/mcp/manager.py` and a new one:
   3. `_Connection.__init__()` · `backend/app/mcp/manager.py` → `_Connection.open()` · `backend/app/mcp/manager.py` starts a background task running `_Connection._run()` · `backend/app/mcp/manager.py`:
      1. `_Connection._transport()` · `backend/app/mcp/manager.py` — stdio parameters (command, args, env) or a streamable-HTTP transport with the headers.
      2. Opens the MCP `Client` (handshake), signals "ready", then waits until asked to stop.
      3. A startup failure is unwrapped by `_leaf()` · `backend/app/mcp/manager.py` and described by `_describe()` · `backend/app/mcp/manager.py`.
   4. `_Connection.open()` · `backend/app/mcp/manager.py` waits for "ready" (at most `MCP_CONNECT_TIMEOUT_S`), then `_Connection.client()` · `backend/app/mcp/manager.py` hands out the client.
3. Listing: `MCPManager.list_tools()` · `backend/app/mcp/manager.py` → `_all_tools()` · `backend/app/mcp/manager.py` (every page). A dropped connection (`_is_lost()` · `backend/app/mcp/manager.py`) → reconnect and list once more.
4. Calling: `MCPManager.call_tool()` · `backend/app/mcp/manager.py` — time limit `MCP_CALL_TIMEOUT_S`; dropped connection → reconnect, and retry once only if the tool isn't destructive.
5. `MCPManager.is_connected()` · `backend/app/mcp/manager.py` tells whether a server has a live connection (tests).
6. Shutdown, in `lifespan()` · `backend/app/main.py`: `MCPManager.aclose()` · `backend/app/mcp/manager.py` → `MCPManager.disconnect()` · `backend/app/mcp/manager.py` for each → `_Connection.close()` · `backend/app/mcp/manager.py` (stops stdio processes).

### 15.2 Registering a server — `POST /v1/tools/servers`

Every `/v1/tools` route first runs `get_db_session()` · `backend/app/api/deps.py` → `get_mcp_service()` · `backend/app/api/deps.py` (`MCPService.__init__()` · `backend/app/mcp/service.py`) → `require_mcp_service()` · `backend/app/api/deps.py` (503 `tools_unavailable` without a database). Routes under `/servers` and `/presets` also run `require_admin()` · `backend/app/api/deps.py`.

1. FastAPI checks the body with `ServerCreate._check_transport()` · `backend/app/mcp/schemas.py` (stdio needs `command`, http needs an `http(s)://` URL).
2. `create_server()` · `backend/app/api/v1/tools.py` → `MCPService.create_server()` · `backend/app/mcp/service.py`
   1. stdio switched off → 403; `MCPService._find_server()` · `backend/app/mcp/service.py` finds the name taken → 409.
   2. `encrypt_secrets()` · `backend/app/mcp/secrets.py` (→ `_fernet()` · `backend/app/mcp/secrets.py`, using `MCPService._secret_key()` · `backend/app/mcp/service.py`) for env values or headers; no `SECRET_KEY` with secrets → 400.
   3. Saves the row → `MCPService._commit()` · `backend/app/mcp/service.py`.
   4. `MCPService._refresh()` · `backend/app/mcp/service.py` (doesn't raise) — see 15.3. A failure is kept in `last_error`.
   5. `MCPService._out()` · `backend/app/mcp/service.py` → `_server_out()` · `backend/app/mcp/service.py` (names of secrets only) → **201**.
3. The **Use demo server** button first calls `list_presets()` · `backend/app/api/v1/tools.py` → `presets()` · `backend/app/mcp/service.py` (the demo server with this backend's Python).

### 15.3 Refreshing tools — `POST /v1/tools/servers/{name}/refresh`

1. `refresh_server()` · `backend/app/api/v1/tools.py` → `MCPService.refresh_server()` · `backend/app/mcp/service.py` → `MCPService._require_server()` · `backend/app/mcp/service.py` (404).
2. `MCPService._refresh()` · `backend/app/mcp/service.py`
   1. `MCPService._target()` · `backend/app/mcp/service.py` — row → `ServerTarget`, secrets via `decrypt_secrets()` · `backend/app/mcp/secrets.py`.
   2. `MCPService._end_read()` · `backend/app/mcp/service.py` — frees the database connection during the network call.
   3. `MCPManager.list_tools()` · `backend/app/mcp/manager.py` (15.1).
   4. `MCPService._upsert_tools()` · `backend/app/mcp/service.py` — existing rows via `MCPService._tools_by_server()` · `backend/app/mcp/service.py`; hints via `_hints()` · `backend/app/mcp/service.py`; new tools **off**; a tool that became destructive → off; vanished tools deleted.
   5. Saves `last_error = None`, `last_connected_at = now` → `MCPService._commit()` · `backend/app/mcp/service.py`. On failure: saves `last_error`, then 502.
3. Reply: `MCPService._out()` · `backend/app/mcp/service.py`.

### 15.4 Managing servers and the allowlist

- List `GET /v1/tools/servers` → `list_servers()` · `backend/app/api/v1/tools.py` → `MCPService.list_servers()` · `backend/app/mcp/service.py` → `MCPService._tools_by_server()` · `backend/app/mcp/service.py` → `_server_out()` · `backend/app/mcp/service.py` (each tool via `_tool_out()` · `backend/app/mcp/service.py`).
- One server `GET /v1/tools/servers/{name}` → `get_server()` · `backend/app/api/v1/tools.py` → `MCPService.get_server()` · `backend/app/mcp/service.py`.
- Enable/disable or describe `PATCH /v1/tools/servers/{name}` → `update_server()` · `backend/app/api/v1/tools.py` → `MCPService.update_server()` · `backend/app/mcp/service.py` (disabling → `MCPManager.disconnect()` · `backend/app/mcp/manager.py`).
- Delete `DELETE /v1/tools/servers/{name}` → `delete_server()` · `backend/app/api/v1/tools.py` → `MCPService.delete_server()` · `backend/app/mcp/service.py` → **204**.
- Switch a tool on/off `PATCH /v1/tools/servers/{name}/tools/{tool}` → `update_tool()` · `backend/app/api/v1/tools.py` → `MCPService.set_tool_enabled()` · `backend/app/mcp/service.py` → `MCPService._require_tool()` · `backend/app/mcp/service.py` → `_tool_out()` · `backend/app/mcp/service.py`.
- Try a tool `POST /v1/tools/servers/{name}/tools/{tool}/call` → `call_tool()` · `backend/app/api/v1/tools.py` → `MCPService.call_tool()` · `backend/app/mcp/service.py` → `MCPService._target()` · `backend/app/mcp/service.py` → `MCPManager.call_tool()` · `backend/app/mcp/manager.py` → `format_result()` · `backend/app/mcp/toolbox.py`. Works on switched-off tools.
- What chats may use `GET /v1/tools` → `list_available_tools()` · `backend/app/api/v1/tools.py` → `MCPService.available_tools()` · `backend/app/mcp/service.py`.
- Tables: `uv run alembic upgrade head` runs `upgrade()` · `backend/migrations/versions/0003_create_mcp.py`; `downgrade()` · `backend/migrations/versions/0003_create_mcp.py` drops them. `Database._tables()` · `backend/app/db/session.py` loads `app.mcp.models` so tests create them too.

### 15.5 Chat with tools — `POST /v1/chat` and `/v1/chat/stream` with `tools`

1. `get_chat_service()` · `backend/app/api/deps.py` also gets `get_mcp_service()` · `backend/app/api/deps.py`, and passes `MCP_MAX_TOOL_ROUNDS` and the session's `close` as `release_db`.
2. `ChatService._prepare()` · `backend/app/services/chat_service.py`
   1. Provider, system prompt, sources (sections 5, 13.3, 14.4).
   2. `ChatService._toolbox()` · `backend/app/services/chat_service.py` — no `tools` → no tools; no database → 503.
      1. `MCPService.toolbox_for_chat()` · `backend/app/mcp/service.py` — named servers (unknown/disabled → 404), their **enabled** tools only (`MCPService._tools_by_server()` · `backend/app/mcp/service.py`), each named by `model_tool_name()` · `backend/app/mcp/toolbox.py` with a schema from `tool_schema()` · `backend/app/mcp/toolbox.py`, targets from `MCPService._target()` · `backend/app/mcp/service.py`.
      2. → `Toolbox.__init__()` · `backend/app/mcp/toolbox.py`.
   3. `release_db` closes the database session.
   4. `_to_llm_request()` · `backend/app/services/chat_service.py` sets `tools` from `Toolbox.specs()` · `backend/app/mcp/toolbox.py`.
3. The loop, in `ChatService.complete()` · `backend/app/services/chat_service.py` (and the same in `ChatService.stream()` · `backend/app/services/chat_service.py`):
   1. The adapter's `complete`/`stream` (15.6) → maybe `tool_calls`.
   2. `_add_usage()` · `backend/app/services/chat_service.py` adds the tokens.
   3. `ChatService._wants_tools()` · `backend/app/services/chat_service.py` — calls asked for, tools offered, under the round limit?
   4. Stream only: one `tool_call` event per call, built by `Toolbox.trace_for()` · `backend/app/mcp/toolbox.py`.
   5. `Toolbox.run()` · `backend/app/mcp/toolbox.py` — all calls at once, each through `Toolbox._run_one()` · `backend/app/mcp/toolbox.py`:
      1. Unknown name → error result.
      2. `MCPManager.call_tool()` · `backend/app/mcp/manager.py` (`retry_safe` = not destructive).
      3. `format_result()` · `backend/app/mcp/toolbox.py` — text for the model, cut to `MCP_MAX_RESULT_CHARS`.
      4. Connection errors → error result. Logs `tool call`.
   6. Stream only: one `tool_result` event per call.
   7. `_turn()` · `backend/app/services/chat_service.py` → `LLMRequest.with_turn()` · `backend/app/llm/base.py` → back to step 1.
4. Done: `ChatService._log()` · `backend/app/services/chat_service.py` logs the totals; the response carries `tool_calls` (the trace).
5. `Toolbox.servers()` · `backend/app/mcp/toolbox.py` and `Toolbox.__len__()` · `backend/app/mcp/toolbox.py` describe a toolbox (servers, count).

### 15.6 Tools inside the provider adapters

Each adapter's `_params`/`_payload` adds `tools` and replays `turns`; `complete`/`stream` read calls back (section 7):

1. Anthropic: `_tool_calls()` · `backend/app/llm/providers/anthropic_provider.py` (only on `stop_reason: "tool_use"`); replayed content goes through `_replayable()` · `backend/app/llm/providers/anthropic_provider.py`.
2. OpenAI: `_tool_calls()` · `backend/app/llm/providers/openai_provider.py` (streamed pieces joined by index); the turn to replay from `_assistant_message()` · `backend/app/llm/providers/openai_provider.py`.
3. Gemini: `_tool_calls()` · `backend/app/llm/providers/gemini_provider.py`; the model's `Content` is replayed as is.
4. Ollama: `_tool_calls()` · `backend/app/llm/providers/ollama_provider.py`; the turn from `_assistant_message()` · `backend/app/llm/providers/ollama_provider.py`.
5. Mock: `MockProvider._tool_call()` · `backend/app/llm/providers/mock_provider.py` turns `/tool name {json}` into a call; after the round, `MockProvider._reply()` · `backend/app/llm/providers/mock_provider.py` lists the results.

### 15.7 The demo MCP server (`uv run python -m app.mcp.demo_server [--http PORT]`)

`main()` · `backend/app/mcp/demo_server.py` picks stdio or HTTP. Tools:
`get_time()` · `backend/app/mcp/demo_server.py`,
`calculate()` · `backend/app/mcp/demo_server.py` (→ `_evaluate()` · `backend/app/mcp/demo_server.py`, safe arithmetic only),
`lookup_order()` · `backend/app/mcp/demo_server.py`,
`cancel_order()` · `backend/app/mcp/demo_server.py` (destructive).

### 15.8 Frontend: the Tools page (`/tools`) and tools in Chat

1. `Tools()` · `frontend/app/tools/page.tsx` → `ToolsPage()` · `frontend/components/tools/tools-page.tsx`
   1. Load effect → `listMcpServers()` · `frontend/lib/api.ts` (with the admin key); errors through `toolsError()` · `frontend/components/tools/messages.ts`; `reload()` · `frontend/components/tools/tools-page.tsx` after changes.
   2. **Add server** → `ServerForm()` · `frontend/components/tools/server-form.tsx`
      1. **Use demo server** → `fillDemo()` · `frontend/components/tools/server-form.tsx` → `listMcpPresets()` · `frontend/lib/api.ts`.
      2. **Add and connect** → `submit()` · `frontend/components/tools/server-form.tsx` → `parsePairs()` · `frontend/components/tools/messages.ts` (env/header lines) → `createMcpServer()` · `frontend/lib/api.ts`.
   3. Selecting a server → `ServerPanel()` · `frontend/components/tools/server-panel.tsx`
      1. Every change goes through `run()` · `frontend/components/tools/server-panel.tsx`.
      2. Refresh → `refreshMcpServer()` · `frontend/lib/api.ts`; Disable/Enable → `updateMcpServer()` · `frontend/lib/api.ts`; Delete → `remove()` · `frontend/components/tools/server-panel.tsx` → `deleteMcpServer()` · `frontend/lib/api.ts`.
      3. **Allow** checkbox → `toggleTool()` · `frontend/components/tools/server-panel.tsx` (asks first for destructive tools) → `setMcpToolEnabled()` · `frontend/lib/api.ts`.
      4. **Try** → `TryTool()` · `frontend/components/tools/server-panel.tsx` (arguments from `exampleArguments()` · `frontend/components/tools/server-panel.tsx`) → `runTool()` · `frontend/components/tools/server-panel.tsx` → `callMcpTool()` · `frontend/lib/api.ts`.
   4. Server URLs are built by `serverPath()` · `frontend/lib/api.ts`; one server can be fetched with `getMcpServer()` · `frontend/lib/api.ts`.
2. Chat: the load-tools effect in `Chat()` · `frontend/components/chat.tsx` calls `listAvailableTools()` · `frontend/lib/api.ts` and shows the **Tools** picker; `send()` · `frontend/components/chat.tsx` adds `tools: {servers}`; `tool_call` / `tool_result` events fill the list drawn by `ToolTrace()` · `frontend/components/chat.tsx` above the reply.

### 15.9 Tool tests

Real MCP servers over stdio (the demo server and `backend/tests/mcp_test_server.py`) and over HTTP.

- Test server tools (`backend/tests/mcp_test_server.py`): `echo()` · `backend/tests/mcp_test_server.py`, `pid()` · `backend/tests/mcp_test_server.py`, `env()` · `backend/tests/mcp_test_server.py`, `slow()` · `backend/tests/mcp_test_server.py`, `crash()` · `backend/tests/mcp_test_server.py`.
- Connections, toolbox, secrets (`backend/tests/test_mcp.py`): fixtures `manager()` · `backend/tests/test_mcp.py` and `http_demo_url()` · `backend/tests/test_mcp.py`; helpers `text()` · `backend/tests/test_mcp.py`, `_process_exists()` · `backend/tests/test_mcp.py`, `entry()` · `backend/tests/test_mcp.py`, and a fake manager `FakeManager.__init__()` · `backend/tests/test_mcp.py` / `FakeManager.call_tool()` · `backend/tests/test_mcp.py`;
  `test_lists_tools_with_annotations()` · `backend/tests/test_mcp.py`,
  `test_calls_tools_and_reports_tool_errors()` · `backend/tests/test_mcp.py`,
  `test_parallel_calls_share_one_connection()` · `backend/tests/test_mcp.py`,
  `test_reconnects_after_the_server_dies()` · `backend/tests/test_mcp.py`,
  `test_call_timeout()` · `backend/tests/test_mcp.py`,
  `test_changed_settings_open_a_new_connection()` · `backend/tests/test_mcp.py`,
  `test_connection_failures_are_clear()` · `backend/tests/test_mcp.py`,
  `test_stdio_can_be_switched_off()` · `backend/tests/test_mcp.py`,
  `test_close_stops_the_server_process()` · `backend/tests/test_mcp.py`,
  `test_http_server()` · `backend/tests/test_mcp.py`,
  `test_toolbox_runs_calls_and_traces_them()` · `backend/tests/test_mcp.py`,
  `test_toolbox_turns_connection_errors_into_tool_errors()` · `backend/tests/test_mcp.py`,
  `test_format_result_handles_every_content_type()` · `backend/tests/test_mcp.py`,
  `test_model_tool_names_are_safe_and_unique()` · `backend/tests/test_mcp.py`,
  `test_tool_schema_defaults_to_an_object()` · `backend/tests/test_mcp.py`,
  `test_secrets_round_trip_and_need_a_key()` · `backend/tests/test_mcp.py`
- Adapters (`backend/tests/test_tool_calling.py`): helpers `request()` · `backend/tests/test_tool_calling.py`, `follow_up()` · `backend/tests/test_tool_calling.py`, `claude()` · `backend/tests/test_tool_calling.py`, `openai()` · `backend/tests/test_tool_calling.py`, `completion()` · `backend/tests/test_tool_calling.py`, `chunk()` · `backend/tests/test_tool_calling.py`, `gemini()` · `backend/tests/test_tool_calling.py`, `gemini_call()` · `backend/tests/test_tool_calling.py`, `ollama()` · `backend/tests/test_tool_calling.py` (with its inner `record()` · `backend/tests/test_tool_calling.py`);
  `test_anthropic_tool_round_trip()` · `backend/tests/test_tool_calling.py`,
  `test_anthropic_no_tool_calls_when_cut_off()` · `backend/tests/test_tool_calling.py`,
  `test_anthropic_stream_collects_tool_call()` · `backend/tests/test_tool_calling.py`,
  `test_openai_tool_round_trip()` · `backend/tests/test_tool_calling.py`,
  `test_openai_stream_assembles_tool_call_pieces()` · `backend/tests/test_tool_calling.py`,
  `test_gemini_tool_round_trip()` · `backend/tests/test_tool_calling.py`,
  `test_gemini_stream_returns_tool_call_with_id()` · `backend/tests/test_tool_calling.py`,
  `test_ollama_tool_round_trip()` · `backend/tests/test_tool_calling.py`,
  `test_ollama_stream_collects_tool_calls()` · `backend/tests/test_tool_calling.py`,
  `test_mock_tool_command_matches_short_name_and_reports_results()` · `backend/tests/test_tool_calling.py`,
  `test_mock_ignores_tool_command_without_tools()` · `backend/tests/test_tool_calling.py`
- End to end (`backend/tests/test_tools_api.py`): helpers `chat_body()` · `backend/tests/test_tools_api.py`, `enable()` · `backend/tests/test_tools_api.py`, `tool()` · `backend/tests/test_tools_api.py`; a fake manager `ListingManager.__init__()` · `backend/tests/test_tools_api.py`, `ListingManager.list_tools()` · `backend/tests/test_tools_api.py`, `ListingManager.disconnect()` · `backend/tests/test_tools_api.py`; a fake model that always calls tools `AlwaysCallsTools.__init__()` · `backend/tests/test_tools_api.py`, `AlwaysCallsTools.complete()` · `backend/tests/test_tools_api.py`, `AlwaysCallsTools.stream()` · `backend/tests/test_tools_api.py`;
  `test_register_server_lists_tools_switched_off()` · `backend/tests/test_tools_api.py`,
  `test_chat_runs_an_enabled_tool()` · `backend/tests/test_tools_api.py`,
  `test_disabled_and_destructive_tools_are_not_offered()` · `backend/tests/test_tools_api.py`,
  `test_tool_errors_are_returned_to_the_model()` · `backend/tests/test_tools_api.py`,
  `test_stream_reports_tool_calls_and_results()` · `backend/tests/test_tools_api.py`,
  `test_chat_names_unknown_or_disabled_server()` · `backend/tests/test_tools_api.py`,
  `test_tools_need_a_database()` · `backend/tests/test_tools_api.py`,
  `test_secrets_are_encrypted_hidden_and_reach_the_server()` · `backend/tests/test_tools_api.py`,
  `test_secret_needs_secret_key()` · `backend/tests/test_tools_api.py`,
  `test_stdio_servers_can_be_switched_off()` · `backend/tests/test_tools_api.py`,
  `test_unreachable_server_is_saved_with_its_error()` · `backend/tests/test_tools_api.py`,
  `test_invalid_server_definitions_are_rejected()` · `backend/tests/test_tools_api.py`,
  `test_admin_key_protects_server_management()` · `backend/tests/test_tools_api.py`,
  `test_demo_preset_can_be_registered_as_is()` · `backend/tests/test_tools_api.py`,
  `test_try_a_tool_and_delete_a_server()` · `backend/tests/test_tools_api.py`,
  `test_refresh_keeps_choices_and_rechecks_tools()` · `backend/tests/test_tools_api.py`,
  `test_tool_rounds_are_limited()` · `backend/tests/test_tools_api.py`,
  `test_manager_is_created_and_closed_with_the_app()` · `backend/tests/test_tools_api.py`
