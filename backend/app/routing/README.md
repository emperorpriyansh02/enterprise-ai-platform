# Layer 7b — Router

Chooses a model per request: by policy (task type, cost budget, latency), with fallback to
another provider when one is down or rate-limited. Builds on `ProviderRegistry`.

**Hooks into:** `ChatService` — replaces the direct `registry.resolve()` call.
**Status:** not built yet.
