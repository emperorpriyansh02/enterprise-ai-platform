"""Roles, the permissions each role gives, and the per-resource access rules.

Roles come from the identity provider (Keycloak realm roles). Permissions are fixed here in code, so
what a role can do is reviewed like any other code change.

    eap-user    chat, read prompts and knowledge bases, use tools
    eap-editor  everything a user can, plus change prompts and knowledge bases
    eap-admin   everything, including MCP servers and guardrail policies; passes every per-resource rule

Per-resource rules: a collection or MCP server may list `allowed_roles`; empty means everyone.
Models may be limited with the MODEL_ROLES setting; models not listed are open to every role.
"""

from collections.abc import Iterable
from typing import Literal

ADMIN = "eap-admin"
EDITOR = "eap-editor"
USER = "eap-user"
ROLES: tuple[str, ...] = (ADMIN, EDITOR, USER)
RoleName = Literal["eap-admin", "eap-editor", "eap-user"]  # for API fields that take role names

CHAT = "chat:use"
PROMPTS_READ = "prompts:read"
PROMPTS_WRITE = "prompts:write"
KNOWLEDGE_READ = "knowledge:read"
KNOWLEDGE_WRITE = "knowledge:write"
TOOLS_USE = "tools:use"
TOOLS_ADMIN = "tools:admin"
GUARDRAILS_ADMIN = "guardrails:admin"

_USER = frozenset({CHAT, PROMPTS_READ, KNOWLEDGE_READ, TOOLS_USE})
_EDITOR = _USER | {PROMPTS_WRITE, KNOWLEDGE_WRITE}
_ADMIN = _EDITOR | {TOOLS_ADMIN, GUARDRAILS_ADMIN}
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {USER: _USER, EDITOR: _EDITOR, ADMIN: _ADMIN}


def permissions_for(roles: Iterable[str]) -> frozenset[str]:
    """Everything the given roles allow together. Unknown roles add nothing."""
    allowed: set[str] = set()
    for role in roles:
        allowed |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(allowed)


def roles_allow(user_roles: Iterable[str], allowed_roles: Iterable[str] | None) -> bool:
    """A per-resource rule: empty `allowed_roles` = everyone; admins always pass."""
    allowed = set(allowed_roles or [])
    roles = set(user_roles)
    return not allowed or ADMIN in roles or bool(roles & allowed)
