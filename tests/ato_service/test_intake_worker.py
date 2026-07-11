"""Tests for the long-running intake worker loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, TypeVar
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from ato_service.intake_worker import run_intake_worker_loop
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.synthetic_intake import SyntheticIntakeResult

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def _config(tmp_path: Path):
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": str(tmp_path / "storage"),
        },
        base_dir=tmp_path,
    )


def test_worker_loop_drains_until_shutdown(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls = {"count": 0}

    async def _fake_cycle(*_args, **_kwargs) -> tuple[SyntheticIntakeResult, ...]:
        calls["count"] += 1
        if calls["count"] >= 2:
            return (
                SyntheticIntakeResult(
                    package_revision_id=uuid4(),
                    previous_status="scanning",
                    status="extracting",
                    revision_version=2,
                    artifact_count=1,
                    proposal_count=0,
                ),
            )
        return ()

    with (
        patch(
            "ato_service.intake_worker.create_async_engine_from_url",
            return_value=AsyncMock(),
        ),
        patch(
            "ato_service.intake_worker.create_session_factory",
            return_value=AsyncMock(),
        ),
        patch(
            "ato_service.intake_worker.resolve_runtime_audit_hmac_key",
            return_value=b"audit-key",
        ),
        patch(
            "ato_service.intake_worker.drain_synthetic_intake",
            side_effect=_fake_cycle,
        ),
        patch(
            "ato_service.intake_worker.resolve_database_dsn",
            return_value="postgresql://example",
        ),
    ):
        _run(
            run_intake_worker_loop(
            config,
            dsn="postgresql://example",
            audit_hmac_key=b"audit-key",
            poll_interval_seconds=0.01,
            should_stop=lambda: calls["count"] >= 2,
            now_factory=lambda: datetime.now(timezone.utc),
            )
        )

    assert calls["count"] == 2
