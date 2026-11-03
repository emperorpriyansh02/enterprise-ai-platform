"""FastAPI dependencies: shared objects that routes ask for by type.

Auth (Layer 5): `get_principal` says who is calling (verified token, or the dev user when auth is
off); `require(permission)` stops the request with 403 unless the caller's roles allow it. FastAPI
runs each dependency once per request, so the token is checked once even if several need it.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import CHAT
from app.auth.principal import Principal, dev_principal
from app.auth.tokens import TokenVerifier
from app.core.config import Settings
from app.core.errors import (
    AuthRequiredError,
    ForbiddenError,
    PromptStoreUnavailableError,
    ToolsUnavailableError,
)
from app.db.session import Database
from app.llm.registry import ProviderRegistry
from app.mcp.manager import MCPManager
from app.mcp.service import MCPService
from app.prompts.service import PromptService
from app.rag.runtime import RagRuntime
from app.rag.service import RagService
from app.services.chat_service import ChatService


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


async def get_principal(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
    """The caller. Auth off → the dev user. Auth on → the user in the Bearer token (401 if missing/bad)."""
    settings = get_settings_from_app(request)
    if not settings.auth_enabled:
        principal = dev_principal(settings.dev_user_roles)
    else:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthRequiredError("Sign in first: send `Authorization: Bearer <access token>`.")
        verifier: TokenVerifier = request.app.state.auth
        principal = await verifier.verify(token.strip())
    request.state.user = principal.username  # for the request log line
    return principal


CurrentUser = Annotated[Principal, Depends(get_principal)]


def require(permission: str) -> Callable[..., Awaitable[Principal]]:
    """A dependency that lets the request through only if the caller has `permission`."""

    async def check(principal: CurrentUser) -> Principal:
        if not principal.can(permission):
            raise ForbiddenError(f"Your roles ({_roles(principal)}) don't allow this ({permission}).")
        return principal

    return check


def _roles(principal: Principal) -> str:
    return ", ".join(sorted(principal.roles)) or "none"


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession | None]:
    """One database session per request, closed afterwards. None if no database is configured."""
    database: Database | None = request.app.state.database
    if database is None:
        yield None
        return
    async with database.session() as session:
        yield session


def get_prompt_service(
    request: Request, session: Annotated[AsyncSession | None, Depends(get_db_session)]
) -> PromptService | None:
    if session is None:
        return None
    return PromptService(session, default_prompt_name=get_settings_from_app(request).default_prompt_name)


def require_prompt_service(
    service: Annotated[PromptService | None, Depends(get_prompt_service)],
) -> PromptService:
    if service is None:
        raise PromptStoreUnavailableError("Prompt store is not configured. Set DATABASE_URL.")
    return service


def get_rag_service(
    request: Request,
    session: Annotated[AsyncSession | None, Depends(get_db_session)],
    principal: CurrentUser,
) -> RagService | None:
    """A RagService for this request (seeing only collections the caller may use), or None."""
    runtime: RagRuntime = request.app.state.rag
    if session is None or runtime.embedder is None:
        return None
    return RagService(session, runtime, principal=principal)


async def require_rag_service(
    request: Request, service: Annotated[RagService | None, Depends(get_rag_service)]
) -> RagService:
    """Stops the request with 503 `rag_unavailable` (and the reason) unless knowledge bases work."""
    await request.app.state.rag.check()
    assert service is not None  # check() passed, so there is a database and an embedder
    return service


def get_mcp_service(
    request: Request,
    session: Annotated[AsyncSession | None, Depends(get_db_session)],
    principal: CurrentUser,
) -> MCPService | None:
    """An MCPService for this request (seeing only servers the caller may use), or None without a database."""
    if session is None:
        return None
    manager: MCPManager = request.app.state.mcp
    return MCPService(session, manager, get_settings_from_app(request), principal=principal)


def require_mcp_service(service: Annotated[MCPService | None, Depends(get_mcp_service)]) -> MCPService:
    if service is None:
        raise ToolsUnavailableError("MCP tools need a database for the server registry. Set DATABASE_URL.")
    return service


def get_chat_service(
    request: Request,
    session: Annotated[AsyncSession | None, Depends(get_db_session)],
    prompts: Annotated[PromptService | None, Depends(get_prompt_service)],
    rag: Annotated[RagService | None, Depends(get_rag_service)],
    tools: Annotated[MCPService | None, Depends(get_mcp_service)],
    principal: Annotated[Principal, Depends(require(CHAT))],
) -> ChatService:
    settings = get_settings_from_app(request)
    return ChatService(
        get_registry(request),
        prompts,
        rag,
        request.app.state.rag,
        tools,
        max_tool_rounds=settings.mcp_max_tool_rounds,
        release_db=session.close if session is not None else None,
        principal=principal,
        model_roles=settings.model_roles,
    )
