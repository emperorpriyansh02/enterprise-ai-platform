"""Create MCP tool tables: mcp_servers, mcp_tools (Layer 4).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("transport", sa.String(10), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("env_names", sa.JSON(), nullable=False),
        sa.Column("env_secret", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("header_names", sa.JSON(), nullable=False),
        sa.Column("header_secret", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_roles", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mcp_servers_name", "mcp_servers", ["name"], unique=True)

    op.create_table(
        "mcp_tools",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "server_id", sa.Uuid(), sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("destructive", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),
    )
    op.create_index("ix_mcp_tools_server_id", "mcp_tools", ["server_id"])


def downgrade() -> None:
    op.drop_table("mcp_tools")
    op.drop_table("mcp_servers")
