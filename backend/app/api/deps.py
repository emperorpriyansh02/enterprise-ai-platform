from fastapi import Request

from app.llm.registry import ProviderRegistry
from app.services.chat_service import ChatService


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def get_chat_service(request: Request) -> ChatService:
    return ChatService(get_registry(request))
