"""Application settings, loaded from environment variables and an optional `.env` file.

List settings (e.g. `OPENAI_MODELS`) accept a comma-separated string:
    OPENAI_MODELS=gpt-5,gpt-5-mini
"""

from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # --- Database (Layer 2 prompts; later RAG and RBAC). Blank = prompt features off. ---
    # Example: postgresql+asyncpg://eap:eap@localhost:5432/eap
    database_url: str | None = None

    # --- Prompts (Layer 2) ---
    # Prompt used when a chat request has neither `prompt` nor `system`. Blank = none.
    default_prompt_name: str | None = None

    # --- Knowledge bases / RAG (Layer 3). Needs DATABASE_URL on Postgres with pgvector. ---
    # openai | gemini | ollama | mock. Blank = first available, in that order.
    embedding_provider: str | None = None
    # Blank = the provider's default (see app/rag/embeddings.py). Vectors are always 768 numbers.
    embedding_model: str | None = None
    rag_chunk_size: int = Field(default=1200, ge=200, description="Characters per chunk.")
    rag_chunk_overlap: int = Field(default=200, ge=0, description="Characters repeated between chunks.")
    rag_top_k: int = Field(default=5, ge=1, le=20, description="Passages added to a chat by default.")
    rag_candidates: int = Field(
        default=20, ge=1, le=200, description="Matches taken from each search method."
    )
    rag_max_upload_mb: float = Field(default=20, gt=0)
    rag_ingest_concurrency: int = Field(default=2, ge=1, le=16)

    # --- MCP tools (Layer 4). Needs DATABASE_URL (the server registry is stored there). ---
    # Encrypts MCP secrets (env values, HTTP headers) at rest. Required to save any secret.
    secret_key: SecretStr | None = None
    # Allow MCP servers that the backend starts as a local command. Turn off where that's not acceptable.
    mcp_allow_stdio: bool = True
    mcp_max_tool_rounds: int = Field(default=5, ge=1, le=20, description="Model ↔ tool rounds per message.")
    mcp_call_timeout_s: float = Field(default=30, gt=0)
    mcp_connect_timeout_s: float = Field(default=20, gt=0)
    mcp_max_result_chars: int = Field(default=20_000, ge=500, description="Longer tool results are cut.")

    # --- Auth: RBAC + OAuth (Layer 5) ---
    # Off = every request acts as a built-in dev user with DEV_USER_ROLES (local development only).
    auth_enabled: bool = False
    # The identity provider's issuer, exactly as in the tokens' `iss`, e.g. http://localhost:8080/realms/eap
    oidc_issuer: str | None = None
    # Tokens must list this in `aud` (Keycloak: an audience mapper on the web client).
    oidc_audience: str = "eap-api"
    # The public client the frontend signs in with (authorization code + PKCE).
    oidc_client_id: str = "eap-web"
    oidc_scope: str = "openid profile email"
    # Where to fetch signing keys. Blank = discover from {OIDC_ISSUER}/.well-known/openid-configuration.
    # Set it when the backend reaches the provider at another address
    # (in Docker: http://keycloak:8080/realms/eap/protocol/openid-connect/certs).
    oidc_jwks_url: str | None = None
    oidc_jwks_cache_s: float = Field(default=600, gt=0, description="Re-read signing keys after this long.")
    # Roles of the dev user when AUTH_ENABLED=false.
    dev_user_roles: CsvList = ["eap-admin"]
    # Limit models to roles, as JSON. Unlisted models are open to every role. Example:
    #   MODEL_ROLES={"anthropic/claude-opus-5-5": ["eap-admin", "eap-editor"]}
    model_roles: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator(
        "cors_origins",
        "anthropic_models",
        "anthropic_fallback_models",
        "openai_models",
        "gemini_models",
        "ollama_models",
        "dev_user_roles",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "anthropic_effort",
        "openai_base_url",
        "ollama_base_url",
        "database_url",
        "default_prompt_name",
        "embedding_provider",
        "embedding_model",
        "secret_key",
        "oidc_issuer",
        "oidc_jwks_url",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_auth(self) -> Self:
        if self.auth_enabled and not self.oidc_issuer:
            raise ValueError("AUTH_ENABLED=true needs OIDC_ISSUER (e.g. http://localhost:8080/realms/eap)")
        if self.oidc_issuer:
            self.oidc_issuer = self.oidc_issuer.rstrip("/")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
