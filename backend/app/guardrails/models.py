"""Database tables for guardrails.

guardrail_policies  one row per policy: what it checks, where (stages), and what it does (action)
guardrail_events    one row each time a policy fired: who, where, what was found (never raw PII)
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.prompts.models import utcnow


class GuardrailPolicyRow(Base):
    __tablename__ = "guardrail_policies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20))  # pii | injection | harmful | topic
    stages: Mapped[list[str]] = mapped_column(JSON, default=list)  # input | context | output
    action: Mapped[str] = mapped_column(String(10))  # block | redact | log
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # kind-specific, see schemas.py
    builtin: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # shipped with the platform; can't be deleted
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GuardrailEventRow(Base):
    __tablename__ = "guardrail_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    policy: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(10))
    action: Mapped[str] = mapped_column(String(10))  # blocked | redacted | removed | logged
    detector: Mapped[str] = mapped_column(String(10))  # rules | llm
    source: Mapped[str] = mapped_column(String(200), default="")  # "user message", "tool demo__get_time"...
    findings: Mapped[dict] = mapped_column(JSON, default=dict)  # {"email": 2} or {"ignore_instructions": 1}
    reason: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)  # start of the text, PII masked; never for PII findings
