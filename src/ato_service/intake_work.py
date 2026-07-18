"""Lease-safe async repository for package revision intake work and attempts."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.db import enums as ev
from ato_service.db.models import (
    PackageRevision,
    PackageRevisionIntakeAttempt,
    PackageRevisionIntakeWork,
)

ERROR_INTAKE_LEASE_LOST = "intake_lease_lost"
ERROR_INTAKE_RECONCILIATION_REQUIRED = "reconciliation_required"
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,127}$")


class IntakeWorkPhase(StrEnum):
    MALWARE_SCAN = "malware_scan"
    DETERMINISTIC_EXTRACT = "deterministic_extract"


class IntakeWorkStatus(StrEnum):
    AVAILABLE = "available"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class IntakeAttemptStatus(StrEnum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ClaimedIntakeWork:
    work: PackageRevisionIntakeWork
    attempt: PackageRevisionIntakeAttempt
    fence_token: uuid.UUID


class IntakeLeaseLostError(Exception):
    """Raised when a worker no longer owns an unexpired lease or fence token."""

    def __init__(
        self,
        *,
        package_revision_id: uuid.UUID,
        work_phase: str,
    ) -> None:
        self.package_revision_id = package_revision_id
        self.work_phase = work_phase
        super().__init__(
            "intake lease lost for "
            f"package_revision_id={package_revision_id} work_phase={work_phase}"
        )


class IntakeInvariantError(ValueError):
    """Raised when an intake work operation violates queue invariants."""

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


def _require_work_phase(work_phase: str) -> str:
    if work_phase not in {phase.value for phase in IntakeWorkPhase}:
        raise ValueError("work_phase must be a supported intake work phase")
    return work_phase


def _expected_revision_status(work_phase: str) -> str:
    if work_phase == IntakeWorkPhase.MALWARE_SCAN.value:
        return "scanning"
    if work_phase == IntakeWorkPhase.DETERMINISTIC_EXTRACT.value:
        return "extracting"
    raise ValueError("work_phase must be a supported intake work phase")


def _require_allowed_data_origins(
    allowed_data_origins: frozenset[str] | None,
) -> frozenset[str]:
    values = (
        frozenset(ev.DATA_ORIGIN_VALUES)
        if allowed_data_origins is None
        else frozenset(allowed_data_origins)
    )
    if not values or not values.issubset(ev.DATA_ORIGIN_VALUES):
        raise ValueError("allowed_data_origins must contain supported data origins")
    return values


def _require_error_code(error_code: str) -> str:
    if _ERROR_CODE_PATTERN.fullmatch(error_code) is None:
        raise ValueError("error_code must match the stable error-code pattern")
    return error_code


def _lease_is_expired(lease_expires_at: datetime | None, *, now: datetime) -> bool:
    return lease_expires_at is None or lease_expires_at <= now


def _validate_retry_schedule(
    *,
    now: datetime,
    next_available_at: datetime | None,
) -> datetime:
    if next_available_at is None:
        raise ValueError("next_available_at is required for a retryable failure")
    validated = _require_aware_utc(next_available_at, field_name="next_available_at")
    if validated < now:
        raise ValueError("next_available_at must not be before now")
    return validated


def _clear_lease_fields(work: PackageRevisionIntakeWork) -> None:
    work.lease_owner = None
    work.lease_expires_at = None
    work.heartbeat_at = None
    work.fence_token = None


def _set_lease_fields(
    work: PackageRevisionIntakeWork,
    *,
    lease_owner: str,
    fence_token: uuid.UUID,
    now: datetime,
    lease_seconds: int,
) -> None:
    work.lease_owner = lease_owner
    work.fence_token = fence_token
    work.lease_expires_at = now + timedelta(seconds=lease_seconds)
    work.heartbeat_at = now


def _assert_valid_lease(
    work: PackageRevisionIntakeWork,
    *,
    lease_owner: str,
    fence_token: uuid.UUID,
    now: datetime,
) -> None:
    if (
        work.status != IntakeWorkStatus.LEASED.value
        or work.lease_owner != lease_owner
        or work.fence_token != fence_token
        or _lease_is_expired(work.lease_expires_at, now=now)
    ):
        raise IntakeLeaseLostError(
            package_revision_id=work.package_revision_id,
            work_phase=work.work_phase,
        )


def assert_intake_claim_live(
    work: PackageRevisionIntakeWork,
    revision: PackageRevision,
    *,
    lease_owner: str,
    fence_token: uuid.UUID,
    now: datetime,
) -> None:
    """Verify an owned, unexpired claim with matching fence and revision version."""
    _assert_valid_lease(work, lease_owner=lease_owner, fence_token=fence_token, now=now)
    if revision.revision_version != work.expected_revision_version:
        raise IntakeLeaseLostError(
            package_revision_id=work.package_revision_id,
            work_phase=work.work_phase,
        )


def _claim_select_statement(
    *,
    work_phase: str,
    now: datetime,
    max_attempts: int,
    allowed_data_origins: frozenset[str] | None = None,
) -> object:
    allowed_origins = _require_allowed_data_origins(allowed_data_origins)
    return (
        select(PackageRevisionIntakeWork)
        .join(
            PackageRevision,
            PackageRevision.package_revision_id
            == PackageRevisionIntakeWork.package_revision_id,
        )
        .where(
            PackageRevisionIntakeWork.work_phase == work_phase,
            PackageRevisionIntakeWork.status == IntakeWorkStatus.AVAILABLE.value,
            PackageRevisionIntakeWork.available_at <= now,
            PackageRevisionIntakeWork.attempt_count < max_attempts,
            PackageRevision.status == _expected_revision_status(work_phase),
            PackageRevision.content_manifest_sha256.is_not(None),
            or_(
                PackageRevision.data_origin.is_(None),
                PackageRevision.data_origin.in_(sorted(allowed_origins)),
            ),
        )
        .order_by(
            PackageRevisionIntakeWork.available_at.asc(),
            PackageRevisionIntakeWork.package_revision_id.asc(),
        )
        .limit(1)
        .with_for_update(of=PackageRevisionIntakeWork, skip_locked=True)
    )


def _recover_select_statement(*, now: datetime, batch_size: int) -> object:
    return (
        select(PackageRevisionIntakeWork)
        .where(
            PackageRevisionIntakeWork.status == IntakeWorkStatus.LEASED.value,
            PackageRevisionIntakeWork.lease_expires_at.is_not(None),
            PackageRevisionIntakeWork.lease_expires_at <= now,
        )
        .order_by(
            PackageRevisionIntakeWork.lease_expires_at.asc(),
            PackageRevisionIntakeWork.package_revision_id.asc(),
            PackageRevisionIntakeWork.work_phase.asc(),
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


async def _load_intake_work_for_update(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    work_phase: str,
) -> PackageRevisionIntakeWork | None:
    statement = (
        select(PackageRevisionIntakeWork)
        .where(
            PackageRevisionIntakeWork.package_revision_id == package_revision_id,
            PackageRevisionIntakeWork.work_phase == work_phase,
        )
        .with_for_update()
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _load_package_revision_for_update(
    session: AsyncSession,
    package_revision_id: uuid.UUID,
) -> PackageRevision | None:
    statement = (
        select(PackageRevision)
        .where(PackageRevision.package_revision_id == package_revision_id)
        .with_for_update()
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _load_active_attempt_for_update(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    work_phase: str,
) -> PackageRevisionIntakeAttempt | None:
    statement = (
        select(PackageRevisionIntakeAttempt)
        .where(
            PackageRevisionIntakeAttempt.package_revision_id == package_revision_id,
            PackageRevisionIntakeAttempt.work_phase == work_phase,
            PackageRevisionIntakeAttempt.status == IntakeAttemptStatus.ACTIVE.value,
        )
        .with_for_update()
    )
    return (await session.execute(statement)).scalar_one_or_none()


def bootstrap_malware_scan_work(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    expected_revision_version: int,
    now: datetime,
) -> PackageRevisionIntakeWork:
    """Insert available malware_scan work for a freshly finalized revision."""
    now = _require_aware_utc(now, field_name="now")
    _require_positive_int(expected_revision_version, field_name="expected_revision_version")

    work = PackageRevisionIntakeWork(
        package_revision_id=package_revision_id,
        work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
        status=IntakeWorkStatus.AVAILABLE.value,
        attempt_count=0,
        available_at=now,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        fence_token=None,
        expected_revision_version=expected_revision_version,
        last_error_code=None,
    )
    session.add(work)
    return work


def bootstrap_deterministic_extract_work(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    expected_revision_version: int,
    now: datetime,
) -> PackageRevisionIntakeWork:
    """Insert available deterministic_extract work after a clean scan."""
    now = _require_aware_utc(now, field_name="now")
    _require_positive_int(expected_revision_version, field_name="expected_revision_version")

    work = PackageRevisionIntakeWork(
        package_revision_id=package_revision_id,
        work_phase=IntakeWorkPhase.DETERMINISTIC_EXTRACT.value,
        status=IntakeWorkStatus.AVAILABLE.value,
        attempt_count=0,
        available_at=now,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        fence_token=None,
        expected_revision_version=expected_revision_version,
        last_error_code=None,
    )
    session.add(work)
    return work


async def claim_next_eligible_intake_work(
    session: AsyncSession,
    *,
    work_phase: str,
    lease_owner: str,
    now: datetime,
    max_attempts: int,
    lease_seconds: int,
    allowed_data_origins: frozenset[str] | None = None,
) -> ClaimedIntakeWork | None:
    """Atomically claim the next eligible intake work within the caller transaction."""
    now = _require_aware_utc(now, field_name="now")
    work_phase = _require_work_phase(work_phase)
    _require_positive_int(max_attempts, field_name="max_attempts")
    _require_positive_int(lease_seconds, field_name="lease_seconds")
    _require_lease_owner(lease_owner)

    statement = _claim_select_statement(
        work_phase=work_phase,
        now=now,
        max_attempts=max_attempts,
        allowed_data_origins=allowed_data_origins,
    )
    work = (await session.execute(statement)).scalar_one_or_none()
    if work is None:
        return None

    if work.attempt_count >= max_attempts:
        raise IntakeInvariantError(
            message="claim selected work that exhausted the transport attempt budget"
        )

    revision = await _load_package_revision_for_update(session, work.package_revision_id)
    if revision is None:
        raise IntakeInvariantError(
            message="claim selected intake work with a missing package revision"
        )
    if revision.status != _expected_revision_status(work.work_phase):
        raise IntakeInvariantError(
            message="claim selected intake work for the wrong revision status"
        )

    fence_token = uuid.uuid4()
    next_attempt_number = work.attempt_count + 1
    work.attempt_count = next_attempt_number
    work.status = IntakeWorkStatus.LEASED.value
    work.expected_revision_version = revision.revision_version
    _set_lease_fields(
        work,
        lease_owner=lease_owner,
        fence_token=fence_token,
        now=now,
        lease_seconds=lease_seconds,
    )

    attempt = PackageRevisionIntakeAttempt(
        attempt_id=uuid.uuid4(),
        package_revision_id=work.package_revision_id,
        work_phase=work.work_phase,
        attempt_number=next_attempt_number,
        status=IntakeAttemptStatus.ACTIVE.value,
        lease_owner=lease_owner,
        fence_token=fence_token,
        expected_revision_version=revision.revision_version,
        started_at=now,
        completed_at=None,
        error_code=None,
        error_retryable=None,
    )
    session.add(attempt)
    return ClaimedIntakeWork(work=work, attempt=attempt, fence_token=fence_token)


async def heartbeat_intake_work(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    work_phase: str,
    lease_owner: str,
    fence_token: uuid.UUID,
    now: datetime,
    lease_seconds: int,
) -> None:
    """Extend an owned lease without mutating state when ownership is invalid."""
    now = _require_aware_utc(now, field_name="now")
    work_phase = _require_work_phase(work_phase)
    _require_positive_int(lease_seconds, field_name="lease_seconds")
    _require_lease_owner(lease_owner)

    work = await _load_intake_work_for_update(
        session,
        package_revision_id=package_revision_id,
        work_phase=work_phase,
    )
    if work is None:
        raise IntakeLeaseLostError(
            package_revision_id=package_revision_id,
            work_phase=work_phase,
        )
    _assert_valid_lease(
        work,
        lease_owner=lease_owner,
        fence_token=fence_token,
        now=now,
    )
    _set_lease_fields(
        work,
        lease_owner=lease_owner,
        fence_token=fence_token,
        now=now,
        lease_seconds=lease_seconds,
    )


async def complete_intake_work(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    work_phase: str,
    lease_owner: str,
    fence_token: uuid.UUID,
    now: datetime,
) -> None:
    """Mark leased intake work completed after verifying fence and revision version."""
    now = _require_aware_utc(now, field_name="now")
    work_phase = _require_work_phase(work_phase)
    _require_lease_owner(lease_owner)

    work = await _load_intake_work_for_update(
        session,
        package_revision_id=package_revision_id,
        work_phase=work_phase,
    )
    if work is None:
        raise IntakeLeaseLostError(
            package_revision_id=package_revision_id,
            work_phase=work_phase,
        )
    _assert_valid_lease(
        work,
        lease_owner=lease_owner,
        fence_token=fence_token,
        now=now,
    )

    revision = await _load_package_revision_for_update(session, package_revision_id)
    if revision is None:
        raise IntakeInvariantError(
            message="complete requires the owning package revision"
        )
    assert_intake_claim_live(
        work,
        revision,
        lease_owner=lease_owner,
        fence_token=fence_token,
        now=now,
    )

    attempt = await _load_active_attempt_for_update(
        session,
        package_revision_id=package_revision_id,
        work_phase=work_phase,
    )
    if attempt is None:
        raise IntakeInvariantError(message="complete requires an active intake attempt")

    attempt.status = IntakeAttemptStatus.SUCCEEDED.value
    attempt.completed_at = now
    attempt.error_code = None
    attempt.error_retryable = None

    work.status = IntakeWorkStatus.COMPLETED.value
    work.last_error_code = None
    _clear_lease_fields(work)


async def record_intake_work_failure(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    work_phase: str,
    lease_owner: str,
    fence_token: uuid.UUID,
    now: datetime,
    error_code: str,
    transport_retryable: bool,
    max_attempts: int,
    next_available_at: datetime | None = None,
) -> None:
    """Record a failed attempt and either requeue or terminalize intake work."""
    now = _require_aware_utc(now, field_name="now")
    work_phase = _require_work_phase(work_phase)
    _require_positive_int(max_attempts, field_name="max_attempts")
    _require_error_code(error_code)
    _require_lease_owner(lease_owner)

    work = await _load_intake_work_for_update(
        session,
        package_revision_id=package_revision_id,
        work_phase=work_phase,
    )
    if work is None:
        raise IntakeLeaseLostError(
            package_revision_id=package_revision_id,
            work_phase=work_phase,
        )
    _assert_valid_lease(
        work,
        lease_owner=lease_owner,
        fence_token=fence_token,
        now=now,
    )

    revision = await _load_package_revision_for_update(session, package_revision_id)
    if revision is None:
        raise IntakeInvariantError(
            message="record_intake_work_failure requires the owning package revision"
        )
    assert_intake_claim_live(
        work,
        revision,
        lease_owner=lease_owner,
        fence_token=fence_token,
        now=now,
    )

    attempt = await _load_active_attempt_for_update(
        session,
        package_revision_id=package_revision_id,
        work_phase=work_phase,
    )
    if attempt is None:
        raise IntakeInvariantError(
            message="record_intake_work_failure requires an active intake attempt"
        )

    can_reclaim = work.attempt_count < max_attempts
    should_retry = transport_retryable and can_reclaim

    validated_next_available_at: datetime | None = None
    if should_retry:
        validated_next_available_at = _validate_retry_schedule(
            now=now,
            next_available_at=next_available_at,
        )

    attempt.status = IntakeAttemptStatus.FAILED.value
    attempt.completed_at = now
    attempt.error_code = error_code
    attempt.error_retryable = transport_retryable
    work.last_error_code = error_code

    if should_retry:
        assert validated_next_available_at is not None
        work.status = IntakeWorkStatus.AVAILABLE.value
        work.available_at = validated_next_available_at
        work.expected_revision_version = revision.revision_version
        _clear_lease_fields(work)
        return

    work.status = IntakeWorkStatus.FAILED.value
    _clear_lease_fields(work)


def _terminalize_active_attempt_for_lease_loss(
    attempt: PackageRevisionIntakeAttempt,
    *,
    now: datetime,
) -> None:
    attempt.status = IntakeAttemptStatus.FAILED.value
    attempt.completed_at = now
    attempt.error_code = ERROR_INTAKE_LEASE_LOST
    attempt.error_retryable = True


async def _recover_single_expired_intake_work(
    session: AsyncSession,
    work: PackageRevisionIntakeWork,
    *,
    now: datetime,
    max_attempts: int,
) -> None:
    if not _lease_is_expired(work.lease_expires_at, now=now):
        raise IntakeInvariantError(
            message="recover selected intake work whose lease has not expired"
        )

    attempt = await _load_active_attempt_for_update(
        session,
        package_revision_id=work.package_revision_id,
        work_phase=work.work_phase,
    )
    if attempt is not None:
        _terminalize_active_attempt_for_lease_loss(attempt, now=now)

    work.last_error_code = ERROR_INTAKE_LEASE_LOST
    _clear_lease_fields(work)

    if attempt is None:
        work.status = IntakeWorkStatus.RECONCILIATION_REQUIRED.value
        return

    revision = await _load_package_revision_for_update(session, work.package_revision_id)
    if revision is None:
        work.status = IntakeWorkStatus.RECONCILIATION_REQUIRED.value
        return
    if revision.status != _expected_revision_status(work.work_phase):
        work.status = IntakeWorkStatus.RECONCILIATION_REQUIRED.value
        return

    if work.attempt_count >= max_attempts:
        work.status = IntakeWorkStatus.FAILED.value
        return

    work.status = IntakeWorkStatus.AVAILABLE.value
    work.available_at = now
    work.expected_revision_version = revision.revision_version


async def mark_intake_work_reconciliation_required(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    work_phase: str,
    lease_owner: str,
    fence_token: uuid.UUID,
    now: datetime,
) -> bool:
    """Mark a live owned lease reconciliation_required or no-op when the claim is stale."""
    now = _require_aware_utc(now, field_name="now")
    work_phase = _require_work_phase(work_phase)
    _require_lease_owner(lease_owner)

    work = await _load_intake_work_for_update(
        session,
        package_revision_id=package_revision_id,
        work_phase=work_phase,
    )
    if work is None:
        return False
    try:
        _assert_valid_lease(
            work,
            lease_owner=lease_owner,
            fence_token=fence_token,
            now=now,
        )
    except IntakeLeaseLostError:
        return False

    attempt = await _load_active_attempt_for_update(
        session,
        package_revision_id=package_revision_id,
        work_phase=work_phase,
    )
    if attempt is not None:
        attempt.status = IntakeAttemptStatus.FAILED.value
        attempt.completed_at = now
        attempt.error_code = ERROR_INTAKE_RECONCILIATION_REQUIRED
        attempt.error_retryable = False

    work.status = IntakeWorkStatus.RECONCILIATION_REQUIRED.value
    work.last_error_code = ERROR_INTAKE_RECONCILIATION_REQUIRED
    _clear_lease_fields(work)
    return True


async def recover_expired_intake_leases(
    session: AsyncSession,
    *,
    now: datetime,
    max_attempts: int,
    batch_size: int = 100,
) -> list[tuple[uuid.UUID, str]]:
    """Recover expired leases under row locks; every selected row leaves ``leased``."""
    now = _require_aware_utc(now, field_name="now")
    _require_positive_int(max_attempts, field_name="max_attempts")
    _require_positive_int(batch_size, field_name="batch_size")

    statement = _recover_select_statement(now=now, batch_size=batch_size)
    work_rows = (await session.execute(statement)).scalars().all()
    recovered: list[tuple[uuid.UUID, str]] = []

    for work in work_rows:
        await _recover_single_expired_intake_work(
            session,
            work,
            now=now,
            max_attempts=max_attempts,
        )
        if work.status == IntakeWorkStatus.LEASED.value:
            raise IntakeInvariantError(
                message="recover left expired intake work in leased status"
            )
        recovered.append((work.package_revision_id, work.work_phase))

    return recovered
