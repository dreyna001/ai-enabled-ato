"""FastAPI dependency helpers for runtime state, database sessions, and storage."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.blobs import BlobStore
from ato_service.db.session import session_scope
from ato_service.main import AppRuntimeState, RUNTIME_STATE_ATTR


class RuntimeDependencyError(RuntimeError):
    """Base error for API runtime dependency resolution failures."""


class RuntimeStateUnavailableError(RuntimeDependencyError):
    """Raised when application runtime state is missing or unavailable."""


class DatabaseSessionUnavailableError(RuntimeDependencyError):
    """Raised when the database session factory is not available."""


class AuditDependencyUnavailableError(RuntimeDependencyError):
    """Raised when the audit HMAC key dependency is not configured."""


def get_runtime_state(request: Request) -> AppRuntimeState:
    """Return the typed runtime state attached during application startup."""
    state = getattr(request.app.state, RUNTIME_STATE_ATTR, None)
    if not isinstance(state, AppRuntimeState):
        raise RuntimeStateUnavailableError(
            "application runtime state is not available"
        )
    return state


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session with commit/rollback handling."""
    runtime_state = get_runtime_state(request)
    session_factory = runtime_state.session_factory
    if session_factory is None:
        raise DatabaseSessionUnavailableError(
            "database session factory is not available"
        )

    async with session_scope(session_factory) as session:
        yield session


def get_blob_store(request: Request) -> BlobStore:
    """Return blob storage rooted at the configured runtime storage path."""
    runtime_state = get_runtime_state(request)
    return BlobStore(runtime_state.storage_root)


def get_audit_hmac_key(request: Request) -> bytes:
    """Return the in-memory audit HMAC key or fail closed when absent."""
    runtime_state = get_runtime_state(request)
    audit_hmac_key = runtime_state.audit_hmac_key
    if audit_hmac_key is None:
        raise AuditDependencyUnavailableError(
            "audit HMAC key is not configured"
        )
    return audit_hmac_key
