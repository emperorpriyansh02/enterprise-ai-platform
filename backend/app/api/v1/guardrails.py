"""Guardrails admin API (Layer 6): policies, a dry-run tester, and the event log. Admins only
(`guardrails:admin`). Chats don't call this: the policies apply to them automatically."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import get_guardrail_service, require
from app.auth.permissions import GUARDRAILS_ADMIN
from app.guardrails.schemas import (
    EventList,
    PolicyCreate,
    PolicyList,
    PolicyOut,
    PolicyUpdate,
    Stage,
    TestRequest,
    TestResponse,
)
from app.guardrails.service import GuardrailService

router = APIRouter(
    prefix="/v1/guardrails", tags=["guardrails"], dependencies=[Depends(require(GUARDRAILS_ADMIN))]
)

Guardrails = Annotated[GuardrailService, Depends(get_guardrail_service)]


@router.get("/policies", response_model=PolicyList)
async def list_policies(service: Guardrails) -> PolicyList:
    return await service.list_policies()


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy(body: PolicyCreate, service: Guardrails) -> PolicyOut:
    return await service.create_policy(body)


@router.get("/policies/{name}", response_model=PolicyOut)
async def get_policy(name: str, service: Guardrails) -> PolicyOut:
    return await service.get_policy(name)


@router.patch("/policies/{name}", response_model=PolicyOut)
async def update_policy(name: str, body: PolicyUpdate, service: Guardrails) -> PolicyOut:
    """Change description, stages, action, enabled or config. Applies to the next chat."""
    return await service.update_policy(name, body)


@router.delete("/policies/{name}", status_code=204)
async def delete_policy(name: str, service: Guardrails) -> Response:
    """Custom policies only; built-in ones can be disabled instead."""
    await service.delete_policy(name)
    return Response(status_code=204)


@router.post("/test", response_model=TestResponse)
async def test_policies(body: TestRequest, service: Guardrails) -> TestResponse:
    """What the policies would do to a text at a stage. Nothing is recorded."""
    return await service.test(body)


@router.get("/events", response_model=EventList)
async def list_events(
    service: Guardrails,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    policy: Annotated[str | None, Query()] = None,
    stage: Annotated[Stage | None, Query()] = None,
) -> EventList:
    """Recent firings, newest first. PII findings store only counts, never the text."""
    return await service.list_events(limit=limit, policy=policy, stage=stage)
