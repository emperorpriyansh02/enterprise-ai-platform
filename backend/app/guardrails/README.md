# Layer 6 — Guardrails (backend done; UI and code guides still to do)

Policies check text at three stages: **input** (user messages), **context** (retrieved passages and
tool results) and **output** (the reply, also while streaming). Each policy's action is
`block`, `redact` (mask spans such as `[EMAIL]`; for context, remove the item) or `log`.

| File | What's in it |
|---|---|
| `pii.py` | Emails, phones, cards (Luhn), IBAN (mod-97), SSN, Aadhaar (Verhoeff), PAN, IPs, API keys/secrets |
| `rules.py` | Prompt-injection patterns (normalized text, hidden characters), harmful phrases, topic keywords/regex |
| `judge.py` | Optional LLM judge (`GUARDRAIL_JUDGE_MODEL`), batched, treats checked text as data only |
| `engine.py` | Applies policies per stage; `OutputStreamGuard` masks streamed text with a short hold-back |
| `runtime.py` | Policy cache, built-in seeding, background event writer |
| `service.py` | Admin CRUD, dry-run test, event log |
| `defaults.py` | Built-ins: `pii` (redact), `prompt-injection` (block), `indirect-injection` (remove), `harmful-content` (block) |

API (admins, `guardrails:admin`): `GET/POST /v1/guardrails/policies`, `GET/PATCH/DELETE /v1/guardrails/policies/{name}`,
`POST /v1/guardrails/test`, `GET /v1/guardrails/events`. Chat replies carry `guardrails` traces; streams send
`guardrail` events; a block gives `finish_reason: "content_filter"` with the policy's message.
