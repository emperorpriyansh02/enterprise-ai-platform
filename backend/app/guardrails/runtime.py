"""Guardrail machinery that lives for the whole app: the policy cache, the judge, and the event writer.

Policies are read from the database and cached for GUARDRAIL_CACHE_S (changes made through the API
apply at once on this server). Without a database, or if it can't be read, the built-in defaults
apply, so the platform is never left without guardrails.

Events are written by a background task, so recording a firing never slows a chat down.
"""

import asyncio
import logging
import time

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.guardrails.defaults import BUILTIN_POLICIES
from app.guardrails.engine import EventRecord, GuardrailEngine, Policy
from app.guardrails.judge import LLMJudge
from app.guardrails.models import GuardrailEventRow, GuardrailPolicyRow
from app.llm.registry import ProviderRegistry

logger = logging.getLogger(__name__)

BATCH = 100  # events per database write


class GuardrailRuntime:
    def __init__(self, database: Database | None, registry: ProviderRegistry, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.judge = LLMJudge(
            registry, settings.guardrail_judge_model, timeout_s=settings.guardrail_judge_timeout_s
        )
        self._cache: list[Policy] | None = None
        self._cached_at = 0.0
        self._queue: asyncio.Queue[EventRecord] = asyncio.Queue()
        self._writer: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.guardrails_enabled

    async def start(self) -> None:
        """Add any missing built-in policy to the database, and start the event writer."""
        if self.database is None:
            return
        try:
            await self.seed()
        except Exception as exc:  # tables missing (migrations not run) or database down: defaults apply
            logger.warning("guardrails: can't prepare policies in the database (%s); using defaults", exc)
        self._writer = asyncio.create_task(self._write_events(), name="guardrail-events")
        logger.info(
            "guardrails: %s, judge %s",
            "enabled" if self.enabled else "DISABLED",
            self.settings.guardrail_judge_model or "off (rules only)",
        )

    async def seed(self) -> None:
        assert self.database is not None
        async with self.database.session() as session:
            existing = set((await session.scalars(select(GuardrailPolicyRow.name))).all())
            missing = [p for p in BUILTIN_POLICIES if p["name"] not in existing]
            for policy in missing:
                session.add(GuardrailPolicyRow(**policy, enabled=True, builtin=True, created_by="system"))
            await session.commit()
        if missing:
            logger.info("guardrails: added built-in policies %s", ", ".join(p["name"] for p in missing))

    async def policies(self) -> list[Policy]:
        """The current policies: cached, else from the database, else the built-in defaults."""
        fresh = (
            self._cache is not None and time.monotonic() - self._cached_at < self.settings.guardrail_cache_s
        )
        if fresh:
            return self._cache  # type: ignore[return-value]
        if self.database is None:
            self._cache = [Policy.from_mapping(p) for p in BUILTIN_POLICIES]
        else:
            try:
                async with self.database.session() as session:
                    rows = (
                        await session.scalars(select(GuardrailPolicyRow).order_by(GuardrailPolicyRow.name))
                    ).all()
                self._cache = [Policy.from_mapping(r) for r in rows] or [
                    Policy.from_mapping(p) for p in BUILTIN_POLICIES
                ]
            except Exception as exc:
                logger.warning("guardrails: can't read policies (%s); using the last known or defaults", exc)
                if self._cache is None:
                    self._cache = [Policy.from_mapping(p) for p in BUILTIN_POLICIES]
        self._cached_at = time.monotonic()
        return self._cache

    def invalidate(self) -> None:
        """Forget the cached policies (after a change through the API)."""
        self._cache = None

    async def engine(self, username: str | None = None) -> GuardrailEngine | None:
        """An engine with the current policies for one request, or None when guardrails are off."""
        if not self.enabled:
            return None
        return GuardrailEngine(
            await self.policies(),
            self.judge,
            sink=self,
            fail_closed=self.settings.guardrail_fail_closed,
            max_scan_chars=self.settings.guardrail_max_scan_chars,
            username=username,
        )

    def record(self, event: EventRecord) -> None:
        """Queue an event for the database (no-op without one)."""
        if self.database is not None and self._writer is not None:
            self._queue.put_nowait(event)

    async def _write_events(self) -> None:
        assert self.database is not None
        while True:
            batch = [await self._queue.get()]
            while not self._queue.empty() and len(batch) < BATCH:
                batch.append(self._queue.get_nowait())
            try:
                async with self.database.session() as session:
                    session.add_all(GuardrailEventRow(**vars(event)) for event in batch)
                    await session.commit()
            except Exception:
                logger.exception("guardrails: couldn't save %d events", len(batch))
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def drain(self) -> None:
        """Wait until every queued event is saved (tests and shutdown)."""
        await self._queue.join()

    async def stop(self) -> None:
        if self._writer is None:
            return
        try:
            await asyncio.wait_for(self.drain(), timeout=5)
        except TimeoutError:
            logger.warning("guardrails: %d events not saved at shutdown", self._queue.qsize())
        self._writer.cancel()
        await asyncio.gather(self._writer, return_exceptions=True)
        self._writer = None
