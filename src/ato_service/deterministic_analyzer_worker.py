"""Bounded command-line worker for dev_local deterministic analysis runs."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.db.models import AnalysisRun, PackageRevision
from ato_service.db.session import (
    create_async_engine_from_url,
    create_session_factory,
    session_scope,
)
from ato_service.deterministic_analyzer import (
    DeterministicAnalysisProcessingError,
    DeterministicAnalysisResult,
    process_next_deterministic_analysis,
    require_deterministic_analyzer_runtime,
)
from ato_service.jobs import (
    claim_next_eligible_job,
    record_job_failure,
)
from ato_service.lifecycle_transitions import AnalysisRunStatus
from ato_service.main import RUNTIME_CONFIG_PATH_ENV_VAR, resolve_database_dsn
from ato_service.runtime_config import (
    RuntimeConfig,
    load_runtime_config,
    resolve_runtime_audit_hmac_key,
)

DEFAULT_LEASE_OWNER = "deterministic-analyzer-worker"
DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 3

UtcNowFactory = Callable[[], datetime]


async def process_next_deterministic_analysis_job(
    session: AsyncSession,
    *,
    storage_root: Path,
    project_root: Path,
    hmac_key: bytes,
    lease_owner: str,
    now: datetime,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> DeterministicAnalysisResult | None:
    """Claim and execute one deterministic analysis job when eligible."""
    claimed = await claim_next_eligible_job(
        session,
        lease_owner=lease_owner,
        now=now,
        max_attempts=max_attempts,
        lease_seconds=lease_seconds,
    )
    if claimed is None:
        return None

    run_result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.run_id == claimed.job.run_id)
    )
    analysis_run = run_result.scalar_one_or_none()
    if analysis_run is None:
        raise DeterministicAnalysisProcessingError(
            "claimed job references a missing analysis run",
            error_code="reconciliation_required",
        )

    if AnalysisRunStatus(analysis_run.status) == AnalysisRunStatus.CANCELLED:
        await record_job_failure(
            session,
            job_id=claimed.job.job_id,
            lease_owner=lease_owner,
            now=now,
            error_code="illegal_state_transition",
            transport_retryable=False,
            max_attempts=max_attempts,
        )
        return None

    revision_result = await session.execute(
        select(PackageRevision).where(
            PackageRevision.package_revision_id == analysis_run.package_revision_id
        )
    )
    package_revision = revision_result.scalar_one_or_none()
    if package_revision is None:
        await record_job_failure(
            session,
            job_id=claimed.job.job_id,
            lease_owner=lease_owner,
            now=now,
            error_code="resource_not_found",
            transport_retryable=False,
            max_attempts=max_attempts,
        )
        return None

    try:
        return await process_next_deterministic_analysis(
            session,
            claimed=claimed,
            package_revision=package_revision,
            analysis_run=analysis_run,
            storage_root=storage_root,
            project_root=project_root,
            hmac_key=hmac_key,
            now=now,
        )
    except DeterministicAnalysisProcessingError as exc:
        await record_job_failure(
            session,
            job_id=claimed.job.job_id,
            lease_owner=lease_owner,
            now=now,
            error_code=exc.error_code,
            transport_retryable=exc.retryable,
            max_attempts=max_attempts,
        )
        return None


async def drain_deterministic_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    storage_root: Path,
    project_root: Path,
    hmac_key: bytes,
    lease_owner: str = DEFAULT_LEASE_OWNER,
    now_factory: UtcNowFactory | None = None,
) -> tuple[DeterministicAnalysisResult, ...]:
    """Process currently eligible deterministic analysis jobs until idle."""
    current_time = now_factory or (lambda: datetime.now(timezone.utc))
    processed: list[DeterministicAnalysisResult] = []
    while True:
        async with session_scope(session_factory) as session:
            result = await process_next_deterministic_analysis_job(
                session,
                storage_root=storage_root,
                project_root=project_root,
                hmac_key=hmac_key,
                lease_owner=lease_owner,
                now=current_time(),
            )
        if result is None:
            return tuple(processed)
        processed.append(result)


async def run_deterministic_analyzer_worker(
    config: RuntimeConfig,
    *,
    dsn: str | None = None,
    audit_hmac_key: bytes | None = None,
    project_root: Path | None = None,
    now_factory: UtcNowFactory | None = None,
) -> tuple[DeterministicAnalysisResult, ...]:
    """Resolve dependencies, drain deterministic analysis jobs, and dispose the pool."""
    require_deterministic_analyzer_runtime(config)
    resolved_dsn = dsn if dsn is not None else resolve_database_dsn(config)
    resolved_audit_hmac_key = (
        audit_hmac_key
        if audit_hmac_key is not None
        else resolve_runtime_audit_hmac_key(config)
    )
    resolved_project_root = project_root or Path(__file__).resolve().parents[2]
    engine = create_async_engine_from_url(resolved_dsn)
    session_factory = create_session_factory(engine)
    try:
        return await drain_deterministic_analysis(
            session_factory,
            storage_root=config.storage_data_path,
            project_root=resolved_project_root,
            hmac_key=resolved_audit_hmac_key,
            now_factory=now_factory,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    """Drain currently eligible deterministic analysis jobs and exit."""
    parser = argparse.ArgumentParser(
        description=(
            "Process dev_local deterministic_only analysis runs for ready "
            "synthetic JSON package revisions"
        )
    )
    parser.add_argument(
        "--config",
        default=os.environ.get(RUNTIME_CONFIG_PATH_ENV_VAR),
        help=f"Runtime config JSON path (default: {RUNTIME_CONFIG_PATH_ENV_VAR})",
    )
    args = parser.parse_args(argv)
    if not args.config or not str(args.config).strip():
        raise SystemExit(
            f"{RUNTIME_CONFIG_PATH_ENV_VAR} or --config must point to runtime config JSON"
        )

    config = load_runtime_config(Path(args.config))
    processed = asyncio.run(run_deterministic_analyzer_worker(config))
    print(f"processed {len(processed)} deterministic analysis job(s)")


if __name__ == "__main__":
    main()


async def process_next_deterministic_job(
    session: AsyncSession,
    *,
    config: RuntimeConfig,
    storage_root: Path,
    project_root: Path,
    hmac_key: bytes,
    now: datetime,
    lease_owner: str = DEFAULT_LEASE_OWNER,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> DeterministicAnalysisResult | None:
    """Compatibility entry point used by integration tests."""
    return await process_next_deterministic_analysis_job(
        session,
        storage_root=storage_root,
        project_root=project_root,
        hmac_key=hmac_key,
        lease_owner=lease_owner,
        now=now,
        max_attempts=max_attempts,
        lease_seconds=lease_seconds,
    )


__all__ = [
    "DEFAULT_LEASE_OWNER",
    "drain_deterministic_analysis",
    "main",
    "process_next_deterministic_analysis_job",
    "process_next_deterministic_job",
    "run_deterministic_analyzer_worker",
]
