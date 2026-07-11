"""Runtime tests for the deterministic analyzer worker process."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.deterministic_analyzer import DeterministicAnalysisResult
from ato_service.deterministic_analyzer_worker import (
    drain_deterministic_analysis,
    run_deterministic_analyzer_worker,
)
from ato_service.runtime_config import load_runtime_config_from_dict

NOW = datetime(2026, 7, 11, 18, 0, 0, tzinfo=timezone.utc)
RUN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _config(tmp_path: Path) -> Any:
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": str(tmp_path / "storage"),
        },
        base_dir=tmp_path,
    )


@patch(
    "ato_service.deterministic_analyzer_worker.process_next_deterministic_analysis_job",
    new_callable=AsyncMock,
)
def test_drain_stops_when_no_job_claimed(mock_process: AsyncMock, tmp_path: Path) -> None:
    mock_process.side_effect = [None]

    async def exercise() -> tuple[DeterministicAnalysisResult, ...]:
        sessions = [MagicMock(name="session-0")]

        @asynccontextmanager
        async def fake_session_scope(_session_factory: Any) -> AsyncIterator[Any]:
            yield sessions[0]

        with patch(
            "ato_service.deterministic_analyzer_worker.session_scope",
            fake_session_scope,
        ):
            return await drain_deterministic_analysis(
                MagicMock(),
                storage_root=tmp_path,
                project_root=tmp_path,
                hmac_key=b"audit-test-key",
                now_factory=lambda: NOW,
            )

    processed = asyncio.run(exercise())
    assert processed == ()
    assert mock_process.await_count == 1


@patch(
    "ato_service.deterministic_analyzer_worker.drain_deterministic_analysis",
    new_callable=AsyncMock,
)
def test_worker_resolves_dependencies_and_disposes_engine(
    mock_drain: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_drain.return_value = (
        DeterministicAnalysisResult(
            run_id=RUN_ID,
            package_revision_id=uuid.uuid4(),
            matrix_row_count=3,
            artifact_manifest_sha256="a" * 64,
        ),
    )

    async def exercise() -> tuple[DeterministicAnalysisResult, ...]:
        engine = MagicMock()
        engine.dispose = AsyncMock()
        with patch(
            "ato_service.deterministic_analyzer_worker.create_async_engine_from_url",
            return_value=engine,
        ):
            return await run_deterministic_analyzer_worker(
                _config(tmp_path),
                dsn="postgresql+asyncpg://ato:secret@localhost/ato",
                audit_hmac_key=b"audit-test-key",
                project_root=tmp_path,
            )

    processed = asyncio.run(exercise())
    assert len(processed) == 1
    assert processed[0].matrix_row_count == 3
