"""Applying guardrail policies to text at the three stages of a chat.

    input    the user's messages, before anything else happens
    context  retrieved passages and tool results, before the model reads them
    output   the model's reply, before the user sees it (also while it streams)

For each enabled policy of the stage: rules first; if they find nothing and the policy uses the judge
(and one is configured), the judge model decides. Then the policy's action:

    log     record it, change nothing
    redact  mask what was found (e.g. `[EMAIL]`). Findings without a span (injection, harmful, a judged
            topic) can't be masked: they block the text, or remove the passage/tool result (context).
    block   input/output: the text is replaced by the policy's message (no model call for input).
            context: the passage or tool result is removed (replaced by a short note).

Every firing is reported as a GuardrailTrace and recorded as an event (EventSink).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.guardrails.judge import JudgeUnavailableError, LLMJudge
from app.guardrails.pii import ENTITIES, Match, count_types, find_pii, redact
from app.guardrails.rules import HARMFUL_CATEGORIES, find_harmful, find_injection, find_topic_terms
from app.guardrails.schemas import GuardrailTrace
from app.schemas.chat import Message

logger = logging.getLogger(__name__)

SPAN_KINDS = ("pii", "topic")  # kinds whose rules find spans that can be masked
EXCERPT_CHARS = 200


@dataclass(frozen=True)
class Policy:
    name: str
    kind: str
    stages: tuple[str, ...]
    action: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    builtin: bool = False

    @classmethod
    def from_mapping(cls, data: Any) -> "Policy":
        """From a database row or a plain dict (the built-in defaults)."""
        get = data.get if isinstance(data, dict) else lambda key, default=None: getattr(data, key, default)
        return cls(
            name=get("name"),
            kind=get("kind"),
            stages=tuple(get("stages") or ()),
            action=get("action"),
            enabled=bool(get("enabled", True)),
            config=dict(get("config") or {}),
            description=get("description", "") or "",
            builtin=bool(get("builtin", False)),
        )

    @property
    def message(self) -> str:
        return self.config.get("message") or f'This was blocked by the "{self.name}" guardrail policy.'

    @property
    def uses_judge(self) -> bool:
        if self.kind == "pii" or not self.config.get("use_llm", False):
            return False
        return self.kind != "topic" or bool(self.config.get("topics"))


@dataclass
class Finding:
    """One policy firing on one text."""

    policy: Policy
    detector: str  # "rules" or "llm"
    findings: dict[str, int]
    spans: list[Match] = field(default_factory=list)  # empty = can't be masked
    reason: str | None = None


@dataclass(frozen=True)
class EventRecord:
    """What's stored about a firing. Never the raw text of a PII finding."""

    policy: str
    kind: str
    stage: str
    action: str
    detector: str
    source: str
    findings: dict[str, int]
    reason: str | None
    excerpt: str | None
    request_id: str | None
    username: str | None


class EventSink(Protocol):
    def record(self, event: EventRecord) -> None: ...


@dataclass
class TextResult:
    text: str  # after masking; unchanged when blocked; a short note when removed (context)
    traces: list[GuardrailTrace] = field(default_factory=list)
    blocked: GuardrailTrace | None = None
    removed: bool = False


@dataclass
class InputResult:
    messages: list[Message]
    traces: list[GuardrailTrace]
    blocked: GuardrailTrace | None


class GuardrailEngine:
    """The policies in force for one request, and how to apply them. Cheap to create."""

    def __init__(
        self,
        policies: list[Policy],
        judge: LLMJudge | None = None,
        *,
        sink: EventSink | None = None,
        fail_closed: bool = False,
        max_scan_chars: int = 100_000,
        username: str | None = None,
    ) -> None:
        self._policies = policies
        self._judge = judge if judge is not None and judge.available else None
        self._sink = sink
        self._fail_closed = fail_closed
        self._max_scan = max_scan_chars
        self._username = username
        self.request_id: str | None = None  # set by ChatService once it has one

    def policies_for(self, stage: str) -> list[Policy]:
        return [p for p in self._policies if p.enabled and stage in p.stages]

    # --- The three stages ---

    async def check_input(self, messages: list[Message]) -> InputResult:
        """The newest user message gets every check; earlier ones (resent each turn) get the rules only,
        and their masking isn't reported again."""
        last = max(i for i, m in enumerate(messages) if m.role == "user")
        checked: list[Message] = []
        traces: list[GuardrailTrace] = []
        for i, message in enumerate(messages):
            if message.role != "user":
                checked.append(message)
                continue
            newest = i == last
            result = await self.check_text(
                message.content,
                "input",
                "user message" if newest else "earlier user message",
                use_judge=newest,
                report=newest,
            )
            if result.blocked is not None:
                return InputResult(messages, [*traces, *result.traces], result.blocked)
            traces.extend(result.traces)
            checked.append(
                message if result.text == message.content else Message(role="user", content=result.text)
            )
        return InputResult(checked, traces, None)

    async def check_context(self, items: list[tuple[str, str]]) -> list[TextResult]:
        """Passages and tool results, as `(source, text)` pairs. The judge sees all of them in one call
        per policy."""
        policies = self.policies_for("context")
        findings: list[list[Finding]] = [
            [f for p in policies if (f := self._rules(p, text))] for _, text in items
        ]
        await asyncio.gather(*(self._judge_batch(p, items, findings) for p in policies if p.uses_judge))
        return [
            self._apply(text, "context", source, item_findings)
            for (source, text), item_findings in zip(items, findings, strict=True)
        ]

    async def check_output(self, text: str) -> TextResult:
        """A finished reply (blocking requests)."""
        return await self.check_text(text, "output", "reply")

    def stream_guard(self) -> "OutputStreamGuard":
        """For streamed replies: masks and blocks as the text arrives."""
        return OutputStreamGuard(self)

    # --- Shared machinery ---

    async def check_text(
        self,
        text: str,
        stage: str,
        source: str,
        *,
        use_judge: bool = True,
        report: bool = True,
        span_rules: bool = True,
        policies: list[Policy] | None = None,
    ) -> TextResult:
        """Every policy of `stage` on one text. `span_rules=False` skips PII/topic rules (the stream guard
        has already applied them)."""
        selected = policies if policies is not None else self.policies_for(stage)
        found: list[Finding] = []
        to_judge: list[Policy] = []
        for policy in selected:
            if not span_rules and policy.kind in SPAN_KINDS:
                if policy.uses_judge and use_judge:
                    to_judge.append(policy)
                continue
            finding = self._rules(policy, text)
            if finding:
                found.append(finding)
            elif policy.uses_judge and use_judge:
                to_judge.append(policy)
        verdicts = await asyncio.gather(*(self._judge_one(p, text) for p in to_judge))
        found.extend(f for f in verdicts if f is not None)
        return self._apply(text, stage, source, found, report=report)

    def _rules(self, policy: Policy, text: str) -> Finding | None:
        scan = text[: self._max_scan]
        config = policy.config
        if policy.kind == "pii":
            spans = find_pii(scan, config.get("entities") or ENTITIES)
            return Finding(policy, "rules", count_types(spans), spans) if spans else None
        if policy.kind == "topic":
            spans = find_topic_terms(scan, config.get("keywords") or [], config.get("patterns") or [])
            return Finding(policy, "rules", count_types(spans), spans) if spans else None
        if policy.kind == "injection":
            hits = find_injection(scan)
            return Finding(policy, "rules", hits) if hits else None
        if policy.kind == "harmful":
            hits = find_harmful(scan, config.get("categories") or HARMFUL_CATEGORIES)
            return Finding(policy, "rules", hits) if hits else None
        return None

    async def _judge_one(self, policy: Policy, text: str) -> Finding | None:
        holder: list[list[Finding]] = [[]]
        await self._judge_batch(policy, [("", text)], holder)
        return holder[0][0] if holder[0] else None

    async def _judge_batch(
        self, policy: Policy, items: list[tuple[str, str]], findings: list[list[Finding]]
    ) -> None:
        """Ask the judge about every item this policy's rules didn't already flag; add its findings."""
        pending = [
            i
            for i, item_findings in enumerate(findings)
            if not any(f.policy is policy for f in item_findings)
        ]
        if not pending or self._judge is None:
            return
        try:
            verdicts = await self._judge.classify(
                policy.kind,
                [items[i][1][: self._max_scan] for i in pending],
                categories=policy.config.get("categories"),
                topics=policy.config.get("topics"),
            )
        except JudgeUnavailableError as exc:
            logger.warning(
                "guardrail judge unavailable", extra={"policy": policy.name, "error_code": str(exc)}
            )
            if self._fail_closed and policy.action != "log":
                for i in pending:
                    findings[i].append(Finding(policy, "llm", {"judge_unavailable": 1}, reason=str(exc)))
            return
        for i, verdict in zip(pending, verdicts, strict=False):
            if verdict.flagged:
                category = verdict.category or policy.kind
                findings[i].append(Finding(policy, "llm", {category: 1}, reason=verdict.reason))

    def _apply(
        self, text: str, stage: str, source: str, found: list[Finding], *, report: bool = True
    ) -> TextResult:
        """Carry out each finding's action on the text, and report what was done."""
        result = TextResult(text=text)
        masks: list[Match] = []
        for finding in found:
            action = finding.policy.action
            if action == "log":
                done = "logged"
            elif action == "redact" and finding.spans:
                masks.extend(finding.spans)
                done = "redacted"
            elif stage == "context":
                result.removed = True
                done = "removed"
            else:
                done = "blocked"
            trace = self._trace(finding, stage, source, done)
            if done == "blocked" and result.blocked is None:
                result.blocked = trace
            if report or done == "blocked":
                result.traces.append(trace)
                self._record(finding, trace, text)
        if result.removed:
            names = sorted({t.policy for t in result.traces if t.action == "removed"})
            result.text = f"[Removed by guardrail policy {', '.join(names)}: not shown to the assistant.]"
        elif masks:
            result.text = redact(text, _without_overlaps(masks))
        return result

    def _trace(self, finding: Finding, stage: str, source: str, action: str) -> GuardrailTrace:
        return GuardrailTrace(
            policy=finding.policy.name,
            kind=finding.policy.kind,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            action=action,  # type: ignore[arg-type]
            detector=finding.detector,  # type: ignore[arg-type]
            source=source,
            findings=finding.findings,
            reason=finding.reason,
            message=finding.policy.message if action == "blocked" else None,
        )

    def _record(self, finding: Finding, trace: GuardrailTrace, text: str) -> None:
        logger.info(
            "guardrail",
            extra={
                "request_id": self.request_id,
                "user": self._username,
                "policy": trace.policy,
                "stage": trace.stage,
                "action": trace.action,
                "detector": trace.detector,
                "findings": trace.findings,
            },
        )
        if self._sink is None:
            return
        self._sink.record(
            EventRecord(
                policy=trace.policy,
                kind=trace.kind,
                stage=trace.stage,
                action=trace.action,
                detector=trace.detector,
                source=trace.source,
                findings=trace.findings,
                reason=trace.reason,
                excerpt=None if trace.kind == "pii" else excerpt(text),
                request_id=self.request_id,
                username=self._username,
            )
        )


def excerpt(text: str) -> str:
    """The start of a text for the event log, with personal data masked."""
    start = text[: EXCERPT_CHARS * 2]
    masked = redact(start, find_pii(start))
    return masked[:EXCERPT_CHARS] + ("…" if len(masked) > EXCERPT_CHARS or len(text) > len(start) else "")


def _without_overlaps(matches: list[Match]) -> list[Match]:
    """Keep the first of any overlapping matches (PII before topic terms, as they come in)."""
    kept: list[Match] = []
    for match in matches:
        if not any(match.start < k.end and k.start < match.end for k in kept):
            kept.append(match)
    return kept


class OutputStreamGuard:
    """Masks and blocks a reply while it streams.

    Text is released a little behind what has arrived: the last HOLDBACK characters, and any word still
    being written, are held, so something split across chunks ("+1 415" … "555 0100") is masked whole.
    PII and topic rules run as text arrives; injection, harmful and judge checks run once on the whole
    reply in `finish()` (if they block then, the caller withdraws what was shown).
    """

    HOLDBACK = 64
    FORCE_RELEASE = 4000  # release even without whitespace once this much is held

    def __init__(self, engine: GuardrailEngine) -> None:
        self._engine = engine
        self._policies = [p for p in engine.policies_for("output") if p.kind in SPAN_KINDS]
        self._pending = ""
        self._raw: list[str] = []
        self._counts: dict[str, dict[str, int]] = {}  # policy → {type: count}, reported at the end
        self.blocked: GuardrailTrace | None = None

    def feed(self, delta: str) -> str:
        """Take a new piece of the reply; give back the text that's safe to show now (maybe "")."""
        if self.blocked is not None:
            return ""
        self._raw.append(delta)
        self._pending += delta
        return self._release(final=False)

    def flush(self) -> str:
        """Release everything held (e.g. before tool calls are shown)."""
        return "" if self.blocked is not None else self._release(final=True)

    async def finish(self) -> tuple[str, list[GuardrailTrace]]:
        """Release the rest, report masking, and run the whole-reply checks. Gives back (tail, traces)."""
        tail = self.flush()
        traces = [self._summary(policy) for policy in self._policies if policy.name in self._counts]
        if self.blocked is not None:
            return "", [*traces, self.blocked]
        result = await self._engine.check_text("".join(self._raw), "output", "reply", span_rules=False)
        if result.blocked is not None:
            self.blocked = result.blocked
        return tail, [*traces, *result.traces]

    def _release(self, *, final: bool) -> str:
        buffer = self._pending
        cut = len(buffer) if final else self._safe_cut(buffer)
        if cut <= 0:
            return ""
        per_policy = [(p, self._engine._rules(p, buffer)) for p in self._policies]
        masks: list[Match] = []
        for policy, finding in per_policy:
            if finding is None:
                continue
            if policy.action == "block":  # anywhere in what has arrived: stop now
                self.blocked = self._engine._trace(finding, "output", "reply", "blocked")
                self._engine._record(finding, self.blocked, buffer)
                self._pending = ""
                return ""
            for match in finding.spans:
                if not final and match.start < cut < match.end:
                    cut = match.start  # don't release half of it
        for policy, finding in per_policy:
            if finding is None:
                continue
            released = [m for m in finding.spans if m.end <= cut]
            if not released:
                continue
            counts = self._counts.setdefault(policy.name, {})
            for kind, n in count_types(released).items():
                counts[kind] = counts.get(kind, 0) + n
            if policy.action == "redact":
                masks.extend(released)
        part, self._pending = buffer[:cut], buffer[cut:]
        return redact(part, _without_overlaps(masks)) if masks else part

    def _safe_cut(self, buffer: str) -> int:
        """How much of the buffer can go out now: up to the last whitespace before the held-back tail."""
        cut = len(buffer) - self.HOLDBACK
        if cut <= 0:
            return 0
        space = max(buffer.rfind(" ", 0, cut), buffer.rfind("\n", 0, cut), buffer.rfind("\t", 0, cut))
        if space >= 0:
            cut = space + 1
        elif len(buffer) < self.FORCE_RELEASE:
            return 0
        begin = buffer.rfind("-----BEGIN", 0, cut)  # a private key block: hold it until it ends
        if begin >= 0 and "-----END" not in buffer[begin:]:
            cut = begin
        return cut

    def _summary(self, policy: Policy) -> GuardrailTrace:
        """One trace (and event) for everything a span policy found in the whole reply."""
        finding = Finding(policy, "rules", self._counts[policy.name])
        action = "redacted" if policy.action == "redact" else "logged"
        trace = self._engine._trace(finding, "output", "reply", action)
        self._engine._record(finding, trace, "")
        return trace
