"""Runtime tests for the bounded synthetic intake worker alias."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.blobs import BlobStore
from ato_service.intake import IntakeOutcomeKind, IntakeResult
from ato_service.malware_scan import MalwareScannerUnavailableError
from ato_service.runtime_config import (
    RuntimeConfigError,
    load_runtime_config_from_dict,
)
from ato_service.synthetic_intake_worker import (
    drain_synthetic_intake,
    main,
    run_synthetic_intake_worker,
)

NOW = datetime(2026, 7, 11, 17, 0, 0, tzinfo=timezone.utc)
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")

def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _config(tmp_path: Path) -> Any:
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/data",
        },
        base_dir=tmp_path,
    )


def _result(*, work_phase: str, revision_status: str, revision_version: int) -> IntakeResult:
    return IntakeResult(
        package_revision_id=REVISION_ID,
        work_phase=work_phase,
        outcome=IntakeOutcomeKind.COMPLETED,
        previous_revision_status=(
            "scanning" if revision_status == "extracting" else "extracting"
        ),
        revision_status=revision_status,
        revision_version=revision_version,
        artifact_count=1,
        draft_inserted=revision_status == "awaiting_confirmation",
    )


@patch(
    "ato_service.synthetic_intake_worker.drain_intake",
    new_callable=AsyncMock,
)
def test_drain_delegates_to_unified_intake(
    mock_drain: AsyncMock,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    scan = _result(
        work_phase="malware_scan",
        revision_status="extracting",
        revision_version=3,
    )
    extraction = _result(
        work_phase="deterministic_extract",
        revision_status="awaiting_confirmation",
        revision_version=4,
    )
    mock_drain.return_value = (scan, extraction)

    results = _run(
        drain_synthetic_intake(
            MagicMock(),
            blob_store=BlobStore(tmp_path),
            hmac_key=b"x" * 32,
            config=config,
            now_factory=lambda: NOW,
        )
    )

    assert results == (scan, extraction)
    mock_drain.assert_awaited_once()
    assert mock_drain.await_args.kwargs["config"] == config


@patch(
    "ato_service.synthetic_intake_worker.drain_intake",
    new_callable=AsyncMock,
)
def test_drain_synthetic_intake_uses_oneshot_lease_owner(
    mock_drain: AsyncMock,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    mock_drain.return_value = ()

    _run(
        drain_synthetic_intake(
            MagicMock(),
            blob_store=BlobStore(tmp_path),
            hmac_key=b"x" * 32,
            config=config,
            now_factory=lambda: NOW,
        )
    )

    lease_owner = mock_drain.await_args.kwargs["lease_owner"]
    assert lease_owner.startswith("intake-")
    assert "oneshot" in lease_owner


@patch(
    "ato_service.synthetic_intake_worker.drain_synthetic_intake",
    new_callable=AsyncMock,
)
@patch("ato_service.synthetic_intake_worker.create_session_factory")
@patch("ato_service.synthetic_intake_worker.create_async_engine_from_url")
def test_worker_resolves_dependencies_and_disposes_engine(
    mock_engine_factory: MagicMock,
    mock_session_factory: MagicMock,
    mock_drain: AsyncMock,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    engine = MagicMock()
    engine.dispose = AsyncMock()
    mock_engine_factory.return_value = engine
    sessions = MagicMock()
    mock_session_factory.return_value = sessions
    expected = (
        _result(
            work_phase="malware_scan",
            revision_status="extracting",
            revision_version=3,
        ),
    )
    mock_drain.return_value = expected

    result = _run(
        run_synthetic_intake_worker(
            config,
            dsn="postgresql+asyncpg://example.test/ato",
            audit_hmac_key=b"x" * 32,
            now_factory=lambda: NOW,
        )
    )

    assert result == expected
    mock_engine_factory.assert_called_once_with(
        "postgresql+asyncpg://example.test/ato"
    )
    mock_session_factory.assert_called_once_with(engine)
    mock_drain.assert_awaited_once()
    assert mock_drain.await_args.args == (sessions,)
    assert mock_drain.await_args.kwargs["blob_store"].storage_root == (
        tmp_path / "data"
    )
    engine.dispose.assert_awaited_once()


@patch("ato_service.synthetic_intake_worker.create_async_engine_from_url")
def test_worker_rejects_production_before_creating_engine(
    mock_engine_factory: MagicMock,
) -> None:
    config = MagicMock(runtime_profile="onprem_production")
    with pytest.raises(MalwareScannerUnavailableError):
        _run(
            run_synthetic_intake_worker(
                config,
                dsn="postgresql+asyncpg://example.test/ato",
                audit_hmac_key=b"x" * 32,
            )
        )
    mock_engine_factory.assert_not_called()


@patch("ato_service.synthetic_intake_worker.create_async_engine_from_url")
def test_worker_requires_audit_credential_before_creating_engine(
    mock_engine_factory: MagicMock,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with pytest.raises(
        RuntimeConfigError,
        match="AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE",
    ):
        _run(
            run_synthetic_intake_worker(
                config,
                dsn="postgresql+asyncpg://example.test/ato",
            )
        )
    mock_engine_factory.assert_not_called()


def test_main_requires_explicit_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ATO_RUNTIME_CONFIG_PATH", raising=False)
    with pytest.raises(SystemExit, match="ATO_RUNTIME_CONFIG_PATH"):
        main([])
