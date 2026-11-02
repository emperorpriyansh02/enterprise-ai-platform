"""Knowledge-base machinery that lives for the whole app: the embedder, the background ingestion
worker, and the "is RAG usable right now?" check.
"""

import asyncio
import logging
import uuid

from app.core.config import Settings
from app.core.errors import RagUnavailableError
from app.db.session import Database
from app.rag.embeddings import Embedder

logger = logging.getLogger(__name__)


class RagRuntime:
    def __init__(self, database: Database | None, embedder: Embedder | None, settings: Settings) -> None:
        self.database = database
        self.embedder = embedder
        self.settings = settings
        self.worker = IngestionWorker(self) if database is not None and embedder is not None else None
        self._pgvector_ok = False

    async def start(self) -> None:
        if self.worker is not None:
            await self.worker.start()

    async def stop(self) -> None:
        if self.worker is not None:
            await self.worker.stop()
        if self.embedder is not None:
            await self.embedder.aclose()

    async def check(self) -> None:
        """Raise RagUnavailableError (503) with the reason, unless knowledge bases can be used."""
        if self.database is None:
            raise RagUnavailableError("Knowledge bases need a database. Set DATABASE_URL.")
        if not self.database.is_postgres:
            raise RagUnavailableError("Knowledge bases need PostgreSQL with the pgvector extension.")
        if self.embedder is None:
            raise RagUnavailableError(
                "No embedding provider is configured. Set EMBEDDING_PROVIDER and its API key or URL."
            )
        if not self._pgvector_ok:  # checked once; cheap, and fixed by installing pgvector + migrating
            try:
                self._pgvector_ok = await self.database.has_pgvector()
            except Exception as exc:
                raise RagUnavailableError(f"Can't reach the database: {exc}") from exc
            if not self._pgvector_ok:
                raise RagUnavailableError(
                    "The pgvector extension isn't installed. Run `uv run alembic upgrade head` "
                    "as a database user allowed to create extensions."
                )

    async def status(self) -> str:
        """For /health: "ok", "disabled" (not configured) or "unavailable"."""
        if self.database is None or self.embedder is None:
            return "disabled"
        try:
            await self.check()
        except RagUnavailableError:
            return "unavailable"
        return "ok"

    def enqueue(self, document_id: uuid.UUID) -> None:
        if self.worker is not None:
            self.worker.enqueue(document_id)


class IngestionWorker:
    """Processes documents in the background: a queue of document ids and a few worker tasks.

    Each job gets its own database session. If the server stops mid-job, the document is still
    `pending`/`processing` in the database, and it's queued again on the next start.
    """

    def __init__(self, runtime: RagRuntime) -> None:
        self._runtime = runtime
        self._queue: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        for _ in range(self._runtime.settings.rag_ingest_concurrency):
            self._tasks.append(asyncio.create_task(self._run(), name="rag-ingestion"))
        try:
            await self._runtime.check()
            from app.rag.repository import RagRepository

            async with self._runtime.database.session() as session:
                unfinished = await RagRepository(session).unfinished_document_ids()
        except Exception as exc:  # startup must not fail because RAG isn't ready
            logger.info("ingestion worker: not re-queuing documents (%s)", exc)
            return
        for document_id in unfinished:
            self.enqueue(document_id)
        if unfinished:
            logger.info("ingestion worker: re-queued %d unfinished documents", len(unfinished))

    def enqueue(self, document_id: uuid.UUID) -> None:
        self._queue.put_nowait(document_id)

    async def _run(self) -> None:
        from app.rag.service import RagService

        while True:
            document_id = await self._queue.get()
            try:
                async with self._runtime.database.session() as session:
                    await RagService(session, self._runtime).process_document(document_id)
            except Exception:
                logger.exception("ingestion failed for document %s", document_id)
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        """Wait until every queued document has been processed (used by tests and scripts)."""
        await self._queue.join()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
