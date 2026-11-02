"""Admin API for managed prompts (Layer 2).

Reads are open. Writes need X-Admin-Key when ADMIN_API_KEY is set (until RBAC in Layer 5).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_admin, require_prompt_service
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

router = APIRouter(prefix="/v1/prompts", tags=["prompts"])

Prompts = Annotated[PromptService, Depends(require_prompt_service)]
Admin = Depends(require_admin)


@router.get("", response_model=PromptList)
async def list_prompts(service: Prompts, include_archived: Annotated[bool, Query()] = False) -> PromptList:
    return await service.list_prompts(include_archived=include_archived)


@router.post("", response_model=PromptDetail, status_code=201, dependencies=[Admin])
async def create_prompt(body: PromptCreate, service: Prompts) -> PromptDetail:
    return await service.create_prompt(body)


@router.get("/{name}", response_model=PromptDetail)
async def get_prompt(name: str, service: Prompts) -> PromptDetail:
    return await service.get_prompt(name)


@router.patch("/{name}", response_model=PromptDetail, dependencies=[Admin])
async def update_prompt(name: str, body: PromptUpdate, service: Prompts) -> PromptDetail:
    return await service.update_prompt(name, body)


@router.delete("/{name}", response_model=PromptDetail, dependencies=[Admin])
async def archive_prompt(name: str, service: Prompts) -> PromptDetail:
    return await service.archive(name)


@router.get("/{name}/versions/{version}", response_model=PromptVersionOut)
async def get_prompt_version(name: str, version: int, service: Prompts) -> PromptVersionOut:
    return await service.get_version(name, version)


@router.post("/{name}/versions", response_model=PromptVersionOut, status_code=201, dependencies=[Admin])
async def create_prompt_version(name: str, body: PromptVersionCreate, service: Prompts) -> PromptVersionOut:
    return await service.add_version(name, body)


@router.post("/{name}/activate", response_model=PromptDetail, dependencies=[Admin])
async def activate_prompt_version(name: str, body: ActivateRequest, service: Prompts) -> PromptDetail:
    return await service.activate(name, body.version)


@router.post("/{name}/render", response_model=RenderResponse)
async def render_prompt(name: str, body: RenderRequest, service: Prompts) -> RenderResponse:
    return await service.render(name, body)
