"""Async dependency readiness checks for the service health boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import os
import secrets
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.db.dsn import DatabaseDsnError, require_database_dsn_from_env
from ato_service.db.models import Job
from ato_service.db.session import probe_database_connectivity
from ato_service.health import CheckStatus, ReadinessChecks, ReadinessProbe
from ato_service.runtime_config import (
    RuntimeConfig,
    RuntimeConfigError,
    resolve_runtime_database_dsn,
)

_TEMP_DIR_NAME = "_tmp"
_JOB_RECONCILIATION_REQUIRED_STATUS = "reconciliation_required"


@dataclass(frozen=True, slots=True)
class ReadinessDependencies:
    """Runtime objects required to evaluate readiness checks."""

    config: RuntimeConfig
    authority_manifest_path: Path
    project_root: Path
    get_engine: Callable[[], AsyncEngine | None]


async def run_readiness_checks(deps: ReadinessDependencies) -> ReadinessChecks:
    """Evaluate all readiness checks and always return the five required statuses."""
    database = await _run_async_check(lambda: _check_database(deps))
    storage = await _run_sync_check(lambda: _check_storage(deps))
    authority_manifest = await _run_sync_check(
        lambda: _check_authority_manifest(deps)
    )
    jobs = await _run_async_check(lambda: _check_jobs(deps))
    configuration = await _run_sync_check(lambda: _check_configuration(deps))

    return {
        "database": database,
        "storage": storage,
        "authority_manifest": authority_manifest,
        "jobs": jobs,
        "configuration": configuration,
    }


def create_readiness_probe(deps: ReadinessDependencies) -> ReadinessProbe:
    """Build an async readiness probe for the health router."""

    async def probe() -> ReadinessChecks:
        return await run_readiness_checks(deps)

    return probe


async def _run_async_check(
    check: Callable[[], Awaitable[CheckStatus]],
) -> CheckStatus:
    try:
        return await check()
    except Exception:
        return "unavailable"


async def _run_sync_check(check: Callable[[], CheckStatus]) -> CheckStatus:
    try:
        return await asyncio.to_thread(check)
    except Exception:
        return "unavailable"


async def _check_database(deps: ReadinessDependencies) -> CheckStatus:
    try:
        engine = deps.get_engine()
        if engine is None:
            return "unavailable"
        await probe_database_connectivity(engine)
        return "ok"
    except Exception:
        return "unavailable"


def _check_storage(deps: ReadinessDependencies) -> CheckStatus:
    storage_root = deps.config.storage_data_path
    if not storage_root.is_dir():
        return "unavailable"
    temp_dir = storage_root / _TEMP_DIR_NAME
    probe_path: Path | None = None
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        probe_path = temp_dir / f"readiness-{secrets.token_hex(8)}"
        with probe_path.open("xb") as probe_file:
            probe_file.write(b"x")
            probe_file.flush()
            os.fsync(probe_file.fileno())
        return "ok"
    except (OSError, PermissionError):
        return "unavailable"
    finally:
        if probe_path is not None and probe_path.exists():
            try:
                probe_path.unlink()
            except OSError:
                pass


def _check_authority_manifest(deps: ReadinessDependencies) -> CheckStatus:
    try:
        manifest = verify_authority_manifest(
            deps.authority_manifest_path,
            project_root=deps.project_root,
        )
    except AuthorityManifestVerificationError:
        return "unavailable"

    if manifest.get("status") != "approved":
        return "degraded"
    return "ok"


async def _check_jobs(deps: ReadinessDependencies) -> CheckStatus:
    engine = deps.get_engine()
    if engine is None:
        return "unavailable"

    stmt = (
        select(func.count())
        .select_from(Job)
        .where(Job.status == _JOB_RECONCILIATION_REQUIRED_STATUS)
    )
    async with engine.connect() as connection:
        result = await connection.execute(stmt)
        reconciliation_required_count = result.scalar_one()
    if reconciliation_required_count > 0:
        return "degraded"
    return "ok"


def _check_configuration(deps: ReadinessDependencies) -> CheckStatus:
    try:
        document = deps.config.document
        if document.get("schema_version") != "1.0.0":
            return "unavailable"
        if not deps.config.storage_data_path.is_absolute():
            return "unavailable"
        if not deps.authority_manifest_path.is_file():
            return "unavailable"
        _verify_database_dsn_reference(deps.config)
        return "ok"
    except (RuntimeConfigError, DatabaseDsnError, OSError, ValueError):
        return "unavailable"
    except Exception:
        return "unavailable"


def _verify_database_dsn_reference(config: RuntimeConfig) -> None:
    reference = config.document.get("DATABASE_DSN_CREDENTIAL_REFERENCE")
    if isinstance(reference, dict):
        resolve_runtime_database_dsn(config)
        return
    if config.runtime_profile == "dev_local":
        require_database_dsn_from_env()
        return
    raise RuntimeConfigError(
        "DATABASE_DSN_CREDENTIAL_REFERENCE is required to resolve the database DSN"
    )
