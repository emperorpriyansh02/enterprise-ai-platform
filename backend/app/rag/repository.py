"""All database reads and writes for knowledge bases. No business rules here (see service.py)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import Text, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.models import Chunk, Collection, Document


@dataclass(frozen=True)
class ChunkMatch:
    """One search result row: the passage, where it's from, and how well it matched."""

    chunk: Chunk
    document_title: str
    collection_name: str
    score: float  # cosine similarity (vector search) or ts_rank_cd (keyword search)


class RagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Collections ---

    async def list_collections(self, *, include_archived: bool = False) -> list[Collection]:
        query = select(Collection).order_by(Collection.name)
        if not include_archived:
            query = query.where(Collection.archived_at.is_(None))
        return list((await self._session.scalars(query)).all())

    async def get_collection(self, name: str) -> Collection | None:
        return await self._session.scalar(select(Collection).where(Collection.name == name))

    async def document_counts(self, collection_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
        """Documents per status, per collection, in one query: {collection_id: {"ready": 3, ...}}."""
        if not collection_ids:
            return {}
        rows = await self._session.execute(
            select(Document.collection_id, Document.status, func.count())
            .where(Document.collection_id.in_(collection_ids))
            .group_by(Document.collection_id, Document.status)
        )
        counts: dict[uuid.UUID, dict[str, int]] = {}
        for collection_id, status, count in rows.all():
            counts.setdefault(collection_id, {})[status] = count
        return counts

    # --- Documents ---

    async def list_documents(self, collection_id: uuid.UUID) -> list[Document]:
        query = (
            select(Document)
            .where(Document.collection_id == collection_id)
            .order_by(Document.created_at.desc())
        )
        return list((await self._session.scalars(query)).all())

    async def get_document(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def find_by_hash(self, collection_id: uuid.UUID, content_hash: str) -> Document | None:
        return await self._session.scalar(
            select(Document).where(
                Document.collection_id == collection_id, Document.content_hash == content_hash
            )
        )

    async def unfinished_document_ids(self) -> list[uuid.UUID]:
        """Documents still `pending` or `processing`, oldest first (to re-queue after a restart)."""
        rows = await self._session.scalars(
            select(Document.id)
            .where(Document.status.in_(["pending", "processing"]))
            .order_by(Document.created_at)
        )
        return list(rows.all())

    # --- Chunks ---

    async def replace_chunks(self, document: Document, chunks: list[Chunk]) -> None:
        """Delete the document's old chunks and stage the new ones (written on commit)."""
        await self._session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        self._session.add_all(chunks)

    async def vector_search(
        self, collection_ids: list[uuid.UUID], embedding: list[float], limit: int
    ) -> list[ChunkMatch]:
        """Closest passages by meaning (cosine distance, using the HNSW index)."""
        distance = Chunk.embedding.cosine_distance(embedding)
        rows = await self._session.execute(
            select(Chunk, Document.title, Collection.name, (1 - distance).label("similarity"))
            .join(Document, Document.id == Chunk.document_id)
            .join(Collection, Collection.id == Chunk.collection_id)
            .where(Chunk.collection_id.in_(collection_ids), Document.status == "ready")
            .order_by(distance)
            .limit(limit)
        )
        return [
            ChunkMatch(chunk, title, name, float(similarity)) for chunk, title, name, similarity in rows.all()
        ]

    async def keyword_search(
        self, collection_ids: list[uuid.UUID], query: str, limit: int
    ) -> list[ChunkMatch]:
        """Passages containing any of the query's words (Postgres full-text search, English stemming).

        Postgres's own parsers require every word ("how long is the warranty" would miss a passage
        without "long"), so the words are joined with OR instead; `ts_rank_cd` still ranks passages
        with more of them first. Stop words ("how", "is", "the") are dropped by Postgres.
        """
        any_word = func.replace(cast(func.plainto_tsquery("english", query), Text), "&", "|")
        ts_query = func.to_tsquery("english", any_word)
        rank = func.ts_rank_cd(Chunk.search_vector, ts_query)
        rows = await self._session.execute(
            select(Chunk, Document.title, Collection.name, rank.label("rank"))
            .join(Document, Document.id == Chunk.document_id)
            .join(Collection, Collection.id == Chunk.collection_id)
            .where(
                Chunk.collection_id.in_(collection_ids),
                Document.status == "ready",
                Chunk.search_vector.op("@@")(ts_query),
            )
            .order_by(rank.desc())
            .limit(limit)
        )
        return [ChunkMatch(chunk, title, name, float(score)) for chunk, title, name, score in rows.all()]

    # --- Generic ---

    def add(self, row: Collection | Document) -> None:
        """Stage a new row. It's written when the service commits."""
        self._session.add(row)

    async def delete(self, row: Document) -> None:
        """Delete a row (its chunks go with it, by the database's cascade rule)."""
        await self._session.delete(row)
