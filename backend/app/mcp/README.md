# Layer 4 — MCP tools

Connects to MCP servers, keeps an admin allowlist of their tools, and runs the model ↔ tool loop
during a chat.

| File | What's in it |
|---|---|
| `manager.py` | `MCPManager`: one long-lived connection per server (stdio process or streamable HTTP), lazy connect, reconnect, call timeouts |
| `service.py` | `MCPService`: the server registry, refresh (list + upsert tools), the allowlist, admin "try it", chat toolboxes |
| `toolbox.py` | `Toolbox`: the tools one chat may use; runs calls concurrently, formats and cuts results; `server__tool` names |
| `secrets.py` | Fernet encryption of env values and headers with `SECRET_KEY` |
| `models.py` | `mcp_servers`, `mcp_tools` tables (migration `0003_create_mcp.py`) |
| `schemas.py` | API shapes, `ToolsRef` (the chat's `tools` field), `ToolCallTrace` |
| `demo_server.py` | A real MCP server for demos/tests: `get_time`, `calculate`, `lookup_order`, `cancel_order` (destructive) |

**Hooks into:** `ChatService._prepare` (loads the toolbox) and the loop in `ChatService.complete` /
`ChatService.stream`. Provider adapters translate tools for each vendor (`app/llm/providers/`).

**Rules:** new tools start off; destructive tools are flagged; disabled servers/tools never reach a
model; secrets are never returned; at most `MCP_MAX_TOOL_ROUNDS` rounds per message.

**Try it:** Tools page → Add server → Use demo server → switch on `get_time` → Chat → Tools → tick
`demo` → with `mock/echo`, send `/tool get_time {"timezone": "Asia/Tokyo"}` (a real model just needs
a question like "What time is it in Tokyo?").

**Layer 5** adds per-role access (`allowed_roles`) and fills `created_by`.
