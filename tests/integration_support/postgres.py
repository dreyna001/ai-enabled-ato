"""Deterministic PostgreSQL integration-test harness with transaction rollback."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.blobs import BlobStore
from ato_service.db.session import create_async_engine_from_url
from ato_service.runtime_config import RuntimeConfig, load_runtime_config_from_dict

TEST_DATABASE_URL_ENV = "ATO_TEST_DATABASE_URL"
FIXED_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
HMAC_KEY = b"k" * MIN_AUDIT_HMAC_KEY_BYTES
AUTHORITY_MANIFEST_ID = "authority.v2"
CUSTOMER_ENTERPRISE_ID = "dev-local-enterprise"
ORIGIN = "https://portal.example"

T = TypeVar("T")


def require_test_database_url() -> str:
    """Return the configured integration database URL or skip the current test."""
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not configured")
    return url


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Execute one coroutine from a synchronous pytest test."""
    return asyncio.run(coro)


@dataclass(slots=True)
class PostgresIntegrationHarness:
    """Caller-owned PostgreSQL session wrapped in a rolled-back transaction."""

    engine: AsyncEngine
    session: AsyncSession
    transaction: Any
    tmp_path: Path
    config: RuntimeConfig
    blob_store: BlobStore
    hmac_key: bytes
    project_root: Path
    now: datetime

    @property
    def storage_root(self) -> Path:
        return self.config.storage_data_path


@asynccontextmanager
async def postgres_integration_harness(
    tmp_path: Path,
    *,
    now: datetime | None = None,
) -> AsyncIterator[PostgresIntegrationHarness]:
    """Yield a rolled-back PostgreSQL session and isolated storage directory."""
    url = require_test_database_url()
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    config = load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": str(storage_root),
            "INSTALLATION_CUSTOMER_ENTERPRISE_ID": CUSTOMER_ENTERPRISE_ID,
            "PROCESS_CAPABILITIES": {"package_search": True},
        },
        base_dir=tmp_path,
    )
    project_root = Path(__file__).resolve().parents[2]
    engine = create_async_engine_from_url(url)
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False)
    harness = PostgresIntegrationHarness(
        engine=engine,
        session=session,
        transaction=transaction,
        tmp_path=tmp_path,
        config=config,
        blob_store=BlobStore(storage_root),
        hmac_key=HMAC_KEY,
        project_root=project_root,
        now=now or FIXED_NOW,
    )
    try:
        yield harness
    finally:
        await session.close()
        await transaction.rollback()
        await engine.dispose()
