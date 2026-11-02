"""Application settings, loaded from environment variables and an optional `.env` file.

List settings (e.g. `OPENAI_MODELS`) accept a comma-separated string:
    OPENAI_MODELS=gpt-5,gpt-5-mini
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "Enterprise AI Platform"
    environment: str = "dev"
    log_level: str = "INFO"
    cors_origins: CsvList = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # --- LLM defaults (shared by all providers) ---
    default_model: str = "anthropic/claude-opus-5-5"
    request_timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)

    # --- Anthropic ---
    anthropic_api_key: SecretStr | None = None
    anthropic_models: CsvList = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    # Reasoning effort sent as output_config.effort (low|medium|high|xhigh|max). Empty = model default.
    anthropic_effort: str | None = "medium"
    # Server-side refusal fallback: if a model's safety classifier declines, the API retries
    # on Anthropic's recommended fallback model in the same call.
    anthropic_refusal_fallback: bool = True
    anthropic_fallback_models: CsvList = [
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-sonnet-5-5",
    ]

    # --- OpenAI (base URL can point at Azure OpenAI or any OpenAI-compatible server) ---
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_models: CsvList = ["gpt-5", "gpt-5-mini"]

    # --- Google Gemini ---
    gemini_api_key: SecretStr | None = None
    gemini_models: CsvList = ["gemini-flash-latest", "gemini-pro-latest"]

    # --- Ollama (local models). Set the URL to enable, e.g. http://localhost:11434 ---
    ollama_base_url: str | None = None
    ollama_models: CsvList = ["llama3.2"]

    # --- Mock provider: echoes input back. For tests and demos without API keys. ---
    enable_mock_provider: bool = True
    mock_stream_delay_s: float = 0.03

    @field_validator(
        "cors_origins",
        "anthropic_models",
        "anthropic_fallback_models",
        "openai_models",
        "gemini_models",
        "ollama_models",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("anthropic_effort", "openai_base_url", "ollama_base_url", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
