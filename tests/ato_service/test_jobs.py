"""Focused tests for the lease-safe async job repository."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.db.models import AnalysisRun, Job, JobAttempt
from ato_service.jobs import (
    ERROR_DEPENDENCY_ATTEMPTS_EXHAUSTED,
    ERROR_JOB_LEASE_LOST,
    ERROR_RUN_DEADLINE_EXCEEDED,
    ClaimedJob,
    JobAttemptStatus,
    JobInvariantError,
    JobLeaseLostError,
    JobStatus,
    _claim_select_statement,
    _recover_select_statement,
    claim_next_eligible_job,
    complete_job,
    heartbeat_job,
    record_job_failure,
    recover_expired_leases,
)
from ato_service.lifecycle_transitions import AnalysisRunStatus

UTC = timezone.utc
NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
OWNER = "worker-a"
RUN_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ATTEMPT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _compile_sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _make_job(
    *,
    status: str = JobStatus.AVAILABLE.value,
    attempt_count: int = 0,
    available_at: datetime = NOW,
    step_idempotent: bool = True,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
) -> Job:
    return Job(
        job_id=JOB_ID,
        run_id=RUN_ID,
        step_key="normalize_proposal",
        step_idempotent=step_idempotent,
        status=status,
        attempt_count=attempt_count,
        available_at=available_at,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        heartbeat_at=heartbeat_at,
        last_error_code=None,
    )


def _make_run(*, status: str = AnalysisRunStatus.QUEUED.value) -> AnalysisRun:
    return AnalysisRun(
        run_id=RUN_ID,
        package_revision_id=uuid.uuid4(),
        parent_run_id=None,
        run_type="full",
        status=status,
        requested_by="operator",
        requested_at=NOW,
        started_at=None,
        completed_at=None,
        authority_manifest_id="authority-manifest-1",
        analysis_profile_sha256="a" * 64,
        config_fingerprint="b" * 64,
        prompt_bundle_sha256="c" * 64,
        model_profile="default",
        artifact_manifest_sha256=None,
        llm_call_count=0,
        error_code=None,
        error_retryable=None,
    )


def _make_attempt(
    *,
    status: str = JobAttemptStatus.ACTIVE.value,
    attempt_number: int = 1,
    lease_owner: str = OWNER,
) -> JobAttempt:
    return JobAttempt(
        attempt_id=ATTEMPT_ID,
        job_id=JOB_ID,
        run_id=RUN_ID,
        step_key="normalize_proposal",
        attempt_number=attempt_number,
        status=status,
        lease_owner=lease_owner,
        started_at=NOW,
        completed_at=None,
        error_code=None,
        error_retryable=None,
    )


class _RecordingSession:
    def __init__(self, execute_results: list[MagicMock]) -> None:
        self._execute_results = list(execute_results)
        self.added: list[object] = []
        self.execute_calls: list[object] = []

    async def execute(self, statement: object) -> MagicMock:
        self.execute_calls.append(statement)
        if not self._execute_results:
            raise AssertionError("unexpected execute call")
        return self._execute_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    return result


def _scalars_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _job_snapshot(job: Job) -> dict[str, object]:
    return {
        "status": job.status,
        "attempt_count": job.attempt_count,
        "available_at": job.available_at,
        "lease_owner": job.lease_owner,
        "lease_expires_at": job.lease_expires_at,
        "heartbeat_at": job.heartbeat_at,
        "last_error_code": job.last_error_code,
    }


def _attempt_snapshot(attempt: JobAttempt) -> dict[str, object]:
    return {
        "status": attempt.status,
        "completed_at": attempt.completed_at,
        "error_code": attempt.error_code,
        "error_retryable": attempt.error_retryable,
    }


def test_claim_sql_uses_skip_locked_and_deterministic_ordering() -> None:
    sql = _compile_sql(_claim_select_statement(now=NOW, max_attempts=3))

    assert "SKIP LOCKED" in sql
    assert "FOR UPDATE OF jobs" in sql
    assert "ORDER BY jobs.available_at ASC, jobs.job_id ASC" in sql
    assert "jobs.status = 'available'" in sql
    assert "jobs.available_at <=" in sql
    assert "jobs.attempt_count < 3" in sql
    assert "analysis_runs.status IN ('queued', 'running')" in sql
    assert "run_steps" in sql


def test_recover_sql_uses_skip_locked_and_expiry_filter() -> None:
    sql = _compile_sql(_recover_select_statement(now=NOW, batch_size=25))

    assert "SKIP LOCKED" in sql
    assert "FOR UPDATE" in sql
    assert "jobs.status = 'leased'" in sql
    assert "jobs.lease_expires_at <=" in sql
    assert "ORDER BY jobs.lease_expires_at ASC, jobs.job_id ASC" in sql
    assert "LIMIT 25" in sql


def test_claim_next_eligible_job_creates_attempt_and_starts_run() -> None:
    job = _make_job()
    run = _make_run(status=AnalysisRunStatus.QUEUED.value)
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(run),
            _scalar_result(False),
        ]
    )

    claimed = _run(
        claim_next_eligible_job(
            session,
            lease_owner=OWNER,
            now=NOW,
            max_attempts=3,
            lease_seconds=300,
        )
    )

    assert isinstance(claimed, ClaimedJob)
    assert claimed.run_started is True
    assert job.status == JobStatus.LEASED.value
    assert job.attempt_count == 1
    assert job.lease_owner == OWNER
    assert job.lease_expires_at == NOW + timedelta(seconds=300)
    assert job.heartbeat_at == NOW
    assert len(session.added) == 1
    attempt = session.added[0]
    assert isinstance(attempt, JobAttempt)
    assert attempt.attempt_number == 1
    assert attempt.status == JobAttemptStatus.ACTIVE.value
    assert run.status == AnalysisRunStatus.RUNNING.value
    assert run.started_at == NOW

    claim_sql = _compile_sql(session.execute_calls[0])
    assert "SKIP LOCKED" in claim_sql


def test_claim_next_eligible_job_returns_none_when_queue_empty() -> None:
    session = _RecordingSession([_scalar_result(None)])

    claimed = _run(
        claim_next_eligible_job(
            session,
            lease_owner=OWNER,
            now=NOW,
            max_attempts=3,
            lease_seconds=300,
        )
    )

    assert claimed is None


def test_heartbeat_extends_matching_lease() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW - timedelta(seconds=30),
    )
    session = _RecordingSession([_scalar_result(job)])
    later = NOW + timedelta(seconds=45)

    _run(
        heartbeat_job(
            session,
            job_id=JOB_ID,
            lease_owner=OWNER,
            now=later,
            lease_seconds=300,
        )
    )

    assert job.heartbeat_at == later
    assert job.lease_expires_at == later + timedelta(seconds=300)


def test_heartbeat_raises_lease_lost_without_mutation() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(seconds=60),
    )
    original_expiry = job.lease_expires_at
    session = _RecordingSession([_scalar_result(job)])

    with pytest.raises(JobLeaseLostError) as exc_info:
        _run(
            heartbeat_job(
                session,
                job_id=JOB_ID,
                lease_owner=OWNER,
                now=NOW,
                lease_seconds=300,
            )
        )

    assert exc_info.value.job_id == JOB_ID
    assert job.lease_expires_at == original_expiry


def test_heartbeat_rejects_exact_lease_expiry_without_mutation() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW,
        heartbeat_at=NOW - timedelta(seconds=30),
    )
    before = _job_snapshot(job)
    session = _RecordingSession([_scalar_result(job)])

    with pytest.raises(JobLeaseLostError):
        _run(
            heartbeat_job(
                session,
                job_id=JOB_ID,
                lease_owner=OWNER,
                now=NOW,
                lease_seconds=300,
            )
        )

    assert _job_snapshot(job) == before


def test_complete_marks_job_and_attempt_terminal() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
    )
    attempt = _make_attempt()
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(True),
            _scalar_result(attempt),
        ]
    )

    _run(
        complete_job(
            session,
            job_id=JOB_ID,
            lease_owner=OWNER,
            now=NOW,
        )
    )

    assert job.status == JobStatus.COMPLETED.value
    assert job.lease_owner is None
    assert attempt.status == JobAttemptStatus.SUCCEEDED.value
    assert attempt.completed_at == NOW


def test_complete_requires_completed_run_step() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
    )
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(False),
        ]
    )

    with pytest.raises(JobInvariantError):
        _run(
            complete_job(
                session,
                job_id=JOB_ID,
                lease_owner=OWNER,
                now=NOW,
            )
        )


def test_record_job_failure_requeues_retryable_attempt() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
    )
    attempt = _make_attempt()
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(attempt),
        ]
    )
    next_at = NOW + timedelta(seconds=30)

    _run(
        record_job_failure(
            session,
            job_id=JOB_ID,
            lease_owner=OWNER,
            now=NOW,
            error_code="model_timeout",
            transport_retryable=True,
            max_attempts=3,
            run_deadline_at=NOW + timedelta(hours=1),
            next_available_at=next_at,
        )
    )

    assert job.status == JobStatus.AVAILABLE.value
    assert job.available_at == next_at
    assert job.lease_owner is None
    assert attempt.status == JobAttemptStatus.FAILED.value
    assert attempt.error_code == "model_timeout"
    assert attempt.error_retryable is True


def test_record_job_failure_terminalizes_non_retryable_error() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
    )
    attempt = _make_attempt()
    run = _make_run(status=AnalysisRunStatus.RUNNING.value)
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(attempt),
            _scalar_result(run),
        ]
    )

    _run(
        record_job_failure(
            session,
            job_id=JOB_ID,
            lease_owner=OWNER,
            now=NOW,
            error_code="citation_validation_failed",
            transport_retryable=False,
            max_attempts=3,
        )
    )

    assert job.status == JobStatus.FAILED.value
    assert run.status == AnalysisRunStatus.FAILED.value
    assert run.error_code == "citation_validation_failed"
    assert run.error_retryable is False
    assert run.completed_at == NOW


def test_record_job_failure_uses_dependency_attempts_exhausted() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=3,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
    )
    attempt = _make_attempt(attempt_number=3)
    run = _make_run(status=AnalysisRunStatus.RUNNING.value)
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(attempt),
            _scalar_result(run),
        ]
    )

    _run(
        record_job_failure(
            session,
            job_id=JOB_ID,
            lease_owner=OWNER,
            now=NOW,
            error_code="model_timeout",
            transport_retryable=True,
            max_attempts=3,
        )
    )

    assert job.status == JobStatus.FAILED.value
    assert run.error_code == ERROR_DEPENDENCY_ATTEMPTS_EXHAUSTED
    assert job.last_error_code == "model_timeout"


def test_record_job_failure_terminalizes_when_run_deadline_reached() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
    )
    attempt = _make_attempt()
    run = _make_run(status=AnalysisRunStatus.RUNNING.value)
    deadline = NOW
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(attempt),
            _scalar_result(run),
        ]
    )

    _run(
        record_job_failure(
            session,
            job_id=JOB_ID,
            lease_owner=OWNER,
            now=NOW,
            error_code="model_timeout",
            transport_retryable=True,
            max_attempts=3,
            run_deadline_at=deadline,
        )
    )

    assert job.status == JobStatus.FAILED.value
    assert run.error_code == ERROR_RUN_DEADLINE_EXCEEDED
    assert job.last_error_code == "model_timeout"


@pytest.mark.parametrize(
    ("next_available_at", "message"),
    [
        (None, "next_available_at is required"),
        (NOW - timedelta(seconds=1), "next_available_at must not be before now"),
        (NOW + timedelta(hours=1), "strictly before run_deadline_at"),
    ],
)
def test_record_job_failure_rejects_invalid_retry_schedule_without_mutation(
    next_available_at: datetime | None,
    message: str,
) -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
    )
    attempt = _make_attempt()
    job_before = _job_snapshot(job)
    attempt_before = _attempt_snapshot(attempt)
    session = _RecordingSession(
        [
            _scalar_result(job),
            _scalar_result(attempt),
        ]
    )
    deadline = NOW + timedelta(minutes=30)

    with pytest.raises(ValueError, match=message):
        _run(
            record_job_failure(
                session,
                job_id=JOB_ID,
                lease_owner=OWNER,
                now=NOW,
                error_code="model_timeout",
                transport_retryable=True,
                max_attempts=3,
                run_deadline_at=deadline,
                next_available_at=next_available_at,
            )
        )

    assert _job_snapshot(job) == job_before
    assert _attempt_snapshot(attempt) == attempt_before


def test_recover_expired_lease_requeues_idempotent_job() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        step_idempotent=True,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(minutes=6),
    )
    attempt = _make_attempt()
    run = _make_run(status=AnalysisRunStatus.RUNNING.value)
    session = _RecordingSession(
        [
            _scalars_result([job]),
            _scalar_result(run),
            _scalar_result(False),
            _scalar_result(attempt),
        ]
    )

    recovered = _run(recover_expired_leases(session, now=NOW, batch_size=10))

    assert recovered == [JOB_ID]
    assert job.status == JobStatus.AVAILABLE.value
    assert job.available_at == NOW
    assert job.lease_owner is None
    assert attempt.status == JobAttemptStatus.FAILED.value
    assert attempt.error_code == ERROR_JOB_LEASE_LOST
    assert attempt.error_retryable is True
    recover_sql = _compile_sql(session.execute_calls[0])
    assert "SKIP LOCKED" in recover_sql


def test_recover_expired_lease_recovers_exact_expiry_boundary() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        step_idempotent=True,
        lease_owner=OWNER,
        lease_expires_at=NOW,
        heartbeat_at=NOW - timedelta(minutes=6),
    )
    attempt = _make_attempt()
    run = _make_run(status=AnalysisRunStatus.RUNNING.value)
    session = _RecordingSession(
        [
            _scalars_result([job]),
            _scalar_result(run),
            _scalar_result(False),
            _scalar_result(attempt),
        ]
    )

    recovered = _run(recover_expired_leases(session, now=NOW))

    assert recovered == [JOB_ID]
    assert job.status == JobStatus.AVAILABLE.value


def test_recover_expired_lease_marks_non_idempotent_reconciliation() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        step_idempotent=False,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(minutes=6),
    )
    attempt = _make_attempt()
    run = _make_run(status=AnalysisRunStatus.RUNNING.value)
    session = _RecordingSession(
        [
            _scalars_result([job]),
            _scalar_result(run),
            _scalar_result(False),
            _scalar_result(attempt),
        ]
    )

    recovered = _run(recover_expired_leases(session, now=NOW))

    assert recovered == [JOB_ID]
    assert job.status == JobStatus.RECONCILIATION_REQUIRED.value


def test_recover_expired_lease_skips_terminal_run_and_completed_step() -> None:
    job = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(minutes=6),
    )
    attempt = _make_attempt()
    run = _make_run(status=AnalysisRunStatus.FAILED.value)
    session = _RecordingSession(
        [
            _scalars_result([job]),
            _scalar_result(run),
        ]
    )

    recovered = _run(recover_expired_leases(session, now=NOW))
    assert recovered == []
    assert job.status == JobStatus.LEASED.value
    assert attempt.status == JobAttemptStatus.ACTIVE.value

    job2 = _make_job(
        status=JobStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(minutes=6),
    )
    attempt2 = _make_attempt()
    run2 = _make_run(status=AnalysisRunStatus.RUNNING.value)
    session2 = _RecordingSession(
        [
            _scalars_result([job2]),
            _scalar_result(run2),
            _scalar_result(True),
        ]
    )

    recovered2 = _run(recover_expired_leases(session2, now=NOW))
    assert recovered2 == []
    assert job2.status == JobStatus.LEASED.value
    assert attempt2.status == JobAttemptStatus.ACTIVE.value


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_attempts": 0}, "max_attempts must be positive"),
        ({"lease_seconds": 0}, "lease_seconds must be positive"),
    ],
)
def test_claim_validates_positive_limits(kwargs: dict[str, int], message: str) -> None:
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError, match=message):
        _run(
            claim_next_eligible_job(
                session,  # type: ignore[arg-type]
                lease_owner=OWNER,
                now=NOW,
                max_attempts=kwargs.get("max_attempts", 3),
                lease_seconds=kwargs.get("lease_seconds", 300),
            )
        )


def test_claim_rejects_naive_now() -> None:
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError, match="timezone-aware"):
        _run(
            claim_next_eligible_job(
                session,  # type: ignore[arg-type]
                lease_owner=OWNER,
                now=datetime(2026, 7, 11, 12, 0, 0),
                max_attempts=3,
                lease_seconds=300,
            )
        )


@pytest.mark.parametrize(
    "lease_owner",
    ["", "x" * 256],
)
def test_claim_rejects_invalid_lease_owner_length(lease_owner: str) -> None:
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError, match="lease_owner must be between 1 and 255"):
        _run(
            claim_next_eligible_job(
                session,  # type: ignore[arg-type]
                lease_owner=lease_owner,
                now=NOW,
                max_attempts=3,
                lease_seconds=300,
            )
        )
