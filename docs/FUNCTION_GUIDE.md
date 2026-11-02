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
13. [Debugging cheat sheet](#13-debugging-cheat-sheet)

---

## 0. Data shapes (read this first)

These are the "containers" data travels in. Functions below take and return them.

### Public API shapes (`backend/app/schemas/chat.py`)

What callers send and receive over HTTP.

| Shape | What it holds | Example |
|---|---|---|
| `Message` | One chat message: `role` (`"user"` or `"assistant"`) and `content` (non-empty text). | `{"role": "user", "content": "Hi"}` |
| `ChatRequest` | What the caller sends: `model` (optional `"provider/model"`), `messages` (at least one, first must be from the user), `system` (optional system prompt), `temperature` (0–2, optional), `max_tokens` (1–128,000, optional), `provider_options` (extra vendor settings, passed through). | `{"model": "openai/gpt-5", "messages": [...]}` |
| `Usage` | Token counts: `input_tokens`, `output_tokens`. | `{"input_tokens": 12, "output_tokens": 40}` |
| `ChatResponse` | The full reply: `id`, `provider`, `model` (the one that actually answered), `content`, `finish_reason`, `usage`, `latency_ms`. | see section 5 |
| `StreamEvent` | One streaming event: `type` (`start`/`delta`/`done`/`error`), `id`, and depending on type: `provider`, `model`, `delta` (text piece), `finish_reason`, `usage`, `latency_ms`, `error`. | `{"type": "delta", "id": "chat_…", "delta": "Hel"}` |
| `ModelInfo` | One model you may call: `id` (`"provider/model"`), `provider`, `model`. | `{"id": "mock/echo", "provider": "mock", "model": "echo"}` |
| `ModelList` | `default` (model id or null) + `data` (list of `ModelInfo`). | see section 4 |

`finish_reason` is always one of: `stop` (finished normally), `length` (hit the token limit),
`refusal` (model declined), `content_filter` (blocked by a safety filter), `other`.

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
- **What it does:**
  1. Picks the settings (given ones, or from the environment).
  2. Turns on JSON logging at the configured level.
  3. Defines `lifespan` (startup/shutdown steps, below).
  4. Creates the FastAPI app with the app name and `lifespan`.
  5. Saves the settings on `app.state.settings`.
  6. Adds CORS, so browsers on the origins in `CORS_ORIGINS` (e.g. `http://localhost:3000`) may call the API. It also lets the browser read the `X-Request-ID` header.
  7. Adds the `request_context` middleware (request id + request log).
  8. Registers the error handler, then adds the `/health` route and the `/v1` routes.
- **Gives back:** the configured `FastAPI` app object. Providers are **not** created yet; that happens when the server starts.
- **Can fail with:** a pydantic `ValidationError` from `get_settings()` if an env value has the wrong type (e.g. `REQUEST_TIMEOUT_S=abc`). The server won't start; the error names the bad field.
- **Debug tips:** settings are read when `app.main` is imported. If a change to `.env` has no effect, restart the server. `.env` is read from the folder you start uvicorn in (`backend/`).

#### `lifespan()` · `backend/app/main.py`

- **Where:** defined inside `create_app`. **Called by:** FastAPI, once at server start and once at shutdown. **Calls:** `ProviderRegistry.from_settings`, `ProviderRegistry.aclose`.
- **Takes in:** `app`, the FastAPI app (given by FastAPI). Also uses `settings` and `registry` from `create_app`.
- **What it does:**
  1. **Startup:** uses the given registry, or builds one from settings with `ProviderRegistry.from_settings()`, and stores it on `app.state.registry`.
  2. Pauses (`yield`) while the server handles requests.
  3. **Shutdown:** calls `app.state.registry.aclose()` to close all network clients.
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

- **Where:** a validator on `Settings`. **Called by:** pydantic, for `ANTHROPIC_EFFORT`, `OPENAI_BASE_URL`, `OLLAMA_BASE_URL`.
- **Takes in:** `value`, the raw value (e.g. `""` from a line like `OLLAMA_BASE_URL=` in `.env`).
- **What it does:** a blank or whitespace-only string becomes `None` ("not set"). Anything else passes through.
- **Gives back:** `""` → `None`; `"http://localhost:11434"` → unchanged.
- **Can fail with:** nothing.
- **Debug tips:** this rule is why copying `.env.example` (which has blank values) doesn't switch Ollama on with an empty URL.

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
- **What it does:** registers the single model `echo` and stores the delay.
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

- **Where:** route `GET /health`. **Called by:** FastAPI. **Calls:** `ProviderRegistry.provider_names`.
- **Takes in:** `registry`, from `get_registry`.
- **What it does:** reports that the server is up and which providers are enabled.
- **Gives back:** `{"status": "ok", "providers": ["anthropic", "mock"]}`.
- **Can fail with:** nothing in normal operation.
- **Debug tips:** used by Docker's health check. It does **not** contact any vendor, so "ok" doesn't prove your API keys work.

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

- **Where:** `backend/app/api/deps.py`. **Called by:** FastAPI, for the `chat` and `chat_stream` routes (via the `Service` dependency). **Calls:** `get_registry`, `ChatService.__init__`.
- **Takes in:** `request`, the current HTTP request.
- **What it does:** creates a new `ChatService` that uses the shared registry.
- **Gives back:** a `ChatService` object (cheap; one per request).
- **Can fail with:** same as `get_registry`.
- **Debug tips:** later layers (prompts, auth) will add their services here, so this is where request-scoped wiring lives.

#### `ChatService.__init__()` · `backend/app/services/chat_service.py`

- **Where:** `ChatService` constructor. **Called by:** `get_chat_service`.
- **Takes in:** `registry`, the `ProviderRegistry`.
- **What it does:** keeps a reference to the registry for `complete` and `stream`.
- **Gives back:** the service object.
- **Can fail with:** nothing.
- **Debug tips:** nothing to debug here; it only holds the registry.

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

- **Where:** `ChatService`. **Called by:** the `chat` route. **Calls:** `ProviderRegistry.resolve`, `_new_id`, `_to_llm_request`, the chosen adapter's `complete`, `_ms_since`.
- **Takes in:** `request`, the validated `ChatRequest` from the caller.
- **What it does:**
  1. **Picks the provider:** `registry.resolve(request.model)` turns `"openai/gpt-5"` into (OpenAI adapter, `"gpt-5"`). A missing model means the default.
  2. **Makes an id** like `chat_3f2a…` with `_new_id()`.
  3. **Starts a timer.**
  4. **Converts** the public request into an internal `LLMRequest` (`_to_llm_request`) and calls `provider.complete(...)`. This is where the vendor is contacted.
  5. **Measures** how long it took (`_ms_since`).
  6. **Logs** `chat completed` with request id, provider, model, latency, tokens and finish reason.
  7. **Builds** the `ChatResponse`. `model` comes from the vendor's answer, so after a Claude refusal fallback it shows the fallback model.
- **Gives back:**
  ```json
  {"id": "chat_3f2a…", "provider": "openai", "model": "gpt-5-2026-01-01",
   "content": "Hello!", "finish_reason": "stop",
   "usage": {"input_tokens": 9, "output_tokens": 3}, "latency_ms": 812}
  ```
- **Can fail with:** `ModelNotFoundError` (404) from `resolve`; any `Provider*Error` from the adapter (see section 8). Nothing is logged as `chat completed` when it fails.
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
- **What it does:** copies the fields into an internal `LLMRequest`, replacing `"openai/gpt-5"` with just `"gpt-5"`, the name the vendor understands.
- **Gives back:** `LLMRequest(model="gpt-5", messages=[...], system=..., temperature=..., max_tokens=..., provider_options={...})`.
- **Can fail with:** nothing.
- **Debug tips:** Layer 2 (prompts) will change `system` at this point. If the system prompt the model sees is wrong, look here.

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

- **Where:** `ChatService`. **Called by:** `chat_stream`. **Calls:** `ProviderRegistry.resolve`, `_new_id`, `_to_llm_request`, the adapter's `stream`, `_ms_since`, `AppError.to_dict`.
- **Takes in:** `request`, the validated `ChatRequest`.
- **What it does:**
  1. Picks the provider (`resolve`), makes an id, starts a timer, converts the request.
  2. Starts the adapter's stream and **waits for its first chunk**. Errors here are *raised* (not turned into events), so the route can still return an HTTP error. An adapter that yields nothing at all raises `ProviderError("Provider returned an empty stream")`.
  3. Yields `start` (id, provider, model).
  4. Loops over the adapter's chunks:
     - a text chunk → yields `delta` with the text;
     - the final chunk (`done=True`) → measures latency, logs `chat stream completed`, yields `done` with finish reason, usage and latency, then stops.
  5. If the adapter raises an `AppError` mid-stream, or ends without a final chunk → logs `chat stream failed` (with `error_code`), yields one `error` event with `{code, message, provider}`, and stops.
- **Gives back:** a sequence of `StreamEvent`s: `start`, `delta`…, then exactly one `done` **or** `error`. All share the same `id`.
- **Can fail with:** before `start`: `ModelNotFoundError` or any `Provider*Error` (raised). After `start`: never raises; problems become the `error` event.
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
- **What it does:** finds the **last** user message and puts `"Echo: "` in front of it.
- **Gives back:** e.g. `"Echo: hello world"`.
- **Can fail with:** nothing (validation guarantees there's at least one user message).
- **Debug tips:** the system prompt is ignored by this model.

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
  2. Copies the known extra fields if present: `request_id`, `method`, `path`, `status`, `latency_ms`, `provider`, `model`, `input_tokens`, `output_tokens`, `finish_reason`, `error_code`.
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

(Each adapter's own `aclose` is described in section 7.)

---

## 11. Frontend

### 11.1 Page load

#### `RootLayout()` · `frontend/app/layout.tsx`

- **Where:** Next.js root layout. **Called by:** Next.js, for every page.
- **Takes in:** `children`, the page content (here, `Home`).
- **What it does:** renders `<html>` and `<body>`, loads the Geist fonts as CSS variables, sets the page title "Enterprise AI Platform".
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
  - `busy`: a reply is in progress.
- **What it does:**
  1. **Load-models effect** (runs once, after the first render):
     1. Calls `getModels()`.
     2. Success → fills the dropdown and selects the server's default, or the first model.
     3. Failure → shows the server's error message, or "Can't reach the backend. Is it running on port 8000?" for network/CORS failures.
     4. If the page closes mid-request, the request is cancelled and the cancel error is ignored.
  2. **Auto-scroll effect** (runs whenever `turns` changes): scrolls the newest message into view.
  3. Renders the header (model dropdown, "System prompt", "New chat"), the optional system prompt box, an error banner, the message list (user bubbles on the right, assistant replies on the left), and the composer.
  4. Under each finished reply: `provider/model · N in / N out · 1.23s`, plus a note for `length`, `refusal`, `content_filter` or "Stopped."
- **Gives back:** the chat screen (JSX).
- **Can fail with:** nothing thrown; errors are shown on screen.
- **Debug tips:**
  - Open the browser console. A CORS error means the page's origin isn't in the backend's `CORS_ORIGINS`.
  - The Network tab shows `/v1/chat/stream` arriving piece by piece.

#### `getModels()` · `frontend/lib/api.ts`

- **Where:** API client. **Called by:** the load-models effect in `Chat`. **Calls:** `toApiError` on failure.
- **Takes in:** `signal` (optional), used to cancel the request.
- **What it does:** sends `GET {API_URL}/v1/models`. `API_URL` comes from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`), fixed at build time.
- **Gives back:** the `ModelList` JSON: `{default, data: [...]}`.
- **Can fail with:**
  - an `ApiError` for HTTP errors;
  - a `TypeError` ("Failed to fetch") for network or CORS problems;
  - an `AbortError` when cancelled.
- **Debug tips:** after changing `NEXT_PUBLIC_API_URL`, rebuild or restart the frontend; the value is baked in.

#### `toApiError()` · `frontend/lib/api.ts`

- **Where:** API client (internal). **Called by:** `getModels`, `streamChat`.
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
- **Takes in:** nothing directly; reads `input`, `busy`, `model`, `system` and `turns` from the component's state.
- **What it does:**
  1. Trims the input. Stops early if it's empty, a reply is already running, or no model is selected.
  2. **Builds the history:** all earlier turns that have text and no error, as `{role, content}`, plus the new user message. The backend is stateless, so the whole conversation is sent each time.
  3. Adds two turns to the screen: the user's message and an empty assistant turn marked "streaming" (shows "Thinking…").
  4. Clears the input and marks the chat busy (the Send button becomes Stop).
  5. Creates an `AbortController` so Stop can cancel.
  6. Calls `streamChat` with `{model, messages, system}`. `system` is sent only if the box isn't blank. For each event:
     - `start` → saves provider and model on the assistant turn;
     - `delta` → appends the text, so the reply grows on screen;
     - `done` → saves usage, latency, finish reason, and marks it finished;
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
- **What it does:** builds `Settings` that **ignore your real `.env` and keys**: all API keys off, Ollama off, mock on with no delay, default model `mock/echo`. Then applies the overrides.
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

- **Proves:** `/health` returns `ok` with `["mock"]` and an `X-Request-ID` header.

#### `test_models()` · `backend/tests/test_api.py`

- **Proves:** `/v1/models` returns the mock model as default and in the list.

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
  The regex finds named functions, arrow functions assigned to a `const`, and classes.
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

## 13. Debugging cheat sheet

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
