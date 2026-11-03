"""Database tables for MCP tools.

mcp_servers  one row per registered MCP server (how to reach it; secrets encrypted)
mcp_tools    the tools a server offers, as last seen, and whether an admin switched each one on
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.prompts.models import utcnow


class MCPServerRow(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(String(10))  # "stdio" or "http"
    command: Mapped[str | None] = mapped_column(Text)  # stdio
    args: Mapped[list[str]] = mapped_column(JSON, default=list)  # stdio
    env_names: Mapped[list[str]] = mapped_column(JSON, default=list)  # shown in the UI; values are secret
    env_secret: Mapped[str | None] = mapped_column(Text)  # encrypted {"NAME": "value"}
    url: Mapped[str | None] = mapped_column(Text)  # http
    header_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    header_secret: Mapped[str | None] = mapped_column(Text)  # encrypted {"Authorization": "..."}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Roles allowed to use this server's tools; empty = everyone (enforced from Layer 5).
    allowed_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MCPToolRow(Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)  # server says: doesn't change anything
    destructive: Mapped[bool] = mapped_column(Boolean, default=False)  # server says: may delete/overwrite
    # Off until an admin switches it on. Only enabled tools are ever offered to a model.
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
