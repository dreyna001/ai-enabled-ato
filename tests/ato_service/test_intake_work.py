"""Focused tests for package revision intake work lease repository."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.db.models import PackageRevision, PackageRevisionIntakeAttempt, PackageRevisionIntakeWork
from ato_service.intake_work import (
    ERROR_INTAKE_LEASE_LOST,
    ERROR_INTAKE_RECONCILIATION_REQUIRED,
    ClaimedIntakeWork,
    IntakeAttemptStatus,
    IntakeLeaseLostError,
    IntakeWorkPhase,
    IntakeWorkStatus,
    _claim_select_statement,
    _recover_select_statement,
    assert_intake_claim_live,
    claim_next_eligible_intake_work,
    complete_intake_work,
    heartbeat_intake_work,
    mark_intake_work_reconciliation_required,
    record_intake_work_failure,
    recover_expired_intake_leases,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
OWNER = "intake-worker-a"
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
FENCE_TOKEN = uuid.UUID("22222222-2222-4222-8222-222222222222")
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


def _make_revision(*, revision_version: int = 2) -> PackageRevision:
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=uuid.uuid4(),
        parent_revision_id=None,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="customer_upload",
        sensitivity="cui",
        effective_data_labels=["cui"],
        authority_manifest_id="authority-manifest-1",
        content_manifest_sha256="a" * 64,
        package_content_sha256=None,
        system_context_snapshot_id=None,
        revision_version=revision_version,
        status="scanning",
        created_by="operator",
        created_at=NOW,
    )


def _make_work(
    *,
    status: str = IntakeWorkStatus.AVAILABLE.value,
    work_phase: str = IntakeWorkPhase.MALWARE_SCAN.value,
    attempt_count: int = 0,
    available_at: datetime = NOW,
    expected_revision_version: int = 2,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    fence_token: uuid.UUID | None = None,
) -> PackageRevisionIntakeWork:
    return PackageRevisionIntakeWork(
        package_revision_id=REVISION_ID,
        work_phase=work_phase,
        status=status,
        attempt_count=attempt_count,
        available_at=available_at,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        heartbeat_at=heartbeat_at,
        fence_token=fence_token,
        expected_revision_version=expected_revision_version,
        last_error_code=None,
    )


def _make_attempt(
    *,
    status: str = IntakeAttemptStatus.ACTIVE.value,
    attempt_number: int = 1,
    lease_owner: str = OWNER,
    fence_token: uuid.UUID = FENCE_TOKEN,
    expected_revision_version: int = 2,
) -> PackageRevisionIntakeAttempt:
    return PackageRevisionIntakeAttempt(
        attempt_id=ATTEMPT_ID,
        package_revision_id=REVISION_ID,
        work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
        attempt_number=attempt_number,
        status=status,
        lease_owner=lease_owner,
        fence_token=fence_token,
        expected_revision_version=expected_revision_version,
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


def _assert_lease_cleared(work: PackageRevisionIntakeWork) -> None:
    assert work.status != IntakeWorkStatus.LEASED.value
    assert work.lease_owner is None
    assert work.lease_expires_at is None
    assert work.heartbeat_at is None
    assert work.fence_token is None


def test_claim_sql_uses_skip_locked_on_work_row_only() -> None:
    sql = _compile_sql(
        _claim_select_statement(
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            now=NOW,
            max_attempts=3,
        )
    )

    assert "SKIP LOCKED" in sql
    assert "FOR UPDATE OF package_revision_intake_work" in sql
    assert (
        "ORDER BY package_revision_intake_work.available_at ASC, "
        "package_revision_intake_work.package_revision_id ASC"
    ) in sql
    assert "package_revision_intake_work.status = 'available'" in sql
    assert "package_revision_intake_work.work_phase = 'malware_scan'" in sql
    assert "package_revision_intake_work.attempt_count < 3" in sql
    assert "JOIN package_revisions" in sql
    assert "package_revisions.status = 'scanning'" in sql
    assert "package_revisions.content_manifest_sha256 IS NOT NULL" in sql


def test_recover_sql_uses_skip_locked_and_expiry_filter() -> None:
    sql = _compile_sql(_recover_select_statement(now=NOW, batch_size=25))

    assert "SKIP LOCKED" in sql
    assert "package_revision_intake_work.status = 'leased'" in sql
    assert "package_revision_intake_work.lease_expires_at <=" in sql
    assert "LIMIT 25" in sql


def test_claim_next_eligible_intake_work_creates_attempt_with_fence() -> None:
    work = _make_work()
    revision = _make_revision(revision_version=2)
    session = _RecordingSession(
        [
            _scalar_result(work),
            _scalar_result(revision),
        ]
    )

    claimed = _run(
        claim_next_eligible_intake_work(
            session,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            now=NOW,
            max_attempts=3,
            lease_seconds=300,
        )
    )

    assert isinstance(claimed, ClaimedIntakeWork)
    assert work.status == IntakeWorkStatus.LEASED.value
    assert work.attempt_count == 1
    assert work.lease_owner == OWNER
    assert work.expected_revision_version == 2
    assert work.fence_token == claimed.fence_token
    assert work.lease_expires_at == NOW + timedelta(seconds=300)
    assert len(session.added) == 1
    attempt = session.added[0]
    assert isinstance(attempt, PackageRevisionIntakeAttempt)
    assert attempt.fence_token == claimed.fence_token
    assert attempt.expected_revision_version == 2

    claim_sql = _compile_sql(session.execute_calls[0])
    assert "SKIP LOCKED" in claim_sql


def test_claim_returns_none_when_queue_empty() -> None:
    session = _RecordingSession([_scalar_result(None)])

    claimed = _run(
        claim_next_eligible_intake_work(
            session,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            now=NOW,
            max_attempts=3,
            lease_seconds=300,
        )
    )

    assert claimed is None


def test_heartbeat_extends_matching_fence_and_lease() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW - timedelta(seconds=30),
        fence_token=FENCE_TOKEN,
    )
    session = _RecordingSession([_scalar_result(work)])
    later = NOW + timedelta(seconds=45)

    _run(
        heartbeat_intake_work(
            session,
            package_revision_id=REVISION_ID,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            fence_token=FENCE_TOKEN,
            now=later,
            lease_seconds=300,
        )
    )

    assert work.heartbeat_at == later
    assert work.lease_expires_at == later + timedelta(seconds=300)
    assert work.fence_token == FENCE_TOKEN


def test_heartbeat_rejects_stale_fence_token() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
    )
    session = _RecordingSession([_scalar_result(work)])
    stale_fence = uuid.uuid4()

    with pytest.raises(IntakeLeaseLostError):
        _run(
            heartbeat_intake_work(
                session,
                package_revision_id=REVISION_ID,
                work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
                lease_owner=OWNER,
                fence_token=stale_fence,
                now=NOW,
                lease_seconds=300,
            )
        )


def test_complete_marks_work_and_attempt_terminal() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    revision = _make_revision(revision_version=2)
    attempt = _make_attempt()
    session = _RecordingSession(
        [
            _scalar_result(work),
            _scalar_result(revision),
            _scalar_result(attempt),
        ]
    )

    _run(
        complete_intake_work(
            session,
            package_revision_id=REVISION_ID,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            fence_token=FENCE_TOKEN,
            now=NOW,
        )
    )

    assert work.status == IntakeWorkStatus.COMPLETED.value
    _assert_lease_cleared(work)
    assert attempt.status == IntakeAttemptStatus.SUCCEEDED.value
    assert attempt.completed_at == NOW


def test_complete_rejects_stale_revision_version() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    revision = _make_revision(revision_version=3)
    session = _RecordingSession(
        [
            _scalar_result(work),
            _scalar_result(revision),
        ]
    )

    with pytest.raises(IntakeLeaseLostError):
        _run(
            complete_intake_work(
                session,
                package_revision_id=REVISION_ID,
                work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
                lease_owner=OWNER,
                fence_token=FENCE_TOKEN,
                now=NOW,
            )
        )


def test_assert_intake_claim_live_rejects_revision_mismatch() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    revision = _make_revision(revision_version=3)

    with pytest.raises(IntakeLeaseLostError):
        assert_intake_claim_live(
            work,
            revision,
            lease_owner=OWNER,
            fence_token=FENCE_TOKEN,
            now=NOW,
        )


def test_record_intake_work_failure_requeues_retryable_attempt() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    revision = _make_revision(revision_version=2)
    attempt = _make_attempt()
    session = _RecordingSession(
        [
            _scalar_result(work),
            _scalar_result(revision),
            _scalar_result(attempt),
        ]
    )
    next_at = NOW + timedelta(seconds=30)

    _run(
        record_intake_work_failure(
            session,
            package_revision_id=REVISION_ID,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            fence_token=FENCE_TOKEN,
            now=NOW,
            error_code="malware_scan_failed",
            transport_retryable=True,
            max_attempts=3,
            next_available_at=next_at,
        )
    )

    assert work.status == IntakeWorkStatus.AVAILABLE.value
    assert work.available_at == next_at
    assert work.expected_revision_version == 2
    _assert_lease_cleared(work)
    assert attempt.status == IntakeAttemptStatus.FAILED.value


def test_record_intake_work_failure_terminalizes_non_retryable_error() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    revision = _make_revision(revision_version=2)
    attempt = _make_attempt()
    session = _RecordingSession(
        [
            _scalar_result(work),
            _scalar_result(revision),
            _scalar_result(attempt),
        ]
    )

    _run(
        record_intake_work_failure(
            session,
            package_revision_id=REVISION_ID,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            fence_token=FENCE_TOKEN,
            now=NOW,
            error_code="malware_detected",
            transport_retryable=False,
            max_attempts=3,
        )
    )

    assert work.status == IntakeWorkStatus.FAILED.value
    _assert_lease_cleared(work)


def test_recover_expired_intake_lease_requeues_when_attempts_remain() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(seconds=60),
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    attempt = _make_attempt()
    revision = _make_revision(revision_version=2)
    session = _RecordingSession(
        [
            _scalars_result([work]),
            _scalar_result(attempt),
            _scalar_result(revision),
        ]
    )

    recovered = _run(
        recover_expired_intake_leases(
            session,
            now=NOW,
            max_attempts=3,
            batch_size=10,
        )
    )

    assert recovered == [(REVISION_ID, IntakeWorkPhase.MALWARE_SCAN.value)]
    assert work.status == IntakeWorkStatus.AVAILABLE.value
    assert work.available_at == NOW
    assert work.last_error_code == ERROR_INTAKE_LEASE_LOST
    _assert_lease_cleared(work)
    assert attempt.error_code == ERROR_INTAKE_LEASE_LOST


def test_recover_expired_intake_lease_fails_when_attempt_budget_exhausted() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=3,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(seconds=60),
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    attempt = _make_attempt(attempt_number=3)
    revision = _make_revision(revision_version=2)
    session = _RecordingSession(
        [
            _scalars_result([work]),
            _scalar_result(attempt),
            _scalar_result(revision),
        ]
    )

    _run(
        recover_expired_intake_leases(
            session,
            now=NOW,
            max_attempts=3,
            batch_size=10,
        )
    )

    assert work.status == IntakeWorkStatus.FAILED.value
    _assert_lease_cleared(work)


def test_mark_reconciliation_required_noops_on_stale_fence_token() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    session = _RecordingSession([_scalar_result(work)])
    stale_fence = uuid.uuid4()

    applied = _run(
        mark_intake_work_reconciliation_required(
            session,
            package_revision_id=REVISION_ID,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            fence_token=stale_fence,
            now=NOW,
        )
    )

    assert applied is False
    assert work.status == IntakeWorkStatus.LEASED.value
    assert work.fence_token == FENCE_TOKEN


def test_mark_reconciliation_required_noops_when_lease_expired() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW - timedelta(seconds=1),
        heartbeat_at=NOW - timedelta(seconds=60),
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    session = _RecordingSession([_scalar_result(work)])

    applied = _run(
        mark_intake_work_reconciliation_required(
            session,
            package_revision_id=REVISION_ID,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            fence_token=FENCE_TOKEN,
            now=NOW,
        )
    )

    assert applied is False
    assert work.status == IntakeWorkStatus.LEASED.value


def test_mark_reconciliation_required_terminalizes_active_attempt() -> None:
    work = _make_work(
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        lease_owner=OWNER,
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    attempt = _make_attempt()
    session = _RecordingSession(
        [
            _scalar_result(work),
            _scalar_result(attempt),
        ]
    )

    applied = _run(
        mark_intake_work_reconciliation_required(
            session,
            package_revision_id=REVISION_ID,
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            lease_owner=OWNER,
            fence_token=FENCE_TOKEN,
            now=NOW,
        )
    )

    assert applied is True
    assert work.status == IntakeWorkStatus.RECONCILIATION_REQUIRED.value
    assert work.last_error_code == ERROR_INTAKE_RECONCILIATION_REQUIRED
    _assert_lease_cleared(work)
    assert attempt.status == IntakeAttemptStatus.FAILED.value
    assert attempt.completed_at == NOW
    assert attempt.error_code == ERROR_INTAKE_RECONCILIATION_REQUIRED
    assert attempt.error_retryable is False
