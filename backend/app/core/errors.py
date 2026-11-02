"""Error types shared by the whole app, and how they map to HTTP responses.

Provider adapters translate their SDK's exceptions into the `Provider*Error` classes below,
so the API returns the same error shape no matter which LLM vendor failed.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 500
    code = "internal_error"

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


class AdminAuthError(AppError):
    """Missing or wrong X-Admin-Key header. Temporary until RBAC (Layer 5)."""

    status_code = 401
    code = "admin_auth_required"


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


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})
