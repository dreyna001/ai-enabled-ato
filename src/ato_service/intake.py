"""Canonical scan and extract orchestration for Component A Diff 3 intake."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from types import SimpleNamespace
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.db.session import session_scope

from ato_service.audit import append_audit_event
from ato_service.blobs import BlobStore
from ato_service.db.models import (
    FactProposal,
    PackageNormalizationStep,
    PackageRevision,
    PackageRevisionDraft,
    PackageRevisionIntakeWork,
    SourceArtifact,
    System,
)
from ato_service.draft_builder import (
    DOCUMENT_SCHEMA_VERSION,
    AggregatedIntakeDraft,
    DraftBuildError,
    build_initial_draft,
)
from ato_service.extraction import extract_content
from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.limits import resolve_extraction_limits_from_config
from ato_service.extraction.types import ExtractionContext, ExtractionOutcome, VisionPolicy
from ato_service.intake_work import (
    ClaimedIntakeWork,
    IntakeInvariantError,
    IntakeLeaseLostError,
    IntakeWorkPhase,
    assert_intake_claim_live,
    bootstrap_deterministic_extract_work,
    claim_next_eligible_intake_work,
    complete_intake_work,
    heartbeat_intake_work,
    record_intake_work_failure,
    recover_expired_intake_leases,
)
from ato_service.lifecycle_transitions import (
    PackageRevisionStatus,
    PackageRevisionTransitionCondition,
    require_package_revision_transition,
)
from ato_service.malware_scan import (
    MalwareScanOutcome,
    MalwareScanResult,
    MalwareScanner,
    MalwareScannerUnavailableError,
    resolve_malware_scanner,
)
from ato_service.model_routing import DataOrigin
from ato_service.normalization_service import (
    NormalizationDependencies,
    NormalizationInvariantError,
    PendingNormalizationOutcome,
    mark_normalization_and_intake_reconciliation_required,
    normalization_audit_metadata,
    run_intake_normalization,
    terminalize_normalization_step,
    verify_normalization_step_for_commit,
)
from ato_service.runtime_config import RuntimeConfig, RuntimeConfigError
from ato_service.source_artifacts import (
    SourceArtifactStorageError,
    SourceTypeMismatchError,
    read_source_artifact_bytes,
)

INTAKE_ACTOR_ID = "intake-worker"
DEFAULT_LEASE_OWNER = INTAKE_ACTOR_ID

# Bounded intake transport knobs; not borrowed from unrelated analysis semantics.
INTAKE_LEASE_SECONDS = 300
INTAKE_MAX_ATTEMPTS = 3
INTAKE_RETRY_BACKOFF_SECONDS = 30
INTAKE_RECOVER_BATCH_SIZE = 100

_MIME_TO_DECLARED_FORMAT: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/markdown": "markdown",
    "application/json": "json",
    "application/pdf": "pdf",
    "text/plain": "text",
    "application/xml": "xml",
}


class IntakeConfigurationError(RuntimeConfigError):
    """Raised when intake runtime prerequisites are not satisfied."""


class IntakeOutcomeKind(StrEnum):
    COMPLETED = "completed"
    RETRYABLE_FAILURE = "retryable_failure"
    DISCARDED_STALE = "discarded_stale"


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    """Immutable artifact fields loaded before I/O."""

    artifact_id: uuid.UUID
    package_revision_id: uuid.UUID
    display_filename: str
    storage_key: str
    sha256: str
    size_bytes: int
    declared_media_type: str
    detected_media_type: str
    artifact_kind: str
    malware_scan_status: str
    extraction_status: str


@dataclass(frozen=True, slots=True)
class IntakeRevisionSnapshot:
    """Immutable revision, system, and artifact state for one intake attempt."""

    package_revision_id: uuid.UUID
    revision_version: int
    status: str
    profile_id: str
    impact_level: str | None
    content_manifest_sha256: str
    data_origin: str
    sensitivity: str
    system_id: uuid.UUID
    system_display_name: str
    artifacts: tuple[ArtifactSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ClaimedIntakeOperation:
    """Owned intake work claim returned from the claim transaction."""

    package_revision_id: uuid.UUID
    work_phase: str
    lease_owner: str
    fence_token: uuid.UUID
    expected_revision_version: int


@dataclass(frozen=True, slots=True)
class IntakeResult:
    """One committed intake operation outcome."""

    package_revision_id: uuid.UUID
    work_phase: str
    outcome: IntakeOutcomeKind
    previous_revision_status: str | None = None
    revision_status: str | None = None
    revision_version: int | None = None
    artifact_count: int = 0
    draft_inserted: bool = False
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class _ScanArtifactResult:
    artifact_id: uuid.UUID
    result: MalwareScanResult


@dataclass(frozen=True, slots=True)
class _ScanComputeOutcome:
    kind: Literal["all_clean", "retryable", "invalid_content", "quarantined"]
    reason_code: str | None = None
    infected_artifact_id: uuid.UUID | None = None
    artifact_results: tuple[_ScanArtifactResult, ...] = ()


@dataclass(frozen=True, slots=True)
class _ExtractComputeOutcome:
    kind: Literal["succeeded", "retryable", "invalid_content"]
    reason_code: str | None = None
    aggregated_draft: AggregatedIntakeDraft | None = None
    artifact_outcomes: tuple[tuple[ArtifactSnapshot, ExtractionOutcome], ...] = ()


def build_intake_lease_owner(*, token: str | None = None) -> str:
    """Return a bounded per-process lease owner identifier."""
    host = socket.gethostname().split(".")[0][:24]
    unique = token or secrets.token_hex(4)
    owner = f"intake-{host}-{os.getpid()}-{unique}"
    return owner if len(owner) <= 255 else owner[:255]


def require_intake_runtime(config: RuntimeConfig) -> None:
    """Fail closed unless dev_local intake or a resolvable production scanner exists."""
    if config.runtime_profile == "dev_local":
        return
    resolve_malware_scanner(config)


def resolve_intake_allowed_data_origins(config: RuntimeConfig) -> frozenset[str]:
    """Return data origins eligible for intake work claims in the active profile."""
    if config.runtime_profile == "dev_local":
        return frozenset(
            {
                DataOrigin.SYNTHETIC.value,
                DataOrigin.REDACTED_NONPRODUCTION.value,
                DataOrigin.CUSTOMER_PRODUCTION.value,
            }
        )
    return frozenset({DataOrigin.CUSTOMER_PRODUCTION.value})


async def recover_intake_leases(
    session: AsyncSession,
    *,
    now: datetime,
    max_attempts: int = INTAKE_MAX_ATTEMPTS,
    batch_size: int = INTAKE_RECOVER_BATCH_SIZE,
) -> list[tuple[uuid.UUID, str]]:
    """Recover expired intake leases in one short transaction."""
    return await recover_expired_intake_leases(
        session,
        now=now,
        max_attempts=max_attempts,
        batch_size=batch_size,
    )


async def process_next_intake_operation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: RuntimeConfig,
    blob_store: BlobStore,
    hmac_key: bytes,
    scanner: MalwareScanner,
    lease_owner: str = DEFAULT_LEASE_OWNER,
    now_factory: Callable[[], datetime] | None = None,
    max_attempts: int = INTAKE_MAX_ATTEMPTS,
    lease_seconds: int = INTAKE_LEASE_SECONDS,
    allowed_data_origins: frozenset[str] | None = None,
    normalization_deps: NormalizationDependencies | None = None,
) -> IntakeResult | None:
    """Advance one intake operation, preferring extraction before new scans."""
    clock = now_factory or (lambda: datetime.now(timezone.utc))
    origins = (
        allowed_data_origins
        if allowed_data_origins is not None
        else resolve_intake_allowed_data_origins(config)
    )
    for work_phase in (
        IntakeWorkPhase.DETERMINISTIC_EXTRACT.value,
        IntakeWorkPhase.MALWARE_SCAN.value,
    ):
        claimed = await _claim_operation(
            session_factory,
            work_phase=work_phase,
            lease_owner=lease_owner,
            now=_require_aware_utc(clock()),
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            allowed_data_origins=origins,
        )
        if claimed is None:
            continue
        return await _process_claimed_operation(
            session_factory,
            claimed=claimed,
            config=config,
            blob_store=blob_store,
            hmac_key=hmac_key,
            scanner=scanner,
            lease_owner=lease_owner,
            now_factory=clock,
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            normalization_deps=normalization_deps,
        )
    return None


async def drain_intake(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: RuntimeConfig,
    blob_store: BlobStore,
    hmac_key: bytes,
    scanner: MalwareScanner,
    lease_owner: str = DEFAULT_LEASE_OWNER,
    now_factory: Callable[[], datetime] | None = None,
    normalization_deps: NormalizationDependencies | None = None,
) -> tuple[IntakeResult, ...]:
    """Process currently eligible intake operations until idle."""
    clock = now_factory or (lambda: datetime.now(timezone.utc))
    processed: list[IntakeResult] = []
    while True:
        async with session_scope(session_factory) as session:
            await recover_intake_leases(session, now=_require_aware_utc(clock()))
        result = await process_next_intake_operation(
            session_factory,
            config=config,
            blob_store=blob_store,
            hmac_key=hmac_key,
            scanner=scanner,
            lease_owner=lease_owner,
            now_factory=clock,
            normalization_deps=normalization_deps,
        )
        if result is None:
            return tuple(processed)
        processed.append(result)


async def _claim_operation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    work_phase: str,
    lease_owner: str,
    now: datetime,
    max_attempts: int,
    lease_seconds: int,
    allowed_data_origins: frozenset[str],
) -> ClaimedIntakeOperation | None:
    async with session_scope(session_factory) as session:
        claimed = await claim_next_eligible_intake_work(
            session,
            work_phase=work_phase,
            lease_owner=lease_owner,
            now=now,
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            allowed_data_origins=allowed_data_origins,
        )
        if claimed is None:
            return None
        return _operation_from_claim(claimed, lease_owner=lease_owner)


async def _process_claimed_operation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claimed: ClaimedIntakeOperation,
    config: RuntimeConfig,
    blob_store: BlobStore,
    hmac_key: bytes,
    scanner: MalwareScanner,
    lease_owner: str,
    now_factory: Callable[[], datetime],
    max_attempts: int,
    lease_seconds: int,
    normalization_deps: NormalizationDependencies | None = None,
) -> IntakeResult:
    snapshot = await _load_revision_snapshot(
        session_factory,
        package_revision_id=claimed.package_revision_id,
    )
    if claimed.work_phase == IntakeWorkPhase.MALWARE_SCAN.value:
        compute = await _compute_scan_outcome(
            session_factory,
            snapshot=snapshot,
            blob_store=blob_store,
            scanner=scanner,
            claimed=claimed,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            now_factory=now_factory,
        )
        return await _persist_scan_outcome(
            session_factory,
            claimed=claimed,
            snapshot=snapshot,
            compute=compute,
            hmac_key=hmac_key,
            lease_owner=lease_owner,
            now_factory=now_factory,
            max_attempts=max_attempts,
        )
    compute = await _compute_extract_outcome(
        session_factory,
        snapshot=snapshot,
        config=config,
        blob_store=blob_store,
        claimed=claimed,
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
        now_factory=now_factory,
    )
    normalization: PendingNormalizationOutcome | None = None
    if (
        compute.kind == "succeeded"
        and compute.aggregated_draft is not None
        and normalization_deps is not None
    ):
        normalization = await run_intake_normalization(
            deps=normalization_deps,
            session_factory=session_factory,
            snapshot=snapshot,
            claimed=claimed,
            deterministic_draft=compute.aggregated_draft,
            artifact_outcomes=compute.artifact_outcomes,
            lease_owner=lease_owner,
            now_factory=now_factory,
        )
        if normalization.reconciliation_required:
            await _mark_reconciliation_required(
                session_factory,
                claimed=claimed,
                lease_owner=lease_owner,
                now_factory=now_factory,
                normalization_step_id=normalization.step_id,
            )
            return IntakeResult(
                package_revision_id=claimed.package_revision_id,
                work_phase=claimed.work_phase,
                outcome=IntakeOutcomeKind.DISCARDED_STALE,
                reason_code="reconciliation_required",
            )
    return await _persist_extract_outcome(
        session_factory,
        claimed=claimed,
        snapshot=snapshot,
        compute=compute,
        normalization=normalization,
        hmac_key=hmac_key,
        lease_owner=lease_owner,
        now_factory=now_factory,
        max_attempts=max_attempts,
    )


async def _load_revision_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    package_revision_id: uuid.UUID,
) -> IntakeRevisionSnapshot:
    async with session_scope(session_factory) as session:
        revision = (
            await session.execute(
                select(PackageRevision).where(
                    PackageRevision.package_revision_id == package_revision_id
                )
            )
        ).scalar_one_or_none()
        if revision is None:
            raise IntakeInvariantError(
                message="intake snapshot load requires the package revision"
            )
        system = (
            await session.execute(
                select(System).where(System.system_id == revision.system_id)
            )
        ).scalar_one_or_none()
        if system is None:
            raise IntakeInvariantError(
                message="intake snapshot load requires the owning system"
            )
        artifacts = (
            await session.execute(
                select(SourceArtifact)
                .where(SourceArtifact.package_revision_id == package_revision_id)
                .order_by(SourceArtifact.artifact_id.asc())
            )
        ).scalars().all()
        if revision.content_manifest_sha256 is None:
            raise IntakeInvariantError(
                message="intake snapshot load requires a content manifest digest"
            )
        return IntakeRevisionSnapshot(
            package_revision_id=revision.package_revision_id,
            revision_version=revision.revision_version,
            status=revision.status,
            profile_id=revision.profile_id,
            impact_level=revision.impact_level,
            content_manifest_sha256=revision.content_manifest_sha256,
            data_origin=revision.data_origin,
            sensitivity=revision.sensitivity,
            system_id=system.system_id,
            system_display_name=system.display_name,
            artifacts=tuple(_artifact_snapshot(artifact) for artifact in artifacts),
        )


async def _compute_scan_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    snapshot: IntakeRevisionSnapshot,
    blob_store: BlobStore,
    scanner: MalwareScanner,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    lease_seconds: int,
    now_factory: Callable[[], datetime],
) -> _ScanComputeOutcome:
    _require_scan_snapshot(snapshot)
    artifact_results: list[_ScanArtifactResult] = []
    for index, artifact in enumerate(snapshot.artifacts):
        if index > 0 and index % 3 == 0:
            await _heartbeat_claim(
                session_factory,
                claimed=claimed,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
                now_factory=now_factory,
            )
        result = await asyncio.to_thread(
            _scan_one_artifact,
            scanner,
            blob_store,
            artifact,
        )
        artifact_results.append(
            _ScanArtifactResult(artifact_id=artifact.artifact_id, result=result)
        )
        if result.outcome == MalwareScanOutcome.INFECTED:
            return _ScanComputeOutcome(
                kind="quarantined",
                reason_code="malware_detected",
                infected_artifact_id=artifact.artifact_id,
                artifact_results=tuple(artifact_results),
            )
        if result.reason_code == "source_type_mismatch":
            return _ScanComputeOutcome(
                kind="invalid_content",
                reason_code="source_type_mismatch",
                artifact_results=tuple(artifact_results),
            )
        if result.outcome == MalwareScanOutcome.ERROR:
            reason_code = result.reason_code or "malware_scan_failed"
            return _ScanComputeOutcome(
                kind="retryable",
                reason_code=reason_code,
                artifact_results=tuple(artifact_results),
            )
        if result.outcome != MalwareScanOutcome.CLEAN:
            return _ScanComputeOutcome(
                kind="retryable",
                reason_code="malware_scan_failed",
                artifact_results=tuple(artifact_results),
            )
    return _ScanComputeOutcome(
        kind="all_clean",
        artifact_results=tuple(artifact_results),
    )


async def _compute_extract_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    snapshot: IntakeRevisionSnapshot,
    config: RuntimeConfig,
    blob_store: BlobStore,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    lease_seconds: int,
    now_factory: Callable[[], datetime],
) -> _ExtractComputeOutcome:
    _require_extract_snapshot(snapshot)
    limits = resolve_extraction_limits_from_config(config)
    vision_policy = VisionPolicy(vision_allowed=config.vision_model_enabled)
    artifact_outcomes: list[tuple[ArtifactSnapshot, ExtractionOutcome]] = []
    revision_model = _revision_namespace(snapshot)
    system_model = _system_namespace(snapshot)
    artifact_models = [_artifact_namespace(artifact) for artifact in snapshot.artifacts]
    for index, artifact in enumerate(snapshot.artifacts):
        if index > 0 and index % 2 == 0:
            await _heartbeat_claim(
                session_factory,
                claimed=claimed,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
                now_factory=now_factory,
            )
        try:
            content_bytes = await asyncio.to_thread(
                read_source_artifact_bytes,
                blob_store,
                _artifact_namespace(artifact),
            )
            outcome = await asyncio.to_thread(
                extract_content,
                content_bytes=content_bytes,
                sha256=artifact.sha256,
                context=_extraction_context(artifact),
                limits=limits,
                vision_policy=vision_policy,
            )
        except SourceArtifactStorageError:
            return _ExtractComputeOutcome(
                kind="retryable",
                reason_code="storage_unavailable",
            )
        except SourceTypeMismatchError:
            return _ExtractComputeOutcome(
                kind="invalid_content",
                reason_code="source_type_mismatch",
            )
        except ExtractionError as exc:
            return _ExtractComputeOutcome(
                kind="invalid_content",
                reason_code=exc.error_code,
            )
        artifact_outcomes.append((artifact, outcome))
    try:
        aggregated = await asyncio.to_thread(
            build_initial_draft,
            revision=revision_model,
            system=system_model,
            artifacts=artifact_models,
            artifact_outcomes=[
                (_artifact_namespace(artifact), outcome)
                for artifact, outcome in artifact_outcomes
            ],
        )
    except DraftBuildError as exc:
        return _ExtractComputeOutcome(
            kind="invalid_content",
            reason_code=exc.error_code,
        )
    return _ExtractComputeOutcome(
        kind="succeeded",
        aggregated_draft=aggregated,
        artifact_outcomes=tuple(artifact_outcomes),
    )


async def _persist_scan_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    compute: _ScanComputeOutcome,
    hmac_key: bytes,
    lease_owner: str,
    now_factory: Callable[[], datetime],
    max_attempts: int,
) -> IntakeResult:
    if compute.kind == "retryable":
        return await _persist_retryable_failure(
            session_factory,
            claimed=claimed,
            snapshot=snapshot,
            error_code=compute.reason_code or "malware_scan_failed",
            hmac_key=hmac_key,
            lease_owner=lease_owner,
            now_factory=now_factory,
            max_attempts=max_attempts,
        )
    now = _require_aware_utc(now_factory())
    try:
        async with session_scope(session_factory) as session:
            work, revision, artifacts = await _load_locked_intake_state(
                session,
                claimed=claimed,
                snapshot=snapshot,
                now=now,
            )
            if compute.kind == "all_clean":
                return await _commit_clean_scan(
                    session,
                    work=work,
                    revision=revision,
                    artifacts=artifacts,
                    claimed=claimed,
                    snapshot=snapshot,
                    hmac_key=hmac_key,
                    lease_owner=lease_owner,
                    now=now,
                )
            if compute.kind == "quarantined":
                return await _commit_quarantined_scan(
                    session,
                    work=work,
                    revision=revision,
                    artifacts=artifacts,
                    claimed=claimed,
                    snapshot=snapshot,
                    infected_artifact_id=compute.infected_artifact_id,
                    hmac_key=hmac_key,
                    lease_owner=lease_owner,
                    now=now,
                )
            return await _commit_invalid_scan(
                session,
                work=work,
                revision=revision,
                artifacts=artifacts,
                claimed=claimed,
                snapshot=snapshot,
                reason_code=compute.reason_code or "source_type_mismatch",
                hmac_key=hmac_key,
                lease_owner=lease_owner,
                now=now,
            )
    except IntakeLeaseLostError:
        return IntakeResult(
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
            outcome=IntakeOutcomeKind.DISCARDED_STALE,
        )
    except IntakeInvariantError:
        await _mark_reconciliation_required(
            session_factory,
            claimed=claimed,
            lease_owner=lease_owner,
            now_factory=now_factory,
        )
        return IntakeResult(
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
            outcome=IntakeOutcomeKind.DISCARDED_STALE,
            reason_code="reconciliation_required",
        )


async def _persist_extract_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    compute: _ExtractComputeOutcome,
    normalization: PendingNormalizationOutcome | None = None,
    hmac_key: bytes,
    lease_owner: str,
    now_factory: Callable[[], datetime],
    max_attempts: int,
) -> IntakeResult:
    if compute.kind == "retryable":
        return await _persist_retryable_failure(
            session_factory,
            claimed=claimed,
            snapshot=snapshot,
            error_code=compute.reason_code or "storage_unavailable",
            hmac_key=hmac_key,
            lease_owner=lease_owner,
            now_factory=now_factory,
            max_attempts=max_attempts,
        )
    now = _require_aware_utc(now_factory())
    try:
        async with session_scope(session_factory) as session:
            work, revision, artifacts = await _load_locked_intake_state(
                session,
                claimed=claimed,
                snapshot=snapshot,
                now=now,
            )
            await _assert_extract_preconditions(session, revision=revision)
            if compute.kind == "succeeded":
                assert compute.aggregated_draft is not None
                draft_for_commit = (
                    normalization.deterministic_draft
                    if normalization is not None
                    else compute.aggregated_draft
                )
                return await _commit_successful_extract(
                    session,
                    work=work,
                    revision=revision,
                    artifacts=artifacts,
                    claimed=claimed,
                    snapshot=snapshot,
                    aggregated=draft_for_commit,
                    normalization=normalization,
                    hmac_key=hmac_key,
                    lease_owner=lease_owner,
                    now=now,
                )
            return await _commit_invalid_extract(
                session,
                work=work,
                revision=revision,
                artifacts=artifacts,
                claimed=claimed,
                snapshot=snapshot,
                reason_code=compute.reason_code or "source_parse_failed",
                hmac_key=hmac_key,
                lease_owner=lease_owner,
                now=now,
            )
    except IntakeLeaseLostError:
        return IntakeResult(
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
            outcome=IntakeOutcomeKind.DISCARDED_STALE,
        )
    except IntakeInvariantError:
        await _mark_reconciliation_required(
            session_factory,
            claimed=claimed,
            lease_owner=lease_owner,
            now_factory=now_factory,
        )
        return IntakeResult(
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
            outcome=IntakeOutcomeKind.DISCARDED_STALE,
            reason_code="reconciliation_required",
        )


async def _persist_retryable_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    error_code: str,
    hmac_key: bytes,
    lease_owner: str,
    now_factory: Callable[[], datetime],
    max_attempts: int,
) -> IntakeResult:
    now = _require_aware_utc(now_factory())
    try:
        async with session_scope(session_factory) as session:
            work, revision, _artifacts = await _load_locked_intake_state(
                session,
                claimed=claimed,
                snapshot=snapshot,
                now=now,
            )
            await record_intake_work_failure(
                session,
                package_revision_id=claimed.package_revision_id,
                work_phase=claimed.work_phase,
                lease_owner=lease_owner,
                fence_token=claimed.fence_token,
                now=now,
                error_code=error_code,
                transport_retryable=True,
                max_attempts=max_attempts,
                next_available_at=now + timedelta(seconds=INTAKE_RETRY_BACKOFF_SECONDS),
            )
            await append_audit_event(
                session,
                hmac_key=hmac_key,
                actor_type="service",
                actor_id=INTAKE_ACTOR_ID,
                action="package_revision.intake_retry_scheduled",
                object_type="package_revision",
                object_id=str(revision.package_revision_id).lower(),
                outcome="failed",
                reason_code=error_code,
                metadata={
                    "work_phase": claimed.work_phase,
                    "revision_version": revision.revision_version,
                },
                occurred_at=now,
            )
            return IntakeResult(
                package_revision_id=claimed.package_revision_id,
                work_phase=claimed.work_phase,
                outcome=IntakeOutcomeKind.RETRYABLE_FAILURE,
                previous_revision_status=revision.status,
                revision_status=revision.status,
                revision_version=revision.revision_version,
                artifact_count=len(snapshot.artifacts),
                reason_code=error_code,
            )
    except IntakeLeaseLostError:
        return IntakeResult(
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
            outcome=IntakeOutcomeKind.DISCARDED_STALE,
        )


async def _commit_clean_scan(
    session: AsyncSession,
    *,
    work: PackageRevisionIntakeWork,
    revision: PackageRevision,
    artifacts: list[SourceArtifact],
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    hmac_key: bytes,
    lease_owner: str,
    now: datetime,
) -> IntakeResult:
    _assert_scan_artifacts_pending(artifacts)
    previous_status = revision.status
    require_package_revision_transition(
        PackageRevisionStatus.SCANNING,
        PackageRevisionStatus.EXTRACTING,
        condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
    )
    await complete_intake_work(
        session,
        package_revision_id=claimed.package_revision_id,
        work_phase=claimed.work_phase,
        lease_owner=lease_owner,
        fence_token=claimed.fence_token,
        now=now,
    )
    for artifact in artifacts:
        artifact.malware_scan_status = "clean"
    revision.status = PackageRevisionStatus.EXTRACTING.value
    revision.revision_version += 1
    bootstrap_deterministic_extract_work(
        session,
        package_revision_id=revision.package_revision_id,
        expected_revision_version=revision.revision_version,
        now=now,
    )
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=INTAKE_ACTOR_ID,
        action="package_revision.intake_scan_completed",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "artifact_count": len(artifacts),
            "revision_version": revision.revision_version,
        },
        occurred_at=now,
    )
    return IntakeResult(
        package_revision_id=revision.package_revision_id,
        work_phase=claimed.work_phase,
        outcome=IntakeOutcomeKind.COMPLETED,
        previous_revision_status=previous_status,
        revision_status=revision.status,
        revision_version=revision.revision_version,
        artifact_count=len(artifacts),
    )


async def _commit_quarantined_scan(
    session: AsyncSession,
    *,
    work: PackageRevisionIntakeWork,
    revision: PackageRevision,
    artifacts: list[SourceArtifact],
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    infected_artifact_id: uuid.UUID | None,
    hmac_key: bytes,
    lease_owner: str,
    now: datetime,
) -> IntakeResult:
    previous_status = revision.status
    require_package_revision_transition(
        PackageRevisionStatus.SCANNING,
        PackageRevisionStatus.QUARANTINED,
        condition=PackageRevisionTransitionCondition.MALWARE_SCANNER_INFECTED,
    )
    await complete_intake_work(
        session,
        package_revision_id=claimed.package_revision_id,
        work_phase=claimed.work_phase,
        lease_owner=lease_owner,
        fence_token=claimed.fence_token,
        now=now,
    )
    if infected_artifact_id is not None:
        for artifact in artifacts:
            if artifact.artifact_id == infected_artifact_id:
                artifact.malware_scan_status = "infected"
    revision.status = PackageRevisionStatus.QUARANTINED.value
    revision.revision_version += 1
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=INTAKE_ACTOR_ID,
        action="package_revision.intake_scan_quarantined",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code="malware_detected",
        metadata={
            "artifact_count": len(artifacts),
            "revision_version": revision.revision_version,
        },
        occurred_at=now,
    )
    return IntakeResult(
        package_revision_id=revision.package_revision_id,
        work_phase=claimed.work_phase,
        outcome=IntakeOutcomeKind.COMPLETED,
        previous_revision_status=previous_status,
        revision_status=revision.status,
        revision_version=revision.revision_version,
        artifact_count=len(artifacts),
        reason_code="malware_detected",
    )


async def _commit_invalid_scan(
    session: AsyncSession,
    *,
    work: PackageRevisionIntakeWork,
    revision: PackageRevision,
    artifacts: list[SourceArtifact],
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    reason_code: str,
    hmac_key: bytes,
    lease_owner: str,
    now: datetime,
) -> IntakeResult:
    previous_status = revision.status
    require_package_revision_transition(
        PackageRevisionStatus.SCANNING,
        PackageRevisionStatus.INVALID,
        condition=PackageRevisionTransitionCondition.INVALID_CONTENT_NOT_MALWARE,
    )
    await complete_intake_work(
        session,
        package_revision_id=claimed.package_revision_id,
        work_phase=claimed.work_phase,
        lease_owner=lease_owner,
        fence_token=claimed.fence_token,
        now=now,
    )
    for artifact in artifacts:
        if artifact.malware_scan_status == "pending":
            artifact.malware_scan_status = "error"
    revision.status = PackageRevisionStatus.INVALID.value
    revision.revision_version += 1
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=INTAKE_ACTOR_ID,
        action="package_revision.intake_scan_invalidated",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code=reason_code,
        metadata={
            "artifact_count": len(artifacts),
            "revision_version": revision.revision_version,
        },
        occurred_at=now,
    )
    return IntakeResult(
        package_revision_id=revision.package_revision_id,
        work_phase=claimed.work_phase,
        outcome=IntakeOutcomeKind.COMPLETED,
        previous_revision_status=previous_status,
        revision_status=revision.status,
        revision_version=revision.revision_version,
        artifact_count=len(artifacts),
        reason_code=reason_code,
    )


async def _commit_successful_extract(
    session: AsyncSession,
    *,
    work: PackageRevisionIntakeWork,
    revision: PackageRevision,
    artifacts: list[SourceArtifact],
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    aggregated: AggregatedIntakeDraft,
    normalization: PendingNormalizationOutcome | None,
    hmac_key: bytes,
    lease_owner: str,
    now: datetime,
) -> IntakeResult:
    previous_status = revision.status
    require_package_revision_transition(
        PackageRevisionStatus.EXTRACTING,
        PackageRevisionStatus.AWAITING_CONFIRMATION,
        condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
    )
    await complete_intake_work(
        session,
        package_revision_id=claimed.package_revision_id,
        work_phase=claimed.work_phase,
        lease_owner=lease_owner,
        fence_token=claimed.fence_token,
        now=now,
    )
    if normalization is not None and normalization.step_id is not None:
        step = (
            await session.execute(
                select(PackageNormalizationStep)
                .where(PackageNormalizationStep.step_id == normalization.step_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if step is None:
            raise IntakeInvariantError(
                message="deterministic_extract commit requires the normalization step"
            )
        try:
            verify_normalization_step_for_commit(
                step=step,
                pending=normalization,
                package_revision_id=revision.package_revision_id,
            )
        except NormalizationInvariantError as exc:
            raise IntakeInvariantError(message=exc.message) from exc
        terminalize_normalization_step(
            session,
            step=step,
            pending=normalization,
            now=now,
        )
    session.add(
        PackageRevisionDraft(
            package_revision_id=revision.package_revision_id,
            document_schema_version=DOCUMENT_SCHEMA_VERSION,
            document=aggregated.document,
            field_provenance=aggregated.field_provenance,
            updated_by=INTAKE_ACTOR_ID,
            updated_at=now,
        )
    )
    for artifact in artifacts:
        artifact.extraction_status = "succeeded"
    revision.status = PackageRevisionStatus.AWAITING_CONFIRMATION.value
    revision.revision_version += 1
    audit_metadata: dict[str, object] = {
        "artifact_count": len(artifacts),
        "segment_count": aggregated.segment_count,
        "revision_version": revision.revision_version,
    }
    norm_metadata = (
        normalization_audit_metadata(normalization) if normalization is not None else None
    )
    if norm_metadata is not None:
        audit_metadata["normalization"] = norm_metadata
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=INTAKE_ACTOR_ID,
        action="package_revision.intake_extraction_completed",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata=audit_metadata,
        occurred_at=now,
    )
    return IntakeResult(
        package_revision_id=revision.package_revision_id,
        work_phase=claimed.work_phase,
        outcome=IntakeOutcomeKind.COMPLETED,
        previous_revision_status=previous_status,
        revision_status=revision.status,
        revision_version=revision.revision_version,
        artifact_count=len(artifacts),
        draft_inserted=True,
    )


async def _commit_invalid_extract(
    session: AsyncSession,
    *,
    work: PackageRevisionIntakeWork,
    revision: PackageRevision,
    artifacts: list[SourceArtifact],
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    reason_code: str,
    hmac_key: bytes,
    lease_owner: str,
    now: datetime,
) -> IntakeResult:
    previous_status = revision.status
    require_package_revision_transition(
        PackageRevisionStatus.EXTRACTING,
        PackageRevisionStatus.INVALID,
        condition=PackageRevisionTransitionCondition.INVALID_EXTRACTION_OR_REFERENCE,
    )
    await complete_intake_work(
        session,
        package_revision_id=claimed.package_revision_id,
        work_phase=claimed.work_phase,
        lease_owner=lease_owner,
        fence_token=claimed.fence_token,
        now=now,
    )
    for artifact in artifacts:
        artifact.extraction_status = "failed"
    revision.status = PackageRevisionStatus.INVALID.value
    revision.revision_version += 1
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=INTAKE_ACTOR_ID,
        action="package_revision.intake_extraction_invalidated",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code=reason_code,
        metadata={
            "artifact_count": len(artifacts),
            "revision_version": revision.revision_version,
        },
        occurred_at=now,
    )
    return IntakeResult(
        package_revision_id=revision.package_revision_id,
        work_phase=claimed.work_phase,
        outcome=IntakeOutcomeKind.COMPLETED,
        previous_revision_status=previous_status,
        revision_status=revision.status,
        revision_version=revision.revision_version,
        artifact_count=len(artifacts),
        reason_code=reason_code,
    )


async def _load_locked_intake_state(
    session: AsyncSession,
    *,
    claimed: ClaimedIntakeOperation,
    snapshot: IntakeRevisionSnapshot,
    now: datetime,
) -> tuple[PackageRevisionIntakeWork, PackageRevision, list[SourceArtifact]]:
    work = (
        await session.execute(
            select(PackageRevisionIntakeWork)
            .where(
                PackageRevisionIntakeWork.package_revision_id
                == claimed.package_revision_id,
                PackageRevisionIntakeWork.work_phase == claimed.work_phase,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if work is None:
        raise IntakeLeaseLostError(
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
        )
    revision = (
        await session.execute(
            select(PackageRevision)
            .where(PackageRevision.package_revision_id == claimed.package_revision_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revision is None:
        raise IntakeInvariantError(
            message="intake persist requires the owning package revision"
        )
    assert_intake_claim_live(
        work,
        revision,
        lease_owner=claimed.lease_owner,
        fence_token=claimed.fence_token,
        now=now,
    )
    if revision.revision_version != snapshot.revision_version:
        raise IntakeLeaseLostError(
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
        )
    artifacts = (
        await session.execute(
            select(SourceArtifact)
            .where(SourceArtifact.package_revision_id == claimed.package_revision_id)
            .order_by(SourceArtifact.artifact_id.asc())
            .with_for_update()
        )
    ).scalars().all()
    _assert_artifact_snapshots_match(snapshot.artifacts, artifacts)
    return work, revision, list(artifacts)


async def _assert_extract_preconditions(
    session: AsyncSession,
    *,
    revision: PackageRevision,
) -> None:
    if revision.content_manifest_sha256 is None:
        raise IntakeInvariantError(
            message="deterministic_extract requires a content manifest digest"
        )
    draft_exists = (
        await session.execute(
            select(
                exists().where(
                    PackageRevisionDraft.package_revision_id
                    == revision.package_revision_id
                )
            )
        )
    ).scalar_one()
    if draft_exists:
        raise IntakeInvariantError(
            message="deterministic_extract found an existing package revision draft"
        )
    proposals_exist = (
        await session.execute(
            select(
                exists().where(
                    FactProposal.package_revision_id == revision.package_revision_id
                )
            )
        )
    ).scalar_one()
    if proposals_exist:
        raise IntakeInvariantError(
            message="deterministic_extract found existing fact proposals"
        )
    artifacts = (
        await session.execute(
            select(SourceArtifact).where(
                SourceArtifact.package_revision_id == revision.package_revision_id
            )
        )
    ).scalars().all()
    if not artifacts:
        raise IntakeInvariantError(
            message="deterministic_extract requires at least one source artifact"
        )
    if any(artifact.malware_scan_status != "clean" for artifact in artifacts):
        raise IntakeInvariantError(
            message="deterministic_extract requires every artifact to be clean"
        )


async def _mark_reconciliation_required(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    now_factory: Callable[[], datetime],
    normalization_step_id: uuid.UUID | None = None,
) -> None:
    await mark_normalization_and_intake_reconciliation_required(
        session_factory,
        claimed=claimed,
        lease_owner=lease_owner,
        now_factory=now_factory,
        normalization_step_id=normalization_step_id,
    )


async def _heartbeat_claim(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    lease_seconds: int,
    now_factory: Callable[[], datetime],
) -> None:
    async with session_scope(session_factory) as session:
        await heartbeat_intake_work(
            session,
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
            lease_owner=lease_owner,
            fence_token=claimed.fence_token,
            now=_require_aware_utc(now_factory()),
            lease_seconds=lease_seconds,
        )


def _scan_one_artifact(
    scanner: MalwareScanner,
    blob_store: BlobStore,
    artifact: ArtifactSnapshot,
) -> MalwareScanResult:
    try:
        return scanner.scan_stored_artifact(
            blob_store=blob_store,
            artifact=_artifact_namespace(artifact),
        )
    except MalwareScannerUnavailableError:
        return MalwareScanResult(
            MalwareScanOutcome.ERROR,
            reason_code="malware_scan_unavailable",
        )


def _operation_from_claim(
    claimed: ClaimedIntakeWork,
    *,
    lease_owner: str,
) -> ClaimedIntakeOperation:
    return ClaimedIntakeOperation(
        package_revision_id=claimed.work.package_revision_id,
        work_phase=claimed.work.work_phase,
        lease_owner=lease_owner,
        fence_token=claimed.fence_token,
        expected_revision_version=claimed.work.expected_revision_version,
    )


def _artifact_snapshot(artifact: SourceArtifact) -> ArtifactSnapshot:
    return ArtifactSnapshot(
        artifact_id=artifact.artifact_id,
        package_revision_id=artifact.package_revision_id,
        display_filename=artifact.display_filename,
        storage_key=artifact.storage_key,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        declared_media_type=artifact.declared_media_type,
        detected_media_type=artifact.detected_media_type,
        artifact_kind=artifact.artifact_kind,
        malware_scan_status=artifact.malware_scan_status,
        extraction_status=artifact.extraction_status,
    )


def _artifact_namespace(artifact: ArtifactSnapshot) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=artifact.artifact_id,
        package_revision_id=artifact.package_revision_id,
        display_filename=artifact.display_filename,
        storage_key=artifact.storage_key,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        declared_media_type=artifact.declared_media_type,
        detected_media_type=artifact.detected_media_type,
        artifact_kind=artifact.artifact_kind,
        malware_scan_status=artifact.malware_scan_status,
        extraction_status=artifact.extraction_status,
    )


def _revision_namespace(snapshot: IntakeRevisionSnapshot) -> SimpleNamespace:
    return SimpleNamespace(
        package_revision_id=snapshot.package_revision_id,
        profile_id=snapshot.profile_id,
        impact_level=snapshot.impact_level,
    )


def _system_namespace(snapshot: IntakeRevisionSnapshot) -> SimpleNamespace:
    return SimpleNamespace(
        system_id=snapshot.system_id,
        display_name=snapshot.system_display_name,
    )


def _extraction_context(artifact: ArtifactSnapshot) -> ExtractionContext:
    return ExtractionContext(
        declared_media_type=artifact.declared_media_type,
        detected_media_type=artifact.detected_media_type,
        declared_format=_MIME_TO_DECLARED_FORMAT.get(artifact.declared_media_type),
        artifact_kind=artifact.artifact_kind,
        filename=artifact.display_filename,
    )


def _require_scan_snapshot(snapshot: IntakeRevisionSnapshot) -> None:
    if snapshot.status != PackageRevisionStatus.SCANNING.value:
        raise IntakeInvariantError(
            message="malware_scan snapshot requires revision status scanning"
        )
    if not snapshot.artifacts:
        raise IntakeInvariantError(
            message="malware_scan snapshot requires source artifacts"
        )


def _require_extract_snapshot(snapshot: IntakeRevisionSnapshot) -> None:
    if snapshot.status != PackageRevisionStatus.EXTRACTING.value:
        raise IntakeInvariantError(
            message="deterministic_extract snapshot requires revision status extracting"
        )
    if not snapshot.artifacts:
        raise IntakeInvariantError(
            message="deterministic_extract snapshot requires source artifacts"
        )
    if any(artifact.malware_scan_status != "clean" for artifact in snapshot.artifacts):
        raise IntakeInvariantError(
            message="deterministic_extract snapshot requires clean artifacts"
        )


def _assert_scan_artifacts_pending(artifacts: list[SourceArtifact]) -> None:
    if any(artifact.malware_scan_status != "pending" for artifact in artifacts):
        raise IntakeInvariantError(
            message="malware_scan commit requires pending artifact scan statuses"
        )
    if any(artifact.extraction_status != "pending" for artifact in artifacts):
        raise IntakeInvariantError(
            message="malware_scan commit requires pending artifact extraction statuses"
        )


def _assert_artifact_snapshots_match(
    expected: tuple[ArtifactSnapshot, ...],
    actual: list[SourceArtifact],
) -> None:
    if len(expected) != len(actual):
        raise IntakeInvariantError(
            message="intake persist found a mismatched artifact snapshot count"
        )
    actual_by_id = {artifact.artifact_id: artifact for artifact in actual}
    for snapshot in expected:
        row = actual_by_id.get(snapshot.artifact_id)
        if row is None:
            raise IntakeInvariantError(
                message="intake persist found a missing artifact snapshot row"
            )
        if (
            row.sha256 != snapshot.sha256
            or row.size_bytes != snapshot.size_bytes
            or row.malware_scan_status != snapshot.malware_scan_status
            or row.extraction_status != snapshot.extraction_status
        ):
            raise IntakeInvariantError(
                message="intake persist found a changed artifact snapshot"
            )


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


__all__ = [
    "DEFAULT_LEASE_OWNER",
    "INTAKE_ACTOR_ID",
    "INTAKE_LEASE_SECONDS",
    "INTAKE_MAX_ATTEMPTS",
    "INTAKE_RETRY_BACKOFF_SECONDS",
    "ArtifactSnapshot",
    "ClaimedIntakeOperation",
    "IntakeConfigurationError",
    "IntakeOutcomeKind",
    "IntakeResult",
    "IntakeRevisionSnapshot",
    "NormalizationDependencies",
    "build_intake_lease_owner",
    "drain_intake",
    "process_next_intake_operation",
    "recover_intake_leases",
    "require_intake_runtime",
    "resolve_intake_allowed_data_origins",
]
