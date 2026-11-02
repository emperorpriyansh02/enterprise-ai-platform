import pytest

from app.core.config import Settings
from app.core.errors import ModelNotFoundError
from app.llm.registry import ProviderRegistry
from tests.conftest import make_settings


def test_only_configured_providers_are_enabled():
    registry = ProviderRegistry.from_settings(make_settings())
    assert registry.provider_names == ["mock"]

    registry = ProviderRegistry.from_settings(
        make_settings(
            anthropic_api_key="a",
            openai_api_key="o",
            gemini_api_key="g",
            ollama_base_url="http://localhost:11434",
            enable_mock_provider=False,
        )
    )
    assert registry.provider_names == ["anthropic", "openai", "gemini", "ollama"]


def test_csv_lists_and_blank_values_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODELS", "gpt-a, gpt-b")
    monkeypatch.setenv("ANTHROPIC_EFFORT", "")
    settings = Settings(_env_file=None)
    assert settings.openai_models == ["gpt-a", "gpt-b"]
    assert settings.anthropic_effort is None


def test_resolve():
    registry = ProviderRegistry.from_settings(make_settings())
    provider, model = registry.resolve("mock/echo")
    assert (provider.name, model) == ("mock", "echo")
    assert registry.resolve(None)[1] == "echo"  # falls back to the default model


@pytest.mark.parametrize("ref", ["echo", "openai/gpt-5", "mock/unknown", "mock/"])
def test_resolve_rejects_unknown_or_disallowed(ref):
    registry = ProviderRegistry.from_settings(make_settings())
    with pytest.raises(ModelNotFoundError):
        registry.resolve(ref)


def test_default_model_falls_back_to_first_available():
    registry = ProviderRegistry.from_settings(make_settings(default_model="anthropic/claude-opus-5-5"))
    assert registry.default_model == "mock/echo"
