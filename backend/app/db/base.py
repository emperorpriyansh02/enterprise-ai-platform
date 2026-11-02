"""Base class for all database tables. Alembic reads `Base.metadata` to build migrations."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
