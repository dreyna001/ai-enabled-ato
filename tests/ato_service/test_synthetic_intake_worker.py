"""Runtime tests for the bounded synthetic intake worker process."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.blobs import BlobStore
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.synthetic_intake import (
    SyntheticIntakeConfigurationError,
    SyntheticIntakeResult,
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


def _transition(status: str) -> SyntheticIntakeResult:
    return SyntheticIntakeResult(
        package_revision_id=REVISION_ID,
        previous_status=(
            "scanning" if status == "extracting" else "extracting"
        ),
        status=status,
        revision_version=3 if status == "extracting" else 4,
        artifact_count=1,
        proposal_count=0 if status == "extracting" else 2,
    )


@patch(
    "ato_service.synthetic_intake_worker.process_next_synthetic_intake",
    new_callable=AsyncMock,
)
def test_drain_uses_one_transaction_per_transition_and_stops_when_idle(
    mock_process: AsyncMock,
    tmp_path: Path,
) -> None:
    scan = _transition("extracting")
    extraction = _transition("awaiting_confirmation")
    mock_process.side_effect = [scan, extraction, None]
    sessions = [MagicMock(name=f"session-{index}") for index in range(3)]
    scope_calls: list[Any] = []

    @asynccontextmanager
    async def fake_session_scope(_session_factory: Any) -> AsyncIterator[Any]:
        session = sessions[len(scope_calls)]
        scope_calls.append(session)
        yield session

    with patch(
        "ato_service.synthetic_intake_worker.session_scope",
        side_effect=fake_session_scope,
    ):
        results = _run(
            drain_synthetic_intake(
                MagicMock(),
                blob_store=BlobStore(tmp_path),
                hmac_key=b"x" * 32,
                now_factory=lambda: NOW,
            )
        )

    assert results == (scan, extraction)
    assert scope_calls == sessions
    assert [call.args[0] for call in mock_process.await_args_list] == sessions
    assert all(call.kwargs["now"] == NOW for call in mock_process.await_args_list)


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
    expected = (_transition("extracting"),)
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
    with pytest.raises(SyntheticIntakeConfigurationError):
        _run(
            run_synthetic_intake_worker(
                config,
                dsn="postgresql+asyncpg://example.test/ato",
                audit_hmac_key=b"x" * 32,
            )
        )
    mock_engine_factory.assert_not_called()


def test_main_requires_explicit_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ATO_RUNTIME_CONFIG_PATH", raising=False)
    with pytest.raises(SystemExit, match="ATO_RUNTIME_CONFIG_PATH"):
        main([])
