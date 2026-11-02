# Layer 6 — Guardrails

Input checks (prompt injection, PII, topic policy) and output checks (PII leakage, policy,
schema validation). Each check can block, redact, or flag.

**Hooks into:** `ChatService`, before and after the LLM call (and on streamed output).
**Status:** not built yet.
