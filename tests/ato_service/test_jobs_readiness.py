"""Focused tests for the async jobs readiness probe."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine

from ato_service.db.models import Job
from ato_service.health import READINESS_CHECK_NAMES
from ato_service.readiness import ReadinessDependencies, _check_jobs, run_readiness_checks

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"


def _run(coro):
    return asyncio.run(coro)


def _make_jobs_engine(
    *,
    reconciliation_count: int = 0,
    execute_error: Exception | None = None,
) -> tuple[AsyncEngine, AsyncMock]:
    engine = MagicMock(spec=AsyncEngine)
    connection = AsyncMock()
    if execute_error is not None:
        connection.execute = AsyncMock(side_effect=execute_error)
    else:
        result = MagicMock()
        result.scalar_one.return_value = reconciliation_count
        connection.execute = AsyncMock(return_value=result)
    connect_cm = AsyncMock()
    connect_cm.__aenter__.return_value = connection
    connect_cm.__aexit__.return_value = None
    engine.connect.return_value = connect_cm
    return engine, connection


def _make_deps(
    tmp_path: Path,
    *,
    engine: AsyncEngine | None,
) -> ReadinessDependencies:
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    return ReadinessDependencies(
        config=MagicMock(
            storage_data_path=storage_root,
            document={"schema_version": "1.0.0"},
            runtime_profile="dev_local",
        ),
        authority_manifest_path=DEFAULT_MANIFEST_PATH,
        project_root=ROOT,
        get_engine=lambda: engine,
    )


def test_jobs_ok_when_no_reconciliation_required(tmp_path: Path) -> None:
    engine, _connection = _make_jobs_engine(reconciliation_count=0)
    deps = _make_deps(tmp_path, engine=engine)

    status = _run(_check_jobs(deps))

    assert status == "ok"


def test_jobs_degraded_when_reconciliation_required_present(tmp_path: Path) -> None:
    engine, _connection = _make_jobs_engine(reconciliation_count=2)
    deps = _make_deps(tmp_path, engine=engine)

    status = _run(_check_jobs(deps))

    assert status == "degraded"


def test_jobs_unavailable_when_engine_missing(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, engine=None)

    status = _run(_check_jobs(deps))

    assert status == "unavailable"


@pytest.mark.parametrize(
    "execute_error",
    [
        RuntimeError("relation \"jobs\" does not exist"),
        OSError("connection refused"),
    ],
)
def test_jobs_unavailable_when_query_fails(
    tmp_path: Path,
    execute_error: Exception,
) -> None:
    engine, _connection = _make_jobs_engine(execute_error=execute_error)
    deps = _make_deps(tmp_path, engine=engine)

    checks = _run(run_readiness_checks(deps))

    assert checks["jobs"] == "unavailable"


def test_jobs_executes_sqlalchemy_count_against_jobs_table(tmp_path: Path) -> None:
    engine, connection = _make_jobs_engine(reconciliation_count=0)
    deps = _make_deps(tmp_path, engine=engine)

    _run(_check_jobs(deps))

    connection.execute.assert_awaited_once()
    executed_stmt = connection.execute.await_args.args[0]
    expected_stmt = (
        select(func.count())
        .select_from(Job)
        .where(Job.status == "reconciliation_required")
    )
    assert str(executed_stmt.compile(dialect=postgresql.dialect())) == str(
        expected_stmt.compile(dialect=postgresql.dialect())
    )


def test_run_readiness_checks_preserves_five_key_shape_with_jobs_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _connection = _make_jobs_engine(reconciliation_count=0)
    deps = _make_deps(tmp_path, engine=engine)

    async def _database_ok(_deps: ReadinessDependencies):
        return "ok"

    monkeypatch.setattr("ato_service.readiness._check_database", _database_ok)
    monkeypatch.setattr("ato_service.readiness._check_storage", lambda _deps: "ok")
    monkeypatch.setattr(
        "ato_service.readiness._check_authority_manifest",
        lambda _deps: "ok",
    )
    monkeypatch.setattr(
        "ato_service.readiness._check_configuration",
        lambda _deps: "ok",
    )

    checks = _run(run_readiness_checks(deps))

    assert tuple(checks.keys()) == READINESS_CHECK_NAMES
    assert set(checks) == set(READINESS_CHECK_NAMES)
    assert checks["jobs"] == "ok"
