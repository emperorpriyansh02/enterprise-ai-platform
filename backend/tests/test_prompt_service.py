import pytest

from app.core.errors import (
    PromptArchivedError,
    PromptConflictError,
    PromptNotFoundError,
    PromptVariableError,
    PromptVersionNotFoundError,
)
from app.prompts.schemas import PromptCreate, PromptRef, PromptUpdate, PromptVersionCreate, RenderRequest
from app.prompts.seed import seed
from app.prompts.service import PromptService

SUPPORT = PromptCreate(
    name="support", description="Support bot", template="You support {{ company }}.", note="first"
)


async def test_create_makes_version_1_active(prompt_service: PromptService):
    detail = await prompt_service.create_prompt(SUPPORT, created_by="ada")

    assert (detail.name, detail.active_version, detail.latest_version) == ("support", 1, 1)
    assert detail.active_variables == ["company"]
    assert [v.version for v in detail.versions] == [1]
    assert detail.versions[0].is_active and detail.versions[0].created_by == "ada"


async def test_duplicate_name_conflicts(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)
    with pytest.raises(PromptConflictError):
        await prompt_service.create_prompt(SUPPORT)


async def test_new_versions_count_up_and_activate_only_when_asked(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)

    v2 = await prompt_service.add_version("support", PromptVersionCreate(template="v2 {{ company }}"))
    v3 = await prompt_service.add_version(
        "support", PromptVersionCreate(template="v3 for {{ team }}", activate=True)
    )

    assert (v2.version, v2.is_active) == (2, False)
    assert (v3.version, v3.is_active, v3.variables) == (3, True, ["team"])
    detail = await prompt_service.get_prompt("support")
    assert detail.active_version == 3 and detail.latest_version == 3
    assert [v.version for v in detail.versions] == [3, 2, 1]  # newest first


async def test_activate_older_version_is_a_rollback(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)
    await prompt_service.add_version("support", PromptVersionCreate(template="new", activate=True))

    detail = await prompt_service.activate("support", 1)

    assert detail.active_version == 1
    rendered = await prompt_service.resolve_for_chat(
        PromptRef(name="support", variables={"company": "Acme"}), has_system=False
    )
    assert (rendered.version, rendered.text) == (1, "You support Acme.")


async def test_activate_missing_version(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)
    with pytest.raises(PromptVersionNotFoundError):
        await prompt_service.activate("support", 9)


async def test_unknown_prompt(prompt_service: PromptService):
    with pytest.raises(PromptNotFoundError):
        await prompt_service.get_prompt("nope")


async def test_update_description(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)
    detail = await prompt_service.update_prompt("support", PromptUpdate(description="New text"))
    assert detail.description == "New text"


async def test_archive_hides_and_blocks_use_and_changes(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)

    archived = await prompt_service.archive("support")

    assert archived.archived
    assert (await prompt_service.list_prompts()).data == []
    assert [p.name for p in (await prompt_service.list_prompts(include_archived=True)).data] == ["support"]
    with pytest.raises(PromptArchivedError):
        await prompt_service.add_version("support", PromptVersionCreate(template="x"))
    with pytest.raises(PromptArchivedError):
        await prompt_service.resolve_for_chat(PromptRef(name="support"), has_system=False)


async def test_render_specific_version_and_missing_variable(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)
    await prompt_service.add_version("support", PromptVersionCreate(template="Plain v2", activate=True))

    v1 = await prompt_service.render("support", RenderRequest(version=1, variables={"company": "Acme"}))
    active = await prompt_service.render("support", RenderRequest())

    assert (v1.version, v1.text, v1.variables) == (1, "You support Acme.", ["company"])
    assert (active.version, active.text) == (2, "Plain v2")
    with pytest.raises(PromptVariableError):
        await prompt_service.render("support", RenderRequest(version=1))


async def test_default_prompt_applies_only_without_system(database):
    async with database.session() as session:
        service = PromptService(session, default_prompt_name="general")
        await service.create_prompt(PromptCreate(name="general", template="Be helpful."))

        used = await service.resolve_for_chat(None, has_system=False)
        skipped = await service.resolve_for_chat(None, has_system=True)

    assert (used.name, used.version, used.text) == ("general", 1, "Be helpful.")
    assert skipped is None


async def test_no_prompt_and_no_default_returns_none(prompt_service: PromptService):
    assert await prompt_service.resolve_for_chat(None, has_system=False) is None


async def test_list_summaries(prompt_service: PromptService):
    await prompt_service.create_prompt(SUPPORT)
    await prompt_service.create_prompt(PromptCreate(name="alpha", template="A {{ x }}"))
    await prompt_service.add_version("alpha", PromptVersionCreate(template="A2"))

    data = (await prompt_service.list_prompts()).data

    assert [(p.name, p.active_version, p.latest_version, p.active_variables) for p in data] == [
        ("alpha", 1, 2, ["x"]),
        ("support", 1, 1, ["company"]),
    ]


async def test_seed_is_idempotent(prompt_service: PromptService):
    assert await seed(prompt_service) == ["general-assistant", "support-agent"]
    assert await seed(prompt_service) == []
    support = await prompt_service.get_prompt("support-agent")
    assert support.active_variables == ["company_name", "support_email"]
