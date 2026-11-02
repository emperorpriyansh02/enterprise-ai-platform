"""Public API shapes for knowledge bases, and the `rag` field / citations on chat."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.prompts.schemas import NAME_PATTERN

DocumentStatus = Literal["pending", "processing", "ready", "failed"]

# --- Requests ---


class CollectionCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=NAME_PATTERN, examples=["hr-policies"])
    description: str = Field(default="", max_length=500)


class DocumentTextCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=5_000_000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class RagRef(BaseModel):
    """The `rag` field on a chat request: which knowledge bases to search for this message."""

    collections: list[str] = Field(min_length=1, max_length=10, examples=[["hr-policies"]])
    top_k: int | None = Field(default=None, ge=1, le=20, description="Passages to add (default RAG_TOP_K).")


# --- Responses ---


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    filename: str | None
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    data: list[DocumentOut]


class UploadError(BaseModel):
    filename: str
    code: str
    message: str


class UploadResult(BaseModel):
    """Files accepted for processing, and files refused (with why). Accepted ones start as `pending`."""

    data: list[DocumentOut]
    errors: list[UploadError]


class DocumentCounts(BaseModel):
    pending: int = 0
    processing: int = 0
    ready: int = 0
    failed: int = 0


class CollectionSummary(BaseModel):
    name: str
    description: str
    embedding_model: str
    documents: DocumentCounts
    archived: bool
    created_at: datetime
    updated_at: datetime


class CollectionDetail(CollectionSummary):
    server_embedding_model: str | None = Field(description="The embedder the server uses now.")
    needs_reindex: bool = Field(description="True when the two embedding models differ.")


class CollectionList(BaseModel):
    data: list[CollectionSummary]


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    collection: str
    position: int = Field(description="0 = first passage of the document.")
    content: str
    score: float = Field(description="Combined rank score (Reciprocal Rank Fusion). Higher is better.")
    vector_similarity: float | None = Field(description="Cosine similarity, 0-1, if found by vector search.")
    keyword_score: float | None = Field(description="Full-text rank, if found by keyword search.")


class SearchResponse(BaseModel):
    query: str
    collections: list[str]
    embedding_model: str
    hits: list[SearchHit]


class Citation(BaseModel):
    """One source passage given to the model; `index` is the [n] the answer cites."""

    index: int
    collection: str
    document_id: uuid.UUID
    document_title: str
    chunk_id: uuid.UUID
    position: int
    snippet: str
    score: float
