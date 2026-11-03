"""Error types shared by the whole app, and how they map to HTTP responses.

Provider adapters translate their SDK's exceptions into the `Provider*Error` classes below,
so the API returns the same error shape no matter which LLM vendor failed.
"""

from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    headers: ClassVar[dict[str, str] | None] = None  # extra response headers (e.g. WWW-Authenticate)

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider

    def to_dict(self) -> dict:
        body = {"code": self.code, "message": self.message}
        if self.provider:
            body["provider"] = self.provider
        return body


class ModelNotFoundError(AppError):
    status_code = 404
    code = "model_not_found"


class ProviderError(AppError):
    """An upstream LLM provider call failed."""

    status_code = 502
    code = "provider_error"


class ProviderAuthError(ProviderError):
    """Our credentials for the provider were rejected. A server config problem, not the caller's."""

    status_code = 502
    code = "provider_auth_error"


class ProviderBadRequestError(ProviderError):
    """The provider rejected the request (bad parameter, model not found upstream, too long)."""

    status_code = 400
    code = "provider_bad_request"


class ProviderRateLimitError(ProviderError):
    status_code = 429
    code = "provider_rate_limited"


class ProviderTimeoutError(ProviderError):
    status_code = 504
    code = "provider_timeout"


class ProviderUnavailableError(ProviderError):
    """Network failure, provider 5xx, or overloaded."""

    status_code = 503
    code = "provider_unavailable"


# --- Prompts (Layer 2) ---


class PromptNotFoundError(AppError):
    status_code = 404
    code = "prompt_not_found"


class PromptVersionNotFoundError(AppError):
    status_code = 404
    code = "prompt_version_not_found"


class PromptConflictError(AppError):
    """A prompt with this name already exists, or two changes collided."""

    status_code = 409
    code = "prompt_conflict"


class PromptArchivedError(AppError):
    """The prompt is archived, so it can't be changed or used."""

    status_code = 409
    code = "prompt_archived"


class PromptVariableError(AppError):
    """A template variable has no value."""

    status_code = 400
    code = "prompt_variable_missing"


class PromptStoreUnavailableError(AppError):
    """No database is configured (DATABASE_URL), or it can't be reached."""

    status_code = 503
    code = "prompt_store_unavailable"


# --- Knowledge bases / RAG (Layer 3) ---


class CollectionNotFoundError(AppError):
    status_code = 404
    code = "collection_not_found"


class CollectionConflictError(AppError):
    """A collection with this name already exists, or it's archived and can't be changed."""

    status_code = 409
    code = "collection_conflict"


class DocumentNotFoundError(AppError):
    status_code = 404
    code = "document_not_found"


class DuplicateDocumentError(AppError):
    """The same text is already in this collection."""

    status_code = 409
    code = "duplicate_document"


class UnsupportedFileTypeError(AppError):
    status_code = 415
    code = "unsupported_file_type"


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"


class DocumentParseError(AppError):
    """The file couldn't be read, or it has no text (e.g. a scanned PDF)."""

    status_code = 422
    code = "document_parse_error"


class EmbeddingModelMismatchError(AppError):
    """The collection was indexed with a different embedding model than the server uses now."""

    status_code = 409
    code = "embedding_model_mismatch"


class RagUnavailableError(AppError):
    """No database, no pgvector, or no embedding provider configured."""

    status_code = 503
    code = "rag_unavailable"


# --- MCP tools (Layer 4) ---


class ToolsUnavailableError(AppError):
    """No database is configured, so there's no MCP server registry."""

    status_code = 503
    code = "tools_unavailable"


class MCPServerNotFoundError(AppError):
    status_code = 404
    code = "mcp_server_not_found"


class MCPServerConflictError(AppError):
    status_code = 409
    code = "mcp_server_conflict"


class MCPToolNotFoundError(AppError):
    status_code = 404
    code = "mcp_tool_not_found"


class MCPConnectionError(AppError):
    """The MCP server couldn't be started, reached, or didn't answer in time."""

    status_code = 502
    code = "mcp_connection_failed"


class MCPStdioDisabledError(AppError):
    """Local (stdio) MCP servers are switched off (MCP_ALLOW_STDIO=false)."""

    status_code = 403
    code = "mcp_stdio_disabled"


class SecretKeyMissingError(AppError):
    """A secret (env value or header) was given, but SECRET_KEY isn't set, so it can't be encrypted."""

    status_code = 400
    code = "secret_key_missing"


# --- Guardrails (Layer 6) ---


class GuardrailPolicyNotFoundError(AppError):
    status_code = 404
    code = "guardrail_policy_not_found"


class GuardrailsUnavailableError(AppError):
    """Managing policies needs the database (chats still get the built-in defaults without one)."""

    status_code = 503
    code = "guardrails_unavailable"


class GuardrailConfigError(AppError):
    """A policy's config doesn't fit its kind (e.g. a bad regex)."""

    status_code = 422
    code = "guardrail_config_invalid"


class GuardrailPolicyConflictError(AppError):
    """The name is taken, or a built-in policy was asked to be deleted (disable it instead)."""

    status_code = 409
    code = "guardrail_policy_conflict"


# --- Auth: RBAC + OAuth (Layer 5) ---


class AuthRequiredError(AppError):
    """No `Authorization: Bearer <token>` header."""

    status_code = 401
    code = "auth_required"
    headers: ClassVar[dict[str, str] | None] = {"WWW-Authenticate": "Bearer"}


class InvalidTokenError(AppError):
    """The token is malformed, expired, for another audience or issuer, or its signature is wrong."""

    status_code = 401
    code = "invalid_token"
    headers: ClassVar[dict[str, str] | None] = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


class ForbiddenError(AppError):
    """Signed in, but the user's roles don't allow this."""

    status_code = 403
    code = "forbidden"


class AuthUnavailableError(AppError):
    """The identity provider's signing keys can't be fetched, so no token can be checked."""

    status_code = 503
    code = "auth_unavailable"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        body = {"error": exc.to_dict()}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)
