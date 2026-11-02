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

    async def create_all(self) -> None:
        """Create all tables directly. Tests only; real databases use Alembic migrations."""
        import app.prompts.models  # noqa: F401  (registers the tables on Base.metadata)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        """Drop all tables. Tests only."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def aclose(self) -> None:
        await self.engine.dispose()
