"""Shared application runtime types used across API modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.runtime_config import RuntimeConfig

RUNTIME_STATE_ATTR = "runtime"


@dataclass(frozen=True, slots=True)
class AppRuntimeSnapshot:
    """Immutable runtime configuration exposed on the application."""

    config: RuntimeConfig
    storage_root: Path
    authority_manifest_id: str
    project_root: Path


@dataclass(slots=True)
class AppRuntimeState:
    """Mutable runtime dependencies exposed on ``app.state.runtime``."""

    snapshot: AppRuntimeSnapshot
    session_factory: async_sessionmaker[AsyncSession] | None = None
    audit_hmac_key: bytes | None = field(default=None, repr=False)

    @property
    def config(self) -> RuntimeConfig:
        return self.snapshot.config

    @property
    def storage_root(self) -> Path:
        return self.snapshot.storage_root

    @property
    def authority_manifest_id(self) -> str:
        return self.snapshot.authority_manifest_id
