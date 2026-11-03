"""Finding personal data and secrets in text, with checksums where the format has one.

Each entity type has a pattern and, where possible, a validator (Luhn for cards, mod-97 for IBANs,
Verhoeff for Aadhaar), so random digit runs aren't masked as card numbers. When matches overlap,
the more specific type wins (a 16-digit card isn't also reported as a phone number).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

# Order = priority when matches overlap: specific formats first, broad ones (phone, IP) last.
ENTITY_LABELS: dict[str, str] = {
    "secret": "[SECRET]",
    "email": "[EMAIL]",
    "credit_card": "[CARD]",
    "iban": "[IBAN]",
    "us_ssn": "[SSN]",
    "aadhaar": "[AADHAAR]",
    "pan": "[PAN]",
    "phone": "[PHONE]",
    "ip_address": "[IP]",
}
ENTITIES: tuple[str, ...] = tuple(ENTITY_LABELS)


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    type: str  # an entity type, or for topic rules the matched term

    @property
    def label(self) -> str:
        return ENTITY_LABELS.get(self.type, "[REDACTED]")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _iban_ok(value: str) -> bool:
    compact = value.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    number = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(number) % 97 == 1


# Verhoeff checksum tables (used by Aadhaar numbers).
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _verhoeff(digits: str) -> bool:
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(ch)]]
    return check == 0


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _card_ok(value: str) -> bool:
    digits = _digits(value)
    return 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _luhn(digits)


def _phone_ok(value: str) -> bool:
    digits = _digits(value)
    return 10 <= len(digits) <= 15 and len(set(digits)) > 2


def _aadhaar_ok(value: str) -> bool:
    digits = _digits(value)
    return len(digits) == 12 and _verhoeff(digits)


# (entity, pattern, validator, group): `group` is the part to mask (0 = the whole match).
_RULES: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None, int]] = [
    (
        "secret",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        None,
        0,
    ),
    ("secret", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"), None, 0),  # OpenAI / Anthropic keys
    ("secret", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), None, 0),  # AWS access key id
    ("secret", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"), None, 0),
    ("secret", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), None, 0),  # Slack
    ("secret", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), None, 0),  # Google API key
    (
        "secret",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        None,
        0,
    ),  # JWT
    (
        "secret",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*[\"']?([^\s\"']{6,})"
        ),
        None,
        1,
    ),
    (
        "email",
        re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,24}(?![\w-])"),
        None,
        0,
    ),
    ("credit_card", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])"), _card_ok, 0),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b"), _iban_ok, 0),
    ("us_ssn", re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])"), None, 0),
    ("aadhaar", re.compile(r"(?<![\d-])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\d-])"), _aadhaar_ok, 0),
    ("pan", re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b"), None, 0),
    (
        "phone",
        re.compile(
            r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)[ .-]?)?\d{2,5}(?:[ .-]?\d{2,5}){1,4}(?![\w])"
        ),
        _phone_ok,
        0,
    ),
    (
        "ip_address",
        re.compile(
            r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])"
        ),
        None,
        0,
    ),
]


def find_pii(text: str, entities: list[str] | tuple[str, ...] = ENTITIES) -> list[Match]:
    """Every personal-data or secret span in `text`, of the given types, without overlaps, in order."""
    wanted = set(entities)
    found: list[Match] = []
    for entity, pattern, validator, group in _RULES:
        if entity not in wanted:
            continue
        for m in pattern.finditer(text):
            start, end = m.span(group)
            if validator is not None and not validator(m.group(group)):
                continue
            if any(start < f.end and f.start < end for f in found):
                continue  # a more specific type already covers this text
            found.append(Match(start, end, entity))
    return sorted(found, key=lambda f: f.start)


def redact(text: str, matches: list[Match]) -> str:
    """Replace each match with its label, e.g. `[EMAIL]`. Matches must not overlap."""
    for match in sorted(matches, key=lambda f: f.start, reverse=True):
        text = text[: match.start] + match.label + text[match.end :]
    return text


def count_types(matches: list[Match]) -> dict[str, int]:
    """`{"email": 2, "phone": 1}`"""
    counts: dict[str, int] = {}
    for match in matches:
        counts[match.type] = counts.get(match.type, 0) + 1
    return counts
