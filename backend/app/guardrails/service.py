"""Guardrail admin rules: policy CRUD, dry-run tests, and the event log.

Built-in policies can be edited and disabled but not deleted (they're re-added on startup anyway).
Every change clears the policy cache, so it applies to the next chat on this server at once.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.errors import GuardrailConfigError, GuardrailPolicyConflictError, GuardrailPolicyNotFoundError
from app.guardrails.engine import GuardrailEngine, Policy, TextResult
from app.guardrails.models import GuardrailEventRow, GuardrailPolicyRow
from app.guardrails.runtime import GuardrailRuntime
from app.guardrails.schemas import (
    EventList,
    EventOut,
    PolicyCreate,
    PolicyList,
    PolicyOut,
    PolicyUpdate,
    TestRequest,
    TestResponse,
    validate_config,
)


class GuardrailService:
    def __init__(
        self, session: AsyncSession, runtime: GuardrailRuntime, *, principal: Principal | None = None
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._principal = principal

    async def list_policies(self) -> PolicyList:
        rows = (
            await self._session.scalars(select(GuardrailPolicyRow).order_by(GuardrailPolicyRow.name))
        ).all()
        return PolicyList(data=[_policy_out(r) for r in rows])

    async def get_policy(self, name: str) -> PolicyOut:
        return _policy_out(await self._require(name))

    async def create_policy(self, body: PolicyCreate) -> PolicyOut:
        if await self._find(body.name) is not None:
            raise GuardrailPolicyConflictError(f'A guardrail policy named "{body.name}" already exists.')
        row = GuardrailPolicyRow(
            name=body.name,
            description=body.description,
            kind=body.kind,
            stages=list(body.stages),
            action=body.action,
            enabled=body.enabled,
            config=body.config,
            builtin=False,
            created_by=self._principal.username if self._principal else None,
        )
        self._session.add(row)
        await self._commit()
        return _policy_out(row)

    async def update_policy(self, name: str, body: PolicyUpdate) -> PolicyOut:
        """Change any part except the name and kind. A new `config` is checked for the policy's kind."""
        row = await self._require(name)
        if body.description is not None:
            row.description = body.description
        if body.stages is not None:
            row.stages = list(body.stages)
        if body.action is not None:
            row.action = body.action
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.config is not None:
            try:
                row.config = validate_config(row.kind, body.config)
            except ValueError as exc:
                raise GuardrailConfigError(str(exc)) from exc
        await self._commit()
        return _policy_out(row)

    async def delete_policy(self, name: str) -> None:
        row = await self._require(name)
        if row.builtin:
            raise GuardrailPolicyConflictError(f'"{name}" is built in: disable it instead of deleting it.')
        await self._session.delete(row)
        await self._commit()

    async def test(self, body: TestRequest) -> TestResponse:
        """Run policies on a sample text without recording anything. Names one policy, or all enabled."""
        if body.policy:
            policy = Policy.from_mapping(await self._require(body.policy))
            policies = [Policy.from_mapping({**vars(policy), "enabled": True, "stages": [body.stage]})]
        else:
            policies = await self._runtime.policies()
        await self._session.commit()  # end the read transaction before a possible judge call
        engine = GuardrailEngine(
            policies,
            self._runtime.judge,
            fail_closed=self._runtime.settings.guardrail_fail_closed,
            max_scan_chars=self._runtime.settings.guardrail_max_scan_chars,
        )
        result: TextResult
        if body.stage == "context":
            [result] = await engine.check_context([("test", body.text)])
        else:
            result = await engine.check_text(body.text, body.stage, "test")
        if result.blocked is not None:
            return TestResponse(blocked=True, text=result.blocked.message or "", traces=result.traces)
        return TestResponse(blocked=result.removed, text=result.text, traces=result.traces)

    async def list_events(
        self, *, limit: int = 50, policy: str | None = None, stage: str | None = None
    ) -> EventList:
        """Newest first."""
        query = select(GuardrailEventRow).order_by(GuardrailEventRow.created_at.desc()).limit(limit)
        if policy:
            query = query.where(GuardrailEventRow.policy == policy)
        if stage:
            query = query.where(GuardrailEventRow.stage == stage)
        rows = (await self._session.scalars(query)).all()
        return EventList(data=[EventOut.model_validate(r, from_attributes=True) for r in rows])

    async def _find(self, name: str) -> GuardrailPolicyRow | None:
        return await self._session.scalar(select(GuardrailPolicyRow).where(GuardrailPolicyRow.name == name))

    async def _require(self, name: str) -> GuardrailPolicyRow:
        row = await self._find(name)
        if row is None:
            raise GuardrailPolicyNotFoundError(f'No guardrail policy named "{name}".')
        return row

    async def _commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise GuardrailPolicyConflictError("A guardrail policy with this name already exists.") from exc
        self._runtime.invalidate()


def _policy_out(row: GuardrailPolicyRow) -> PolicyOut:
    return PolicyOut(
        name=row.name,
        description=row.description,
        kind=row.kind,  # type: ignore[arg-type]
        stages=list(row.stages or []),  # type: ignore[arg-type]
        action=row.action,  # type: ignore[arg-type]
        enabled=row.enabled,
        config=dict(row.config or {}),
        builtin=row.builtin,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
