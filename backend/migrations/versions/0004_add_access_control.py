"""Access control (Layer 5): who may use a collection, and who created collections and documents.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing collections stay open to everyone (empty list).
    op.add_column(
        "collections", sa.Column("allowed_roles", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.add_column("collections", sa.Column("created_by", sa.String(255), nullable=True))
    op.add_column("documents", sa.Column("created_by", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "created_by")
    op.drop_column("collections", "created_by")
    op.drop_column("collections", "allowed_roles")
