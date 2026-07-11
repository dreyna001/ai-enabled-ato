"""Async PostgreSQL engine and session factory helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


_POSTGRESQL_SCHEMES = frozenset({"postgresql", "postgres"})


class DatabaseConfigurationError(ValueError):
    """Raised when a database URL or configuration is invalid."""


def require_postgresql_url(url: str) -> str:
    """Validate that ``url`` targets PostgreSQL and return it unchanged."""
    if not url or not url.strip():
        raise DatabaseConfigurationError("database URL must be a non-empty string")

    parsed = urlsplit(url.strip())
    driver = parsed.scheme.lower()
    if "+" in driver:
        base_scheme, _async_driver = driver.split("+", 1)
    else:
        base_scheme = driver

    if base_scheme not in _POSTGRESQL_SCHEMES:
        raise DatabaseConfigurationError(
            f"unsupported database scheme {parsed.scheme!r}; PostgreSQL is required"
        )
    return url.strip()


def create_async_engine_from_url(
    url: str,
    *,
    echo: bool = False,
    **engine_kwargs: Any,
) -> AsyncEngine:
    """Create a non-global async engine for an explicit PostgreSQL URL."""
    validated_url = require_postgresql_url(url)
    options = {"pool_pre_ping": True, "echo": echo}
    options.update(engine_kwargs)
    return create_async_engine(validated_url, **options)


def create_session_factory(
    engine: AsyncEngine,
    *,
    expire_on_commit: bool = False,
) -> async_sessionmaker[AsyncSession]:
    """Build an async session factory bound to ``engine`` without connecting."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=expire_on_commit,
    )


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session with commit/rollback handling."""
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def probe_database_connectivity(engine: AsyncEngine) -> None:
    """Verify database reachability with a focused ``SELECT 1`` probe."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
