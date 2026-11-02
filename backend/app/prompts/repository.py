"""All database reads and writes for prompts. No business rules here; those live in service.py."""

import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.prompts.models import Prompt, PromptVersion


class PromptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_prompts(self, *, include_archived: bool = False) -> list[Prompt]:
        query = select(Prompt).order_by(Prompt.name)
        if not include_archived:
            query = query.where(Prompt.archived_at.is_(None))
        return list((await self._session.scalars(query)).all())

    async def get_prompt(self, name: str) -> Prompt | None:
        return await self._session.scalar(select(Prompt).where(Prompt.name == name))

    async def get_version(self, prompt_id: uuid.UUID, version: int) -> PromptVersion | None:
        return await self._session.scalar(
            select(PromptVersion).where(
                PromptVersion.prompt_id == prompt_id, PromptVersion.version == version
            )
        )

    async def list_versions(self, prompt_id: uuid.UUID) -> list[PromptVersion]:
        query = (
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.version.desc())
        )
        return list((await self._session.scalars(query)).all())

    async def latest_versions(self, prompt_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Highest version number per prompt, for many prompts in one query."""
        if not prompt_ids:
            return {}
        rows = await self._session.execute(
            select(PromptVersion.prompt_id, func.max(PromptVersion.version))
            .where(PromptVersion.prompt_id.in_(prompt_ids))
            .group_by(PromptVersion.prompt_id)
        )
        return {prompt_id: latest for prompt_id, latest in rows.all()}

    async def active_versions(self, prompt_ids: list[uuid.UUID]) -> dict[uuid.UUID, PromptVersion]:
        """The active version row per prompt, for many prompts in one query."""
        if not prompt_ids:
            return {}
        rows = await self._session.scalars(
            select(PromptVersion)
            .join(
                Prompt,
                and_(Prompt.id == PromptVersion.prompt_id, Prompt.active_version == PromptVersion.version),
            )
            .where(Prompt.id.in_(prompt_ids))
        )
        return {row.prompt_id: row for row in rows.all()}

    def add(self, row: Prompt | PromptVersion) -> None:
        """Stage a new row. It's written when the service commits."""
        self._session.add(row)
