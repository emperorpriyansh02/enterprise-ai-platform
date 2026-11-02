"""FastAPI dependencies: shared objects that routes ask for by type."""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AdminAuthError, PromptStoreUnavailableError
from app.db.session import Database
from app.llm.registry import ProviderRegistry
from app.prompts.service import PromptService
from app.services.chat_service import ChatService


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


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


def get_chat_service(
    request: Request, prompts: Annotated[PromptService | None, Depends(get_prompt_service)]
) -> ChatService:
    return ChatService(get_registry(request), prompts)


def require_admin(request: Request, x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """Checks X-Admin-Key when ADMIN_API_KEY is set. Stand-in for RBAC (Layer 5)."""
    expected = get_settings_from_app(request).admin_api_key
    if expected is None:
        return
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected.get_secret_value()):
        raise AdminAuthError("Missing or wrong X-Admin-Key header.")
