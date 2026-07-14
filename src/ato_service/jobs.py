"""Lease-safe async repository for analyzer jobs and attempts."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.db.models import AnalysisRun, Job, JobAttempt, RunStep
from ato_service.lifecycle_transitions import (
    AnalysisRunStatus,
    AnalysisRunTransitionCondition,
    analysis_run_status_is_terminal,
    require_analysis_run_transition,
)

ERROR_JOB_LEASE_LOST = "job_lease_lost"
ERROR_DEPENDENCY_ATTEMPTS_EXHAUSTED = "dependency_attempts_exhausted"
ERROR_RUN_DEADLINE_EXCEEDED = "run_deadline_exceeded"
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,127}$")


class JobStatus(StrEnum):
    AVAILABLE = "available"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class JobAttemptStatus(StrEnum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job: Job
    attempt: JobAttempt
    run_started: bool


class JobLeaseLostError(Exception):
    """Raised when a worker no longer owns an unexpired lease."""

    def __init__(self, *, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        super().__init__(f"job lease lost for job_id={job_id}")


class JobInvariantError(ValueError):
    """Raised when a job operation violates queue invariants."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _require_positive_int(value: int, *, field_name: str) -> int:
    if value < 1:
        raise ValueError(f"{field_name} must be positive")
    return value


def _require_lease_owner(lease_owner: str) -> str:
    if not lease_owner or len(lease_owner) > 255:
        raise ValueError("lease_owner must be between 1 and 255 characters")
    return lease_owner


def _require_error_code(error_code: str) -> str:
    if _ERROR_CODE_PATTERN.fullmatch(error_code) is None:
        raise ValueError("error_code must match the stable error-code pattern")
    return error_code


def _lease_is_expired(lease_expires_at: datetime | None, *, now: datetime) -> bool:
    return lease_expires_at is None or lease_expires_at <= now


def _deadline_allows_retry(*, now: datetime, run_deadline_at: datetime | None) -> bool:
    return run_deadline_at is None or now < run_deadline_at


def _validate_retry_schedule(
    *,
    now: datetime,
    next_available_at: datetime | None,
    run_deadline_at: datetime | None,
) -> datetime:
    if next_available_at is None:
        raise ValueError("next_available_at is required for a retryable failure")
    validated = _require_aware_utc(next_available_at, field_name="next_available_at")
    if validated < now:
        raise ValueError("next_available_at must not be before now")
    if run_deadline_at is not None and validated >= run_deadline_at:
        raise ValueError("next_available_at must be strictly before run_deadline_at")
    return validated


def _terminal_run_error_code(
    *,
    error_code: str,
    transport_retryable: bool,
    can_reclaim: bool,
    deadline_allows_retry: bool,
) -> str:
    if transport_retryable and not can_reclaim:
        return ERROR_DEPENDENCY_ATTEMPTS_EXHAUSTED
    if transport_retryable and not deadline_allows_retry:
        return ERROR_RUN_DEADLINE_EXCEEDED
    return error_code


def _clear_lease_fields(job: Job) -> None:
    job.lease_owner = None
    job.lease_expires_at = None
    job.heartbeat_at = None


def _set_lease_fields(
    job: Job,
    *,
    lease_owner: str,
    now: datetime,
    lease_seconds: int,
) -> None:
    job.lease_owner = lease_owner
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.heartbeat_at = now


def _assert_valid_lease(job: Job, *, lease_owner: str, now: datetime) -> None:
    if (
        job.status != JobStatus.LEASED.value
        or job.lease_owner != lease_owner
        or _lease_is_expired(job.lease_expires_at, now=now)
    ):
        raise JobLeaseLostError(job_id=job.job_id)


def _completed_run_step_exists(run_id: uuid.UUID, step_key: str) -> object:
    return (
        select(RunStep.step_id)
        .where(
            RunStep.run_id == run_id,
            RunStep.step_key == step_key,
        )
        .limit(1)
    )


def _claim_select_statement(*, now: datetime, max_attempts: int) -> object:
    completed_step = _completed_run_step_exists(Job.run_id, Job.step_key)
    return (
        select(Job)
        .join(AnalysisRun, AnalysisRun.run_id == Job.run_id)
        .where(
            Job.status == JobStatus.AVAILABLE.value,
            Job.available_at <= now,
            Job.attempt_count < max_attempts,
            AnalysisRun.status.in_(
                (
                    AnalysisRunStatus.QUEUED.value,
                    AnalysisRunStatus.RUNNING.value,
                )
            ),
            ~exists(completed_step),
        )
        .order_by(Job.available_at.asc(), Job.job_id.asc())
        .limit(1)
        .with_for_update(of=Job, skip_locked=True)
    )


def _recover_select_statement(*, now: datetime, batch_size: int) -> object:
    return (
        select(Job)
        .where(
            Job.status == JobStatus.LEASED.value,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at <= now,
        )
        .order_by(Job.lease_expires_at.asc(), Job.job_id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


async def _load_job_for_update(
    session: AsyncSession,
    job_id: uuid.UUID,
) -> Job | None:
    statement = select(Job).where(Job.job_id == job_id).with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def _load_run_for_update(
    session: AsyncSession,
    run_id: uuid.UUID,
) -> AnalysisRun | None:
    statement = select(AnalysisRun).where(AnalysisRun.run_id == run_id).with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def _load_active_attempt_for_update(
    session: AsyncSession,
    job_id: uuid.UUID,
) -> JobAttempt | None:
    statement = (
        select(JobAttempt)
        .where(
            JobAttempt.job_id == job_id,
            JobAttempt.status == JobAttemptStatus.ACTIVE.value,
        )
        .with_for_update()
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _has_completed_run_step(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    step_key: str,
) -> bool:
    statement = _completed_run_step_exists(run_id, step_key)
    return (await session.execute(select(exists(statement)))).scalar_one()


def _transition_run_to_failed(
    run: AnalysisRun,
    *,
    now: datetime,
    error_code: str,
) -> None:
    current = AnalysisRunStatus(run.status)
    if analysis_run_status_is_terminal(current):
        return
    require_analysis_run_transition(
        current,
        AnalysisRunStatus.FAILED,
        condition=AnalysisRunTransitionCondition.RUN_FAILED,
    )
    run.status = AnalysisRunStatus.FAILED.value
    run.error_code = error_code
    run.error_retryable = False
    run.completed_at = now


def transition_run_to_policy_blocked(
    run: AnalysisRun,
    *,
    now: datetime,
    error_code: str,
) -> None:
    """Terminalize an analysis run denied by routing before any model call."""
    current = AnalysisRunStatus(run.status)
    if analysis_run_status_is_terminal(current):
        return
    condition = (
        AnalysisRunTransitionCondition.POLICY_DENIED_BEFORE_EXECUTION
        if current is AnalysisRunStatus.QUEUED
        else AnalysisRunTransitionCondition.POLICY_DENIED_BEFORE_MODEL
    )
    require_analysis_run_transition(
        current,
        AnalysisRunStatus.POLICY_BLOCKED,
        condition=condition,
    )
    run.status = AnalysisRunStatus.POLICY_BLOCKED.value
    run.error_code = error_code
    run.error_retryable = False
    run.completed_at = now
    run.llm_call_count = 0


async def claim_next_eligible_job(
    session: AsyncSession,
    *,
    lease_owner: str,
    now: datetime,
    max_attempts: int,
    lease_seconds: int,
) -> ClaimedJob | None:
    """Atomically claim the next eligible job within the caller's transaction."""
    now = _require_aware_utc(now, field_name="now")
    _require_positive_int(max_attempts, field_name="max_attempts")
    _require_positive_int(lease_seconds, field_name="lease_seconds")
    _require_lease_owner(lease_owner)

    statement = _claim_select_statement(now=now, max_attempts=max_attempts)
    job = (await session.execute(statement)).scalar_one_or_none()
    if job is None:
        return None

    if job.attempt_count >= max_attempts:
        raise JobInvariantError(
            message="claim selected a job that exhausted the transport attempt budget"
        )

    run = await _load_run_for_update(session, job.run_id)
    if run is None:
        raise JobInvariantError(message="claim selected a job with a missing analysis run")

    current_run_status = AnalysisRunStatus(run.status)
    if analysis_run_status_is_terminal(current_run_status):
        raise JobInvariantError(
            message="claim selected a job whose analysis run is terminal"
        )

    if await _has_completed_run_step(session, run_id=job.run_id, step_key=job.step_key):
        raise JobInvariantError(
            message="claim selected a job whose run step is already completed"
        )

    run_started = False
    if current_run_status == AnalysisRunStatus.QUEUED:
        require_analysis_run_transition(
            current_run_status,
            AnalysisRunStatus.RUNNING,
            condition=AnalysisRunTransitionCondition.WORKER_CLAIMED,
        )
        run.status = AnalysisRunStatus.RUNNING.value
        if run.started_at is None:
            run.started_at = now
        run_started = True

    next_attempt_number = job.attempt_count + 1
    job.attempt_count = next_attempt_number
    job.status = JobStatus.LEASED.value
    _set_lease_fields(job, lease_owner=lease_owner, now=now, lease_seconds=lease_seconds)

    attempt = JobAttempt(
        attempt_id=uuid.uuid4(),
        job_id=job.job_id,
        run_id=job.run_id,
        step_key=job.step_key,
        attempt_number=next_attempt_number,
        status=JobAttemptStatus.ACTIVE.value,
        lease_owner=lease_owner,
        started_at=now,
        completed_at=None,
        error_code=None,
        error_retryable=None,
    )
    session.add(attempt)
    return ClaimedJob(job=job, attempt=attempt, run_started=run_started)


async def heartbeat_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    lease_owner: str,
    now: datetime,
    lease_seconds: int,
) -> None:
    """Extend an owned lease without mutating state when ownership is invalid."""
    now = _require_aware_utc(now, field_name="now")
    _require_positive_int(lease_seconds, field_name="lease_seconds")
    _require_lease_owner(lease_owner)

    job = await _load_job_for_update(session, job_id)
    if job is None:
        raise JobLeaseLostError(job_id=job_id)
    _assert_valid_lease(job, lease_owner=lease_owner, now=now)
    _set_lease_fields(job, lease_owner=lease_owner, now=now, lease_seconds=lease_seconds)


async def complete_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    lease_owner: str,
    now: datetime,
) -> None:
    """Mark a leased job completed when its run step is already durable in-session."""
    now = _require_aware_utc(now, field_name="now")
    _require_lease_owner(lease_owner)

    job = await _load_job_for_update(session, job_id)
    if job is None:
        raise JobLeaseLostError(job_id=job_id)
    _assert_valid_lease(job, lease_owner=lease_owner, now=now)

    if not await _has_completed_run_step(
        session,
        run_id=job.run_id,
        step_key=job.step_key,
    ):
        raise JobInvariantError(
            message="complete requires a completed run step in the same transaction"
        )

    attempt = await _load_active_attempt_for_update(session, job_id)
    if attempt is None:
        raise JobInvariantError(message="complete requires an active job attempt")

    attempt.status = JobAttemptStatus.SUCCEEDED.value
    attempt.completed_at = now
    attempt.error_code = None
    attempt.error_retryable = None

    job.status = JobStatus.COMPLETED.value
    job.last_error_code = None
    _clear_lease_fields(job)


async def record_job_failure(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    lease_owner: str,
    now: datetime,
    error_code: str,
    transport_retryable: bool,
    max_attempts: int,
    run_deadline_at: datetime | None = None,
    next_available_at: datetime | None = None,
) -> None:
    """Record a failed attempt and either requeue or terminalize the job and run."""
    now = _require_aware_utc(now, field_name="now")
    _require_positive_int(max_attempts, field_name="max_attempts")
    _require_error_code(error_code)
    _require_lease_owner(lease_owner)
    if run_deadline_at is not None:
        run_deadline_at = _require_aware_utc(run_deadline_at, field_name="run_deadline_at")

    job = await _load_job_for_update(session, job_id)
    if job is None:
        raise JobLeaseLostError(job_id=job_id)
    _assert_valid_lease(job, lease_owner=lease_owner, now=now)

    attempt = await _load_active_attempt_for_update(session, job_id)
    if attempt is None:
        raise JobInvariantError(message="record_job_failure requires an active job attempt")

    can_reclaim = job.attempt_count < max_attempts
    deadline_allows_retry = _deadline_allows_retry(now=now, run_deadline_at=run_deadline_at)
    should_retry = transport_retryable and can_reclaim and deadline_allows_retry

    validated_next_available_at: datetime | None = None
    if should_retry:
        validated_next_available_at = _validate_retry_schedule(
            now=now,
            next_available_at=next_available_at,
            run_deadline_at=run_deadline_at,
        )

    attempt.status = JobAttemptStatus.FAILED.value
    attempt.completed_at = now
    attempt.error_code = error_code
    attempt.error_retryable = transport_retryable
    job.last_error_code = error_code

    if should_retry:
        assert validated_next_available_at is not None
        job.status = JobStatus.AVAILABLE.value
        job.available_at = validated_next_available_at
        _clear_lease_fields(job)
        return

    job.status = JobStatus.FAILED.value
    _clear_lease_fields(job)

    run = await _load_run_for_update(session, job.run_id)
    if run is None:
        raise JobInvariantError(
            message="record_job_failure requires the owning analysis run"
        )

    if AnalysisRunStatus(run.status) == AnalysisRunStatus.RUNNING:
        terminal_error_code = _terminal_run_error_code(
            error_code=error_code,
            transport_retryable=transport_retryable,
            can_reclaim=can_reclaim,
            deadline_allows_retry=deadline_allows_retry,
        )
        _transition_run_to_failed(run, now=now, error_code=terminal_error_code)


def _terminalize_active_attempt_for_lease_loss(
    attempt: JobAttempt,
    *,
    now: datetime,
) -> None:
    attempt.status = JobAttemptStatus.FAILED.value
    attempt.completed_at = now
    attempt.error_code = ERROR_JOB_LEASE_LOST
    attempt.error_retryable = True


async def _recover_single_expired_job(
    session: AsyncSession,
    job: Job,
    *,
    now: datetime,
    max_attempts: int,
) -> None:
    if not _lease_is_expired(job.lease_expires_at, now=now):
        raise JobInvariantError(
            message="recover selected a job whose lease has not expired"
        )

    run = await _load_run_for_update(session, job.run_id)
    has_completed_step = await _has_completed_run_step(
        session,
        run_id=job.run_id,
        step_key=job.step_key,
    )
    attempt = await _load_active_attempt_for_update(session, job.job_id)

    if attempt is not None:
        _terminalize_active_attempt_for_lease_loss(attempt, now=now)

    job.last_error_code = ERROR_JOB_LEASE_LOST
    _clear_lease_fields(job)

    if has_completed_step:
        job.status = JobStatus.RECONCILIATION_REQUIRED.value
        return

    if run is None:
        job.status = JobStatus.RECONCILIATION_REQUIRED.value
        return

    run_status = AnalysisRunStatus(run.status)
    if analysis_run_status_is_terminal(run_status):
        if run_status == AnalysisRunStatus.SUCCEEDED:
            job.status = JobStatus.RECONCILIATION_REQUIRED.value
        else:
            job.status = JobStatus.FAILED.value
        return

    if attempt is None:
        job.status = JobStatus.RECONCILIATION_REQUIRED.value
        return

    if run_status != AnalysisRunStatus.RUNNING:
        job.status = JobStatus.RECONCILIATION_REQUIRED.value
        return

    if not job.step_idempotent:
        job.status = JobStatus.RECONCILIATION_REQUIRED.value
        return

    if job.attempt_count >= max_attempts:
        job.status = JobStatus.FAILED.value
        _transition_run_to_failed(
            run,
            now=now,
            error_code=ERROR_DEPENDENCY_ATTEMPTS_EXHAUSTED,
        )
        return

    job.status = JobStatus.AVAILABLE.value
    job.available_at = now


async def recover_expired_leases(
    session: AsyncSession,
    *,
    now: datetime,
    max_attempts: int,
    batch_size: int = 100,
) -> list[uuid.UUID]:
    """Recover expired leases under row locks; every selected job leaves ``leased``."""
    now = _require_aware_utc(now, field_name="now")
    _require_positive_int(max_attempts, field_name="max_attempts")
    _require_positive_int(batch_size, field_name="batch_size")

    statement = _recover_select_statement(now=now, batch_size=batch_size)
    jobs = (await session.execute(statement)).scalars().all()
    recovered: list[uuid.UUID] = []

    for job in jobs:
        await _recover_single_expired_job(
            session,
            job,
            now=now,
            max_attempts=max_attempts,
        )
        if job.status == JobStatus.LEASED.value:
            raise JobInvariantError(
                message="recover left an expired job in leased status"
            )
        recovered.append(job.job_id)

    return recovered
