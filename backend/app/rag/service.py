"""Knowledge-base rules: collections, adding and processing documents, hybrid search, chat context."""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.errors import (
    AppError,
    CollectionConflictError,
    CollectionNotFoundError,
    DocumentNotFoundError,
    DuplicateDocumentError,
    EmbeddingModelMismatchError,
    FileTooLargeError,
)
from app.rag.chunking import split_text
from app.rag.models import Chunk, Collection, Document
from app.rag.parsers import ExtractedDocument, extract_text, normalize_text
from app.rag.repository import ChunkMatch, RagRepository
from app.rag.retrieval import build_context, fuse_rrf
from app.rag.runtime import RagRuntime
from app.rag.schemas import (
    Citation,
    CollectionCreate,
    CollectionDetail,
    CollectionList,
    CollectionSummary,
    CollectionUpdate,
    DocumentCounts,
    DocumentList,
    DocumentOut,
    DocumentTextCreate,
    RagRef,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 300


@dataclass(frozen=True)
class RetrievedContext:
    hits: list[SearchHit]
    context: str  # the "Sources" block for the system prompt ("" when there are no hits)
    citations: list[Citation]


class RagService:
    def __init__(
        self, session: AsyncSession, runtime: RagRuntime, *, principal: Principal | None = None
    ) -> None:
        self._session = session
        self._repo = RagRepository(session)
        self._runtime = runtime
        self._settings = runtime.settings
        # The caller (Layer 5): collections their roles don't allow look like they don't exist.
        # None = no limits (the background worker).
        self._principal = principal

    @property
    def _embedder(self):
        return self._runtime.embedder

    # --- Collections ---

    async def list_collections(self, *, include_archived: bool = False) -> CollectionList:
        collections = [
            c
            for c in await self._repo.list_collections(include_archived=include_archived)
            if self._visible(c)
        ]
        counts = await self._repo.document_counts([c.id for c in collections])
        return CollectionList(data=[_summary(c, counts.get(c.id, {})) for c in collections])

    async def get_collection(self, name: str) -> CollectionDetail:
        return await self._detail(await self._require_collection(name))

    async def create_collection(self, body: CollectionCreate) -> CollectionDetail:
        if await self._repo.get_collection(body.name) is not None:
            raise CollectionConflictError(f'A collection named "{body.name}" already exists.')
        collection = Collection(
            id=uuid.uuid4(),
            name=body.name,
            description=body.description,
            embedding_model=self._embedder.id,
            allowed_roles=list(dict.fromkeys(body.allowed_roles)),
            created_by=self._username,
        )
        self._repo.add(collection)
        await self._commit()
        return await self._detail(collection)

    async def update_collection(self, name: str, body: CollectionUpdate) -> CollectionDetail:
        """Change the description and/or who may use the collection."""
        collection = await self._require_collection(name, writable=True)
        if body.description is not None:
            collection.description = body.description
        if body.allowed_roles is not None:
            collection.allowed_roles = list(dict.fromkeys(body.allowed_roles))
        await self._commit()
        return await self._detail(collection)

    async def archive_collection(self, name: str) -> CollectionDetail:
        collection = await self._require_collection(name)
        if collection.archived_at is None:
            collection.archived_at = datetime.now(UTC)
            await self._commit()
        return await self._detail(collection)

    async def reindex_collection(self, name: str) -> CollectionDetail:
        """Re-embed every document with the server's current embedding model."""
        collection = await self._require_collection(name, writable=True)
        collection.embedding_model = self._embedder.id
        documents = await self._repo.list_documents(collection.id)
        for document in documents:
            _reset(document)
        await self._commit()
        for document in documents:
            self._runtime.enqueue(document.id)
        return await self._detail(collection)

    # --- Documents ---

    async def list_documents(self, name: str) -> DocumentList:
        collection = await self._require_collection(name)
        return DocumentList(data=[_document_out(d) for d in await self._repo.list_documents(collection.id)])

    async def get_document(self, name: str, document_id: uuid.UUID) -> DocumentOut:
        collection = await self._require_collection(name)
        return _document_out(await self._require_document(collection, document_id))

    async def add_file(
        self, name: str, filename: str | None, content_type: str | None, data: bytes
    ) -> DocumentOut:
        """Check limits, read the file's text (in a thread), save it as `pending`, queue it."""
        collection = await self._require_collection(name, writable=True)
        max_bytes = int(self._settings.rag_max_upload_mb * 1024 * 1024)
        if len(data) > max_bytes:
            raise FileTooLargeError(
                f'"{filename}" is larger than the {self._settings.rag_max_upload_mb:g} MB limit.'
            )
        extracted = await asyncio.to_thread(extract_text, filename, content_type, data)
        return await self._add(collection, extracted, filename=filename, size=len(data))

    async def add_text(self, name: str, body: DocumentTextCreate) -> DocumentOut:
        collection = await self._require_collection(name, writable=True)
        text = normalize_text(body.text)
        extracted = ExtractedDocument(title=body.title, text=text, mime_type="text/plain")
        return await self._add(collection, extracted, filename=None, size=len(body.text.encode()))

    async def delete_document(self, name: str, document_id: uuid.UUID) -> None:
        collection = await self._require_collection(name)
        await self._repo.delete(await self._require_document(collection, document_id))
        await self._commit()

    async def reindex_document(self, name: str, document_id: uuid.UUID) -> DocumentOut:
        collection = await self._require_collection(name, writable=True)
        document = await self._require_document(collection, document_id)
        _reset(document)
        await self._commit()
        self._runtime.enqueue(document.id)
        return _document_out(document)

    async def process_document(self, document_id: uuid.UUID) -> None:
        """The background job: chunk → embed → save chunks → `ready` (or `failed` with the reason)."""
        document = await self._repo.get_document(document_id)
        if document is None:  # deleted while it waited in the queue
            return
        collection = await self._session.get(Collection, document.collection_id)
        document.status, document.error = "processing", None
        await self._session.commit()
        try:
            if collection.embedding_model != self._embedder.id:
                raise EmbeddingModelMismatchError(
                    f'Collection "{collection.name}" uses {collection.embedding_model} but the server uses '
                    f"{self._embedder.id}. Re-index the collection."
                )
            pieces = split_text(
                document.text, self._settings.rag_chunk_size, self._settings.rag_chunk_overlap
            )
            vectors = await self._embedder.embed(pieces, kind="document")
            chunks = [
                Chunk(
                    document_id=document.id,
                    collection_id=document.collection_id,
                    position=i,
                    content=piece,
                    embedding=vector,
                )
                for i, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
            ]
            await self._repo.replace_chunks(document, chunks)
            document.status, document.chunk_count = "ready", len(chunks)
            await self._session.commit()
            logger.info("document indexed", extra={"model": self._embedder.id})
        except Exception as exc:
            await self._session.rollback()
            document = await self._repo.get_document(document_id)
            if document is None:
                return
            document.status = "failed"
            document.error = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}"
            document.chunk_count = 0
            await self._session.commit()
            logger.warning("document failed: %s", document.error)

    # --- Search ---

    async def search_collection(self, name: str, body: SearchRequest) -> SearchResponse:
        hits = await self.search([name], body.query, body.top_k or self._settings.rag_top_k)
        return SearchResponse(
            query=body.query, collections=[name], embedding_model=self._embedder.id, hits=hits
        )

    async def search(self, names: list[str], query: str, top_k: int) -> list[SearchHit]:
        """Hybrid search over the named collections: vector + keyword, merged with RRF."""
        collections = [await self._require_collection(n, writable=True) for n in dict.fromkeys(names)]
        for collection in collections:
            if collection.embedding_model != self._embedder.id:
                raise EmbeddingModelMismatchError(
                    f'Collection "{collection.name}" was indexed with {collection.embedding_model}, but the '
                    f"server now uses {self._embedder.id}. Re-index it (POST /v1/collections/"
                    f"{collection.name}/reindex)."
                )
        ids = [c.id for c in collections]
        limit = self._settings.rag_candidates
        query_vector = (await self._embedder.embed([query], kind="query"))[0]
        by_vector = await self._repo.vector_search(ids, query_vector, limit)
        by_keyword = await self._repo.keyword_search(ids, query, limit)

        matches: dict[uuid.UUID, ChunkMatch] = {m.chunk.id: m for m in by_vector + by_keyword}
        vector_scores = {m.chunk.id: m.score for m in by_vector}
        keyword_scores = {m.chunk.id: m.score for m in by_keyword}
        fused = fuse_rrf([[m.chunk.id for m in by_vector], [m.chunk.id for m in by_keyword]])
        return [
            SearchHit(
                chunk_id=chunk_id,
                document_id=matches[chunk_id].chunk.document_id,
                document_title=matches[chunk_id].document_title,
                collection=matches[chunk_id].collection_name,
                position=matches[chunk_id].chunk.position,
                content=matches[chunk_id].chunk.content,
                score=round(score, 6),
                vector_similarity=_round(vector_scores.get(chunk_id)),
                keyword_score=_round(keyword_scores.get(chunk_id)),
            )
            for chunk_id, score in fused[:top_k]
        ]

    async def retrieve_for_chat(self, ref: RagRef, query: str) -> RetrievedContext:
        """The passages for one chat message, the "Sources" block, and the citations."""
        try:
            hits = await self.search(ref.collections, query, ref.top_k or self._settings.rag_top_k)
        finally:
            await self._session.rollback()  # release the DB connection before the (slow) LLM call
        citations = [
            Citation(
                index=i,
                collection=hit.collection,
                document_id=hit.document_id,
                document_title=hit.document_title,
                chunk_id=hit.chunk_id,
                position=hit.position,
                snippet=hit.content[:SNIPPET_CHARS],
                score=hit.score,
            )
            for i, hit in enumerate(hits, start=1)
        ]
        return RetrievedContext(hits=hits, context=build_context(hits) if hits else "", citations=citations)

    # --- Internals ---

    async def _add(
        self, collection: Collection, extracted: ExtractedDocument, *, filename: str | None, size: int
    ) -> DocumentOut:
        content_hash = hashlib.sha256(extracted.text.encode()).hexdigest()
        existing = await self._repo.find_by_hash(collection.id, content_hash)
        if existing is not None:
            raise DuplicateDocumentError(
                f'This content is already in "{collection.name}" as "{existing.title}" (id {existing.id}).'
            )
        document = Document(
            id=uuid.uuid4(),
            collection_id=collection.id,
            title=extracted.title,
            filename=filename,
            mime_type=extracted.mime_type,
            size_bytes=size,
            content_hash=content_hash,
            text=extracted.text,
            status="pending",
            chunk_count=0,
            created_by=self._username,
        )
        self._repo.add(document)
        collection.updated_at = datetime.now(UTC)
        await self._commit(duplicate_message=f'This content is already in "{collection.name}".')
        self._runtime.enqueue(document.id)
        return _document_out(document)

    @property
    def _username(self) -> str | None:
        return self._principal.username if self._principal else None

    def _visible(self, collection: Collection) -> bool:
        """May the caller see and use this collection? (Its `allowed_roles`; admins always may.)"""
        return self._principal is None or self._principal.may_use(collection.allowed_roles)

    async def _require_collection(self, name: str, *, writable: bool = False) -> Collection:
        collection = await self._repo.get_collection(name)
        if collection is None or not self._visible(collection):
            raise CollectionNotFoundError(f'No collection named "{name}".')
        if writable and collection.archived_at is not None:
            raise CollectionConflictError(f'Collection "{name}" is archived.')
        return collection

    async def _require_document(self, collection: Collection, document_id: uuid.UUID) -> Document:
        document = await self._repo.get_document(document_id)
        if document is None or document.collection_id != collection.id:
            raise DocumentNotFoundError(f'No document {document_id} in "{collection.name}".')
        return document

    async def _detail(self, collection: Collection) -> CollectionDetail:
        counts = (await self._repo.document_counts([collection.id])).get(collection.id, {})
        server_model = self._embedder.id if self._embedder else None
        return CollectionDetail(
            **_summary(collection, counts).model_dump(),
            server_embedding_model=server_model,
            needs_reindex=server_model is not None and collection.embedding_model != server_model,
        )

    async def _commit(self, *, duplicate_message: str | None = None) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if duplicate_message:
                raise DuplicateDocumentError(duplicate_message) from exc
            raise CollectionConflictError("A collection with this name already exists.") from exc


def _reset(document: Document) -> None:
    document.status, document.error = "pending", None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _summary(collection: Collection, counts: dict[str, int]) -> CollectionSummary:
    return CollectionSummary(
        name=collection.name,
        description=collection.description,
        embedding_model=collection.embedding_model,
        documents=DocumentCounts(**counts),
        archived=collection.archived_at is not None,
        allowed_roles=list(collection.allowed_roles or []),
        created_by=collection.created_by,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        title=document.title,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        status=document.status,
        error=document.error,
        chunk_count=document.chunk_count,
        created_by=document.created_by,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )
