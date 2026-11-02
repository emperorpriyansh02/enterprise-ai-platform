# Layer 8 — Observability

OpenTelemetry traces (request → guardrails → retrieval → LLM → tools), metrics (latency,
tokens, cost per user/team/model), and structured logs. Builds on `app/core/logging.py`.

**Hooks into:** middleware plus spans around every step in `ChatService`.
**Status:** not built yet (basic JSON request logs exist today).
