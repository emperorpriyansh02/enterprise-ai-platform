"""Database connection: one async engine per app, one session per request."""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        kwargs: dict[str, Any] = {"echo": echo}
        if url.startswith("sqlite") and ":memory:" in url:
            # Tests: keep one shared in-memory database instead of a new one per connection.
            kwargs |= {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        else:
            kwargs["pool_pre_ping"] = True  # drop dead connections instead of failing a request
        self.engine = create_async_engine(url, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        """A new session. Use as `async with db.session() as session:`."""
        return self.sessionmaker()

    async def ping(self) -> bool:
        """True if the database answers a trivial query."""
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("database ping failed", exc_info=True)
            return False

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    async def has_pgvector(self) -> bool:
        """True if this is Postgres and the `vector` extension is installed in it."""
        if not self.is_postgres:
            return False
        async with self.engine.connect() as conn:
            found = await conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        return found is not None

    def _tables(self) -> list:
        """Every table, except the Postgres-only ones (pgvector) when this isn't Postgres."""
        import app.guardrails.models
        import app.mcp.models
        import app.prompts.models
        import app.rag.models  # noqa: F401

        tables = Base.metadata.sorted_tables
        if self.is_postgres:
            return tables
        return [t for t in tables if not t.info.get("postgres_only")]

    async def create_all(self) -> None:
        """Create all tables directly. Tests only; real databases use Alembic migrations."""
        tables = self._tables()
        async with self.engine.begin() as conn:
            if self.is_postgres:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    async def drop_all(self) -> None:
        """Drop all tables. Tests only."""
        tables = self._tables()
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=tables))

    async def aclose(self) -> None:
        await self.engine.dispose()
