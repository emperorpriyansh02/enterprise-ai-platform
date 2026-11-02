# Layer 4 — MCP tools

An MCP client that connects to approved MCP servers, lists their tools, and runs the
model ↔ tool loop. Tool access is allowlisted per role.

**Hooks into:** `ChatService` — wraps the LLM call in a tool-use loop.
**Status:** not built yet.
