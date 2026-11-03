"""The built-in policies. They're added to the database on startup if missing (admins can edit or disable
them, not delete them), and used as they are when there's no database.
"""

from typing import Any

from app.guardrails.pii import ENTITIES
from app.guardrails.rules import HARMFUL_CATEGORIES

HARMFUL_MESSAGE = (
    "I can't help with this request. If you're thinking about harming yourself or someone else, please "
    "reach out to someone you trust or a local emergency or crisis line right away."
)

BUILTIN_POLICIES: list[dict[str, Any]] = [
    {
        "name": "pii",
        "description": "Mask personal data and secrets (emails, phone numbers, cards, IDs, API keys) "
        "before they reach the model, and in replies.",
        "kind": "pii",
        "stages": ["input", "output"],
        "action": "redact",
        "config": {"entities": list(ENTITIES), "message": None},
    },
    {
        "name": "prompt-injection",
        "description": "Block messages that try to override or reveal the assistant's instructions "
        "(jailbreaks).",
        "kind": "injection",
        "stages": ["input"],
        "action": "block",
        "config": {
            "use_llm": True,
            "message": "This message looks like an attempt to override the assistant's instructions, so it "
            "wasn't sent. Rephrase it as a normal question.",
        },
    },
    {
        "name": "indirect-injection",
        "description": "Remove retrieved passages and tool results that contain instructions aimed at the "
        "assistant (indirect prompt injection).",
        "kind": "injection",
        "stages": ["context"],
        "action": "redact",
        "config": {"use_llm": True, "message": None},
    },
    {
        "name": "harmful-content",
        "description": "Block requests and replies about self-harm, weapons, illegal drugs, hate and "
        "similar.",
        "kind": "harmful",
        "stages": ["input", "output"],
        "action": "block",
        "config": {"use_llm": True, "categories": list(HARMFUL_CATEGORIES), "message": HARMFUL_MESSAGE},
    },
]
