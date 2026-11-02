"""Builds the configured providers and resolves "provider/model" strings to an adapter.

Only providers with credentials (or a URL, for Ollama) are registered, and only models listed in
config are allowed. That allowlist is the first governance control: callers can't reach a model
the platform team hasn't approved.
"""

import logging

from app.core.config import Settings
from app.core.errors import ModelNotFoundError
from app.llm.base import LLMProvider
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.providers.mock_provider import MockProvider
from app.llm.providers.ollama_provider import OllamaProvider
from app.llm.providers.openai_provider import OpenAIProvider
from app.schemas.chat import ModelInfo

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, providers: list[LLMProvider], default_model: str | None = None) -> None:
        self._providers = {p.name: p for p in providers}
        self._default_model = default_model

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderRegistry":
        common = {"timeout_s": settings.request_timeout_s, "max_retries": settings.max_retries}
        providers: list[LLMProvider] = []
        if settings.anthropic_api_key:
            providers.append(
                AnthropicProvider(
                    api_key=settings.anthropic_api_key.get_secret_value(),
                    models=settings.anthropic_models,
                    effort=settings.anthropic_effort,
                    refusal_fallback=settings.anthropic_refusal_fallback,
                    fallback_models=settings.anthropic_fallback_models,
                    **common,
                )
            )
        if settings.openai_api_key:
            providers.append(
                OpenAIProvider(
                    api_key=settings.openai_api_key.get_secret_value(),
                    models=settings.openai_models,
                    base_url=settings.openai_base_url,
                    **common,
                )
            )
        if settings.gemini_api_key:
            providers.append(
                GeminiProvider(
                    api_key=settings.gemini_api_key.get_secret_value(),
                    models=settings.gemini_models,
                    **common,
                )
            )
        if settings.ollama_base_url:
            providers.append(
                OllamaProvider(
                    base_url=settings.ollama_base_url,
                    models=settings.ollama_models,
                    timeout_s=settings.request_timeout_s,
                )
            )
        if settings.enable_mock_provider:
            providers.append(MockProvider(stream_delay_s=settings.mock_stream_delay_s))

        logger.info("providers enabled: %s", ", ".join(p.name for p in providers) or "none")
        return cls(providers, default_model=settings.default_model)

    @property
    def provider_names(self) -> list[str]:
        return list(self._providers)

    def catalog(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=f"{p.name}/{m}", provider=p.name, model=m)
            for p in self._providers.values()
            for m in p.models
        ]

    @property
    def default_model(self) -> str | None:
        """The configured default if it is available, otherwise the first available model."""
        ids = [m.id for m in self.catalog()]
        if self._default_model in ids:
            return self._default_model
        return ids[0] if ids else None

    def resolve(self, model_ref: str | None) -> tuple[LLMProvider, str]:
        ref = model_ref or self.default_model
        if not ref:
            raise ModelNotFoundError("No LLM providers are configured. Set an API key in .env.")
        provider_name, sep, model = ref.partition("/")
        if not sep or not model:
            raise ModelNotFoundError(f'Model must look like "provider/model", got "{ref}".')
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ModelNotFoundError(
                f'Provider "{provider_name}" is not configured. '
                f"Available: {', '.join(self._providers) or 'none'}."
            )
        if model not in provider.models:
            raise ModelNotFoundError(
                f'Model "{model}" is not enabled for {provider_name}. Enabled: {", ".join(provider.models)}.'
            )
        return provider, model

    async def aclose(self) -> None:
        for provider in self._providers.values():
            try:
                await provider.aclose()
            except Exception:  # shutdown must not fail because one client did
                logger.exception("failed to close provider %s", provider.name)
