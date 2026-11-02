# Layer 2: System prompts

Managed, versioned system prompts stored in Postgres.

- A **prompt** has a unique name (`support-agent`), a description, and numbered **versions**.
- Versions are never edited. A change creates a new version, so every chat reply can be traced to
  the exact text it used (`prompt: {name, version}` on each reply).
- One version is **active**. Activating an older version is a rollback.
- Templates use `{{ variable }}` placeholders, filled from the chat request. A missing value is a
  400 `prompt_variable_missing` error.
- **Archived** prompts are hidden and can't be used or changed. Their versions are kept for audit.

## Files

| File | What's in it |
|---|---|
| `models.py` | Database tables: `prompts`, `prompt_versions` |
| `repository.py` | Every SQL query (no business rules) |
| `service.py` | Rules: versioning, activation, archive, rendering for chat |
| `template.py` | The `{{ variable }}` renderer |
| `schemas.py` | API request and response shapes |
| `seed.py` | Adds two example prompts: `uv run python -m app.prompts.seed` |

The routes are in `app/api/v1/prompts.py`. Chat uses prompts in `ChatService._build_system`
(`app/services/chat_service.py`).

## Using a prompt in chat

```json
POST /v1/chat
{
  "model": "anthropic/claude-opus-5-5",
  "prompt": {"name": "support-agent", "variables": {"company_name": "Acme", "support_email": "help@acme.com"}},
  "system": "Optional extra instructions, added after the prompt.",
  "messages": [{"role": "user", "content": "Where is my order?"}]
}
```

- Add `"version": 2` inside `prompt` to pin a version. Leave it out to use the active one.
- To check exactly what the model received, use the model `mock/echo-system`: it replies with the
  final system prompt.

## Admin API

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/prompts` | List prompts (`?include_archived=true` for all) |
| POST | `/v1/prompts` | Create a prompt (version 1, active) |
| GET | `/v1/prompts/{name}` | Details + all versions |
| PATCH | `/v1/prompts/{name}` | Change the description |
| DELETE | `/v1/prompts/{name}` | Archive |
| GET | `/v1/prompts/{name}/versions/{n}` | One version |
| POST | `/v1/prompts/{name}/versions` | New version (`"activate": true` to switch to it) |
| POST | `/v1/prompts/{name}/activate` | Make a version active (`{"version": 1}`) |
| POST | `/v1/prompts/{name}/render` | Preview the final text without calling an LLM |

If `ADMIN_API_KEY` is set, write requests need the header `X-Admin-Key: <value>`. This is a
stand-in until role-based access control (Layer 5).

## Setup

```bash
cd backend
# DATABASE_URL=postgresql+asyncpg://eap:eap@localhost:5432/eap in backend/.env
uv run alembic upgrade head        # create the tables
uv run python -m app.prompts.seed  # optional: example prompts
```

Without `DATABASE_URL`, chat still works and the prompt endpoints return 503 `prompt_store_unavailable`.
