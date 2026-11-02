"""Prompt templates: `{{ variable }}` placeholders, filled in with plain text.

Deliberately tiny and safe: no loops, no expressions, no code execution. Anything that isn't
`{{ name }}` (letters, digits, underscores) is left exactly as written.
"""

import re
from collections.abc import Mapping

from app.core.errors import PromptVariableError

VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

Scalar = str | int | float | bool


def extract_variables(template: str) -> list[str]:
    """Variable names in the order they first appear, without duplicates."""
    return list(dict.fromkeys(match.group(1) for match in VARIABLE.finditer(template)))


def render(template: str, variables: Mapping[str, Scalar]) -> str:
    """Replace every `{{ name }}` with its value. Extra values are ignored."""
    missing = [name for name in extract_variables(template) if name not in variables]
    if missing:
        raise PromptVariableError(f"Missing values for prompt variables: {', '.join(missing)}")
    return VARIABLE.sub(lambda match: str(variables[match.group(1)]), template)
