"""Tests for the long-running intake worker loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, TypeVar
from unittest.mock import AsyncMock, patch
from uuid import uuid4


from ato_service.intake import IntakeOutcomeKind, IntakeResult, build_intake_lease_owner
from ato_service.intake_worker import run_intake_worker_loop
from ato_service.runtime_config import load_runtime_config_from_dict

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def _config(tmp_path: Path):
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/data",
        },
        base_dir=tmp_path,
    )


def test_worker_loop_drains_until_shutdown(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls = {"count": 0}

    async def _fake_cycle(*_args, **_kwargs) -> tuple[IntakeResult, ...]:
        calls["count"] += 1
        if calls["count"] >= 2:
            return (
                IntakeResult(
                    package_revision_id=uuid4(),
                    work_phase="malware_scan",
                    outcome=IntakeOutcomeKind.COMPLETED,
                    previous_revision_status="scanning",
                    revision_status="extracting",
                    revision_version=3,
                    artifact_count=1,
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
            "ato_service.intake_worker.resolve_malware_scanner",
            return_value=AsyncMock(),
        ),
        patch(
            "ato_service.intake_worker.run_intake_cycle",
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


def test_worker_loop_passes_injected_lease_owner(tmp_path: Path) -> None:
    config = _config(tmp_path)
    captured: dict[str, str] = {}
    iterations = {"count": 0}

    async def _fake_cycle(*_args, **kwargs) -> tuple[IntakeResult, ...]:
        captured["lease_owner"] = kwargs["lease_owner"]
        return ()

    def _should_stop() -> bool:
        iterations["count"] += 1
        return iterations["count"] >= 2

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
            "ato_service.intake_worker.resolve_malware_scanner",
            return_value=AsyncMock(),
        ),
        patch(
            "ato_service.intake_worker.run_intake_cycle",
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
                should_stop=_should_stop,
                lease_owner="injected-owner",
            )
        )

    assert captured["lease_owner"] == "injected-owner"


def test_build_intake_lease_owner_differs_for_distinct_tokens() -> None:
    assert build_intake_lease_owner(token="a") != build_intake_lease_owner(token="b")
