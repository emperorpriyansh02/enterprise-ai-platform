# Layer 2 — System prompts

Versioned prompt templates (stored in Postgres, cached in memory), rendered with variables per
request. Each response records the prompt ID and version used, so evals can compare versions.

**Hooks into:** `ChatService`, before the LLM call — sets `LLMRequest.system`.
**Status:** not built yet.
