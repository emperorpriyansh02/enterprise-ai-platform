# Layer 7a — Cache

Exact-match response cache (Redis), optional semantic cache (pgvector), and use of provider
prompt caching for long, stable system prompts.

**Hooks into:** `ChatService`, before the router — a hit skips the LLM call.
**Status:** not built yet.
