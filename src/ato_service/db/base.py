"""SQLAlchemy declarative base for ATO domain tables."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Typed declarative base for PostgreSQL 16 domain tables."""
