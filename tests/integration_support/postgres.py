"""Deterministic PostgreSQL integration-test harness with transaction rollback."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.blobs import BlobStore
from ato_service.db.base import Base
from ato_service.db.session import create_async_engine_from_url, create_session_factory
from ato_service.runtime_config import RuntimeConfig, load_runtime_config_from_dict

import ato_service.db.models  # noqa: F401

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
    """PostgreSQL integration session with explicit fixture isolation."""

    engine: AsyncEngine
    session: AsyncSession
    transaction: Any
    tmp_path: Path
    config: RuntimeConfig
    blob_store: BlobStore
    hmac_key: bytes
    project_root: Path
    now: datetime
    isolated_schema: str | None = None

    @property
    def storage_root(self) -> Path:
        return self.config.storage_data_path


@asynccontextmanager
async def postgres_integration_harness(
    tmp_path: Path,
    *,
    now: datetime | None = None,
    ordinary_session: bool = False,
) -> AsyncIterator[PostgresIntegrationHarness]:
    """Yield an isolated PostgreSQL session and storage directory.

    The default external transaction keeps legacy integration fixtures fast and
    disposable.  Boundary tests that prove a service ends its own root
    transaction opt into ordinary engine-bound sessions and a temporary schema,
    so service rollbacks cannot erase committed setup data.
    """
    url = require_test_database_url()
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    config = load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/storage",
            "INSTALLATION_CUSTOMER_ENTERPRISE_ID": CUSTOMER_ENTERPRISE_ID,
            "PROCESS_CAPABILITIES": {
                "api": True,
                "intake_worker": True,
                "analyzer_worker": True,
                "portal_static": False,
                "malware_scanning": False,
                "text_model_calls": True,
                "vision_model_calls": False,
                "oidc_authentication": False,
                "package_search": True,
                "package_chat": False,
            },
            "TEXT_MODEL_ENDPOINT_PROFILE": "mock",
            "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": True,
        },
        base_dir=tmp_path,
    )
    assert config.storage_data_path == storage_root
    project_root = Path(__file__).resolve().parents[2]
    engine = create_async_engine_from_url(url)
    connection = None
    transaction = None
    isolated_schema = None
    if ordinary_session:
        isolated_schema = f"ato_harness_{uuid.uuid4().hex}"
        async with engine.begin() as setup_connection:
            await setup_connection.execute(
                text(f'CREATE SCHEMA "{isolated_schema}"')
            )
            await setup_connection.execute(
                text(f'SET search_path TO "{isolated_schema}"')
            )
            await setup_connection.run_sync(Base.metadata.create_all)
        session = create_session_factory(engine)()
        await session.execute(text(f'SET search_path TO "{isolated_schema}"'))
        await session.commit()
    else:
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
        isolated_schema=isolated_schema,
    )
    try:
        yield harness
    finally:
        await session.close()
        if transaction is not None and transaction.is_active:
            await transaction.rollback()
        if connection is not None:
            await connection.close()
        if isolated_schema is not None:
            async with engine.begin() as cleanup_connection:
                await cleanup_connection.execute(
                    text(f'DROP SCHEMA "{isolated_schema}" CASCADE')
                )
        await engine.dispose()
