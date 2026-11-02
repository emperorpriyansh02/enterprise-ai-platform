"""Add example prompts to an empty database. Safe to run again: existing prompts are skipped.

cd backend && uv run python -m app.prompts.seed
"""

import asyncio

from app.core.config import Settings
from app.core.errors import PromptNotFoundError
from app.db.session import Database
from app.prompts.schemas import PromptCreate
from app.prompts.service import PromptService

EXAMPLES = [
    PromptCreate(
        name="general-assistant",
        description="Default helpful assistant.",
        template=(
            "You are a helpful, accurate assistant. Answer clearly and concisely. "
            "If you are not sure about something, say so."
        ),
        note="Initial version",
    ),
    PromptCreate(
        name="support-agent",
        description="Customer support agent for a named company.",
        template=(
            "You are a customer support agent for {{ company_name }}. Be friendly and professional. "
            "Answer questions about {{ company_name }}'s products. If you can't help, offer to "
            "connect the customer with a human agent at {{ support_email }}."
        ),
        note="Initial version",
    ),
]


async def seed(service: PromptService) -> list[str]:
    """Create each example prompt that doesn't exist yet. Returns the names it created."""
    created = []
    for example in EXAMPLES:
        try:
            await service.get_prompt(example.name)
        except PromptNotFoundError:
            await service.create_prompt(example, created_by="seed")
            created.append(example.name)
    return created


async def main() -> None:
    settings = Settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")
    database = Database(settings.database_url)
    try:
        async with database.session() as session:
            created = await seed(PromptService(session))
    finally:
        await database.aclose()
    print(f"Created: {', '.join(created)}" if created else "Nothing to do: example prompts already exist.")


if __name__ == "__main__":
    asyncio.run(main())
