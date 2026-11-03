"""Create guardrail tables: guardrail_policies, guardrail_events (Layer 6).

The built-in policies are added by the app on startup (app/guardrails/defaults.py), not here, so
new built-ins in later versions appear without a migration.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guardrail_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_guardrail_policies_name", "guardrail_policies", ["name"], unique=True)

    op.create_table(
        "guardrail_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("policy", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(10), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("detector", sa.String(10), nullable=False),
        sa.Column("source", sa.String(200), nullable=False, server_default=""),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
    )
    op.create_index("ix_guardrail_events_created_at", "guardrail_events", ["created_at"])
    op.create_index("ix_guardrail_events_request_id", "guardrail_events", ["request_id"])
    op.create_index("ix_guardrail_events_policy", "guardrail_events", ["policy"])


def downgrade() -> None:
    op.drop_table("guardrail_events")
    op.drop_table("guardrail_policies")
