"""Admin API for managed prompts (Layer 2).

Reading needs `prompts:read` (every role); changes need `prompts:write` (editors and admins).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import require, require_prompt_service
from app.auth.permissions import PROMPTS_READ, PROMPTS_WRITE
from app.auth.principal import Principal
from app.prompts.schemas import (
    ActivateRequest,
    PromptCreate,
    PromptDetail,
    PromptList,
    PromptUpdate,
    PromptVersionCreate,
    PromptVersionOut,
    RenderRequest,
    RenderResponse,
)
from app.prompts.service import PromptService

router = APIRouter(prefix="/v1/prompts", tags=["prompts"], dependencies=[Depends(require(PROMPTS_READ))])

Prompts = Annotated[PromptService, Depends(require_prompt_service)]
Writer = Depends(require(PROMPTS_WRITE))
Author = Annotated[Principal, Writer]


@router.get("", response_model=PromptList)
async def list_prompts(service: Prompts, include_archived: Annotated[bool, Query()] = False) -> PromptList:
    return await service.list_prompts(include_archived=include_archived)


@router.post("", response_model=PromptDetail, status_code=201)
async def create_prompt(body: PromptCreate, service: Prompts, author: Author) -> PromptDetail:
    return await service.create_prompt(body, created_by=author.username)


@router.get("/{name}", response_model=PromptDetail)
async def get_prompt(name: str, service: Prompts) -> PromptDetail:
    return await service.get_prompt(name)


@router.patch("/{name}", response_model=PromptDetail, dependencies=[Writer])
async def update_prompt(name: str, body: PromptUpdate, service: Prompts) -> PromptDetail:
    return await service.update_prompt(name, body)


@router.delete("/{name}", response_model=PromptDetail, dependencies=[Writer])
async def archive_prompt(name: str, service: Prompts) -> PromptDetail:
    return await service.archive(name)


@router.get("/{name}/versions/{version}", response_model=PromptVersionOut)
async def get_prompt_version(name: str, version: int, service: Prompts) -> PromptVersionOut:
    return await service.get_version(name, version)


@router.post("/{name}/versions", response_model=PromptVersionOut, status_code=201)
async def create_prompt_version(
    name: str, body: PromptVersionCreate, service: Prompts, author: Author
) -> PromptVersionOut:
    return await service.add_version(name, body, created_by=author.username)


@router.post("/{name}/activate", response_model=PromptDetail, dependencies=[Writer])
async def activate_prompt_version(name: str, body: ActivateRequest, service: Prompts) -> PromptDetail:
    return await service.activate(name, body.version)


@router.post("/{name}/render", response_model=RenderResponse)
async def render_prompt(name: str, body: RenderRequest, service: Prompts) -> RenderResponse:
    return await service.render(name, body)
