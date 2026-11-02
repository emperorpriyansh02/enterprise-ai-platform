"""Prompt rules: versioning, activation (and rollback), archiving, and rendering for chat.

Versions are append-only: changing a prompt always creates a new version, so any past chat
reply can be traced to the exact text it used.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    PromptArchivedError,
    PromptConflictError,
    PromptNotFoundError,
    PromptVersionNotFoundError,
)
from app.prompts import template as tmpl
from app.prompts.models import Prompt, PromptVersion
from app.prompts.repository import PromptRepository
from app.prompts.schemas import (
    PromptCreate,
    PromptDetail,
    PromptList,
    PromptRef,
    PromptSummary,
    PromptUpdate,
    PromptVersionCreate,
    PromptVersionOut,
    RenderRequest,
    RenderResponse,
)


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: int
    text: str
    variables: list[str]


class PromptService:
    def __init__(self, session: AsyncSession, *, default_prompt_name: str | None = None) -> None:
        self._session = session
        self._repo = PromptRepository(session)
        self._default_prompt_name = default_prompt_name

    # --- Admin: read ---

    async def list_prompts(self, *, include_archived: bool = False) -> PromptList:
        prompts = await self._repo.list_prompts(include_archived=include_archived)
        ids = [p.id for p in prompts]
        latest = await self._repo.latest_versions(ids)
        active = await self._repo.active_versions(ids)
        return PromptList(data=[_summary(p, latest.get(p.id, 0), active.get(p.id)) for p in prompts])

    async def get_prompt(self, name: str) -> PromptDetail:
        return await self._detail(await self._require_prompt(name))

    async def get_version(self, name: str, version: int) -> PromptVersionOut:
        prompt = await self._require_prompt(name)
        return _version_out(await self._require_version(prompt, version), prompt)

    # --- Admin: write ---

    async def create_prompt(self, body: PromptCreate, *, created_by: str | None = None) -> PromptDetail:
        """Create a prompt with version 1 already active."""
        if await self._repo.get_prompt(body.name) is not None:
            raise PromptConflictError(f'A prompt named "{body.name}" already exists.')
        prompt = Prompt(id=uuid.uuid4(), name=body.name, description=body.description, active_version=1)
        self._repo.add(prompt)
        self._repo.add(_new_version(prompt, 1, body.template, body.note, created_by))
        await self._commit()
        return await self._detail(prompt)

    async def update_prompt(self, name: str, body: PromptUpdate) -> PromptDetail:
        prompt = await self._require_prompt(name)
        prompt.description = body.description
        await self._commit()
        return await self._detail(prompt)

    async def add_version(
        self, name: str, body: PromptVersionCreate, *, created_by: str | None = None
    ) -> PromptVersionOut:
        """Save a new version (next number). Only becomes active if `activate` is true."""
        prompt = await self._require_prompt(name, writable=True)
        number = (await self._repo.latest_versions([prompt.id])).get(prompt.id, 0) + 1
        version = _new_version(prompt, number, body.template, body.note, created_by)
        self._repo.add(version)
        if body.activate:
            prompt.active_version = number
        prompt.updated_at = datetime.now(UTC)
        await self._commit()
        return _version_out(version, prompt)

    async def activate(self, name: str, version: int) -> PromptDetail:
        """Make `version` the one chat uses. Activating an older version is a rollback."""
        prompt = await self._require_prompt(name, writable=True)
        await self._require_version(prompt, version)
        prompt.active_version = version
        await self._commit()
        return await self._detail(prompt)

    async def archive(self, name: str) -> PromptDetail:
        """Hide a prompt and stop chat from using it. Its versions are kept for audit."""
        prompt = await self._require_prompt(name)
        if prompt.archived_at is None:
            prompt.archived_at = datetime.now(UTC)
            await self._commit()
        return await self._detail(prompt)

    # --- Rendering ---

    async def render(self, name: str, body: RenderRequest) -> RenderResponse:
        """Preview the final text without calling any LLM."""
        rendered = await self._render(name, body.version, body.variables)
        return RenderResponse(
            name=rendered.name, version=rendered.version, text=rendered.text, variables=rendered.variables
        )

    async def resolve_for_chat(self, ref: PromptRef | None, *, has_system: bool) -> RenderedPrompt | None:
        """The prompt a chat request should use, rendered, or None for "no managed prompt".

        Uses `ref` if given; otherwise the configured default prompt, but only when the request
        brings no `system` text of its own.
        """
        if ref is None and (has_system or not self._default_prompt_name):
            return None
        try:
            if ref is not None:
                return await self._render(ref.name, ref.version, ref.variables)
            return await self._render(self._default_prompt_name or "", None, {})
        finally:
            # End the read transaction so no DB connection is held while the LLM streams.
            await self._session.rollback()

    # --- Internals ---

    async def _render(self, name: str, version: int | None, variables: dict) -> RenderedPrompt:
        prompt = await self._require_prompt(name)
        if prompt.archived_at is not None:
            raise PromptArchivedError(f'Prompt "{name}" is archived and can\'t be used.')
        number = version or prompt.active_version
        if number is None:
            raise PromptVersionNotFoundError(f'Prompt "{name}" has no active version.')
        row = await self._require_version(prompt, number)
        return RenderedPrompt(
            name=prompt.name,
            version=row.version,
            text=tmpl.render(row.template, variables),
            variables=row.variables,
        )

    async def _require_prompt(self, name: str, *, writable: bool = False) -> Prompt:
        prompt = await self._repo.get_prompt(name)
        if prompt is None:
            raise PromptNotFoundError(f'No prompt named "{name}".')
        if writable and prompt.archived_at is not None:
            raise PromptArchivedError(f'Prompt "{name}" is archived and can\'t be changed.')
        return prompt

    async def _require_version(self, prompt: Prompt, version: int) -> PromptVersion:
        row = await self._repo.get_version(prompt.id, version)
        if row is None:
            raise PromptVersionNotFoundError(f'Prompt "{prompt.name}" has no version {version}.')
        return row

    async def _detail(self, prompt: Prompt) -> PromptDetail:
        versions = await self._repo.list_versions(prompt.id)
        active = next((v for v in versions if v.version == prompt.active_version), None)
        summary = _summary(prompt, versions[0].version if versions else 0, active)
        return PromptDetail(**summary.model_dump(), versions=[_version_out(v, prompt) for v in versions])

    async def _commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise PromptConflictError(
                "Another change to this prompt happened at the same time. Please retry."
            ) from exc


def _new_version(
    prompt: Prompt, number: int, template: str, note: str, created_by: str | None
) -> PromptVersion:
    return PromptVersion(
        prompt_id=prompt.id,
        version=number,
        template=template,
        variables=tmpl.extract_variables(template),
        note=note,
        created_by=created_by,
    )


def _summary(prompt: Prompt, latest: int, active: PromptVersion | None) -> PromptSummary:
    return PromptSummary(
        name=prompt.name,
        description=prompt.description,
        active_version=prompt.active_version,
        latest_version=latest,
        active_variables=active.variables if active else [],
        archived=prompt.archived_at is not None,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


def _version_out(version: PromptVersion, prompt: Prompt) -> PromptVersionOut:
    return PromptVersionOut(
        version=version.version,
        template=version.template,
        variables=version.variables,
        note=version.note,
        created_by=version.created_by,
        created_at=version.created_at,
        is_active=version.version == prompt.active_version,
    )
