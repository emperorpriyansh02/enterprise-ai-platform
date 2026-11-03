"""Rule-based detectors for prompt injection, harmful requests, and admin-defined topics.

Text is normalized first (Unicode NFKC, zero-width characters removed, lower case, single spaces), so
full-width letters, doubled spaces and invisible characters don't hide "ignore previous instructions".

Rules are fast and predictable but miss paraphrases; the LLM judge (judge.py) is the second opinion.
Injection and harmful rules report *what* they saw (pattern names), not spans: such a finding blocks
the text, or removes the passage when it was retrieved context.
"""

import re
import unicodedata

from app.guardrails.pii import Match

_ZERO_WIDTH = re.compile("[​-‏⁠﻿]")
_HIDDEN = re.compile("[\U000e0000-\U000e007f‪-‮⁦-⁩]")  # tag chars, bidi overrides

_INJECTION: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass|skip)\b.{0,40}?\b(?:previous|prior|above|earlier|"
            r"preceding|all|any|your|the|system|developer|safety)\b.{0,40}?\b(?:instructions?|rules|prompts?|"
            r"directions|guidelines|guardrails|messages?|policies)\b",
            re.S,
        ),
    ),
    (
        "reveal_prompt",
        re.compile(
            r"\b(?:reveal|show|print|repeat|output|display|leak|tell me|what (?:is|are|were))\b.{0,30}?"
            r"\b(?:your|the)\b.{0,20}?\b(?:system prompt|initial prompt|hidden (?:prompt|instructions)|"
            r"original instructions|instructions you were given|developer (?:message|instructions))\b",
            re.S,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"\byou are (?:now|no longer)\b|\bfrom now on,? you (?:are|will|must)\b|"
            r"\b(?:act|behave|respond) as (?:an? )?(?:unfiltered|uncensored|jailbroken|unrestricted|evil)\b|"
            r"\bdo anything now\b|\bdan mode\b|\bdeveloper mode (?:enabled|on)\b|\bjailbreak(?:ed)?\b"
        ),
    ),
    (
        "fake_system_message",
        re.compile(
            r"<\|(?:im_start|im_end|system|endoftext)\|>|\[/?(?:system|inst)\]|</?(?:system|instructions?)>|"
            r"(?:^|\n)\s*(?:#{1,3}\s*)?(?:system|developer)\s*(?:prompt|message)?\s*:|"
            r"\bbegin (?:new )?system prompt\b"
        ),
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(?:send|post|upload|forward|exfiltrate|transmit)\b.{0,60}?\b(?:to|at)\b\s*(?:this )?"
            r"(?:url|link|endpoint|address)?\s*:?\s*https?://",
            re.S,
        ),
    ),
]

_HARMFUL: list[tuple[str, re.Pattern[str]]] = [
    (
        "self_harm",
        re.compile(
            r"\b(?:i (?:want|plan|am going|'m going) to (?:kill|hurt|harm) myself|how (?:do i|to|can i) "
            r"(?:kill|hurt|harm) myself|ways to (?:die|commit suicide)|"
            r"best (?:way|method) to (?:die|commit suicide))\b"
        ),
    ),
    (
        "weapons",
        re.compile(
            r"\bhow (?:do i|to|can i|would i) (?:make|build|assemble|construct) (?:a |an )?(?:pipe )?"
            r"(?:bomb|explosive|ied|nerve agent|bioweapon|chemical weapon)s?\b"
        ),
    ),
    (
        "drugs",
        re.compile(
            r"\bhow (?:do i|to|can i) (?:make|cook|synthesi[sz]e|produce) "
            r"(?:meth|methamphetamine|fentanyl|heroin)\b"
        ),
    ),
]

HARMFUL_CATEGORIES: tuple[str, ...] = (
    "self_harm",
    "violence",
    "weapons",
    "drugs",
    "hate",
    "harassment",
    "sexual_minors",
    "extremism",
)


def normalize(text: str, *, zero_width_as: str = "") -> str:
    """Lower case, NFKC (full-width → normal letters), zero-width characters removed, single spaces."""
    text = unicodedata.normalize("NFKC", _ZERO_WIDTH.sub(zero_width_as, text)).lower()
    return re.sub(r"[ \t]+", " ", text)


def _variants(text: str) -> list[str]:
    """The normalized text, twice if it has zero-width characters: they may hide a letter or a space."""
    variants = [normalize(text)]
    if _ZERO_WIDTH.search(text):
        variants.append(normalize(text, zero_width_as=" "))
    return variants


def find_injection(text: str) -> dict[str, int]:
    """Which injection patterns appear, e.g. `{"ignore_instructions": 1, "hidden_text": 1}`."""
    found: dict[str, int] = {}
    if _HIDDEN.search(text) or len(_ZERO_WIDTH.findall(text)) >= 3:
        found["hidden_text"] = 1  # invisible characters are a common way to smuggle instructions
    for flat in _variants(text):
        for name, pattern in _INJECTION:
            hits = len(pattern.findall(flat))
            if hits:
                found[name] = max(found.get(name, 0), hits)
    return found


def find_harmful(text: str, categories: list[str] | tuple[str, ...] = HARMFUL_CATEGORIES) -> dict[str, int]:
    """High-precision phrases only (e.g. "how to make a bomb"); the LLM judge covers the rest."""
    wanted = set(categories)
    found: dict[str, int] = {}
    for flat in _variants(text):
        for name, pattern in _HARMFUL:
            if name in wanted and pattern.search(flat):
                found[name] = max(found.get(name, 0), len(pattern.findall(flat)))
    return found


def find_topic_terms(text: str, keywords: list[str], patterns: list[str]) -> list[Match]:
    """Spans of the admin's keywords (whole words, any case) and regex patterns, without overlaps."""
    found: list[Match] = []
    for keyword in keywords:
        term = keyword.strip()
        if not term:
            continue
        for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE):
            found.append(Match(m.start(), m.end(), term.lower()))
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if m.end() > m.start():
                found.append(Match(m.start(), m.end(), f"pattern:{pattern}"))
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    result: list[Match] = []
    for match in found:
        if not result or match.start >= result[-1].end:
            result.append(match)
    return result
