"""Deterministic ``dev_local`` intake for synthetic JSON package revisions.

This module deliberately does not provide a production malware scanner or a
customer-file extraction path. Each function mutates at most one lifecycle
transition in the caller-owned transaction so scan and extraction commits are
independently observable and replay-safe.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.blobs import BlobStore
from ato_service.db.models import FactProposal, PackageRevision, SourceArtifact
from ato_service.lifecycle_transitions import (
    PackageRevisionStatus,
    PackageRevisionTransitionCondition,
    require_package_revision_transition,
)
from ato_service.runtime_config import RuntimeConfig, RuntimeConfigError
from ato_service.source_artifacts import read_source_artifact_bytes

SYNTHETIC_INTAKE_ACTOR_ID = "synthetic-intake-worker"
SYNTHETIC_DATA_ORIGIN = "synthetic"
JSON_MEDIA_TYPE = "application/json"


class SyntheticIntakeConfigurationError(RuntimeConfigError):
    """Raised when the synthetic worker is started outside ``dev_local``."""


class SyntheticIntakeInvariantError(RuntimeError):
    """Raised when persisted intake state cannot be advanced safely."""


class SyntheticJsonExtractionError(ValueError):
    """Raised when synthetic JSON cannot produce deterministic fact proposals."""

    def __init__(self, message: str, *, error_code: str = "source_parse_failed") -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SyntheticIntakeResult:
    """One committed-by-caller synthetic intake transition."""

    package_revision_id: uuid.UUID
    previous_status: str
    status: str
    revision_version: int
    artifact_count: int
    proposal_count: int


def require_synthetic_intake_runtime(config: RuntimeConfig) -> None:
    """Fail closed unless the explicitly development-only profile is active."""
    if config.runtime_profile != "dev_local":
        raise SyntheticIntakeConfigurationError(
            "synthetic intake worker requires runtime_profile=dev_local"
        )


def _eligible_revision_statement(status: PackageRevisionStatus) -> Any:
    """Claim one synthetic all-JSON revision without blocking another worker."""
    non_json_artifact = exists().where(
        SourceArtifact.package_revision_id == PackageRevision.package_revision_id,
        or_(
            SourceArtifact.declared_media_type != JSON_MEDIA_TYPE,
            SourceArtifact.detected_media_type != JSON_MEDIA_TYPE,
        ),
    )
    return (
        select(PackageRevision)
        .where(
            PackageRevision.status == status.value,
            PackageRevision.data_origin == SYNTHETIC_DATA_ORIGIN,
            ~non_json_artifact,
        )
        .order_by(
            PackageRevision.created_at.asc(),
            PackageRevision.package_revision_id.asc(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _source_artifacts_statement(package_revision_id: uuid.UUID) -> Any:
    return (
        select(SourceArtifact)
        .where(SourceArtifact.package_revision_id == package_revision_id)
        .order_by(SourceArtifact.artifact_id.asc())
    )


def _fact_proposals_exist_statement(package_revision_id: uuid.UUID) -> Any:
    return select(
        exists().where(FactProposal.package_revision_id == package_revision_id)
    )


async def process_next_synthetic_scan(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    now: datetime,
) -> SyntheticIntakeResult | None:
    """Mark one eligible synthetic JSON revision clean and start extraction."""
    validated_now = _require_aware_utc(now)
    revision_result = await session.execute(
        _eligible_revision_statement(PackageRevisionStatus.SCANNING)
    )
    revision = revision_result.scalar_one_or_none()
    if revision is None:
        return None

    artifacts = await _load_artifacts(session, revision.package_revision_id)
    _require_revision_foundation(revision, artifacts)
    for artifact in artifacts:
        if artifact.malware_scan_status != "pending":
            raise SyntheticIntakeInvariantError(
                "scanning revision contains a non-pending synthetic scan result"
            )
        if artifact.extraction_status != "pending":
            raise SyntheticIntakeInvariantError(
                "scanning revision contains a non-pending extraction result"
            )

    require_package_revision_transition(
        PackageRevisionStatus.SCANNING,
        PackageRevisionStatus.EXTRACTING,
        condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
    )
    for artifact in artifacts:
        artifact.malware_scan_status = "clean"
    revision.status = PackageRevisionStatus.EXTRACTING.value
    revision.revision_version += 1

    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=SYNTHETIC_INTAKE_ACTOR_ID,
        action="package_revision.synthetic_scan_completed",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "artifact_count": len(artifacts),
            "revision_version": revision.revision_version,
        },
        occurred_at=validated_now,
    )
    return _result(
        revision,
        previous_status=PackageRevisionStatus.SCANNING,
        artifact_count=len(artifacts),
        proposal_count=0,
    )


async def process_next_synthetic_extraction(
    session: AsyncSession,
    *,
    blob_store: BlobStore,
    hmac_key: bytes,
    now: datetime,
) -> SyntheticIntakeResult | None:
    """Extract one eligible synthetic JSON revision into pending proposals."""
    validated_now = _require_aware_utc(now)
    revision_result = await session.execute(
        _eligible_revision_statement(PackageRevisionStatus.EXTRACTING)
    )
    revision = revision_result.scalar_one_or_none()
    if revision is None:
        return None

    existing_result = await session.execute(
        _fact_proposals_exist_statement(revision.package_revision_id)
    )
    if existing_result.scalar_one():
        raise SyntheticIntakeInvariantError(
            "extracting revision already contains fact proposals"
        )

    artifacts = await _load_artifacts(session, revision.package_revision_id)
    _require_revision_foundation(revision, artifacts)
    for artifact in artifacts:
        if artifact.malware_scan_status != "clean":
            raise SyntheticIntakeInvariantError(
                "extracting revision contains an artifact without a clean synthetic scan"
            )
        if artifact.extraction_status != "pending":
            raise SyntheticIntakeInvariantError(
                "extracting revision contains a non-pending extraction result"
            )

    proposals: list[FactProposal] = []
    seen_pointers: set[str] = set()
    try:
        for artifact in artifacts:
            facts = await asyncio.to_thread(
                _extract_json_facts,
                blob_store,
                artifact,
            )
            for json_pointer, proposed_value in facts:
                if json_pointer in seen_pointers:
                    raise SyntheticJsonExtractionError(
                        "synthetic artifacts propose the same canonical JSON pointer",
                        error_code="duplicate_canonical_id",
                    )
                seen_pointers.add(json_pointer)
                proposals.append(
                    FactProposal(
                        fact_proposal_id=uuid.uuid5(
                            artifact.artifact_id,
                            json_pointer,
                        ),
                        package_revision_id=revision.package_revision_id,
                        json_pointer=json_pointer,
                        proposed_value=proposed_value,
                        source_artifact_id=artifact.artifact_id,
                        source_sha256=artifact.sha256,
                        source_locator={
                            "kind": "json_pointer",
                            "json_pointer": json_pointer,
                        },
                        extraction_method="deterministic",
                        model_step_id=None,
                        review_status="pending",
                        reviewed_by=None,
                        reviewed_at=None,
                    )
                )
        if not proposals:
            raise SyntheticJsonExtractionError(
                "synthetic JSON contains no addressable facts"
            )
    except SyntheticJsonExtractionError as exc:
        return await _invalidate_extraction(
            session,
            revision=revision,
            artifacts=artifacts,
            hmac_key=hmac_key,
            now=validated_now,
            reason_code=exc.error_code,
        )

    require_package_revision_transition(
        PackageRevisionStatus.EXTRACTING,
        PackageRevisionStatus.AWAITING_CONFIRMATION,
        condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
    )
    for proposal in proposals:
        session.add(proposal)
    for artifact in artifacts:
        artifact.extraction_status = "succeeded"
    revision.status = PackageRevisionStatus.AWAITING_CONFIRMATION.value
    revision.revision_version += 1

    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=SYNTHETIC_INTAKE_ACTOR_ID,
        action="package_revision.synthetic_extraction_completed",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "artifact_count": len(artifacts),
            "proposal_count": len(proposals),
            "revision_version": revision.revision_version,
        },
        occurred_at=validated_now,
    )
    return _result(
        revision,
        previous_status=PackageRevisionStatus.EXTRACTING,
        artifact_count=len(artifacts),
        proposal_count=len(proposals),
    )


async def process_next_synthetic_intake(
    session: AsyncSession,
    *,
    blob_store: BlobStore,
    hmac_key: bytes,
    now: datetime,
) -> SyntheticIntakeResult | None:
    """Advance one transition, finishing extraction work before new scans."""
    extraction = await process_next_synthetic_extraction(
        session,
        blob_store=blob_store,
        hmac_key=hmac_key,
        now=now,
    )
    if extraction is not None:
        return extraction
    return await process_next_synthetic_scan(
        session,
        hmac_key=hmac_key,
        now=now,
    )


async def _load_artifacts(
    session: AsyncSession,
    package_revision_id: uuid.UUID,
) -> list[SourceArtifact]:
    result = await session.execute(
        _source_artifacts_statement(package_revision_id)
    )
    return list(result.scalars().all())


def _require_revision_foundation(
    revision: PackageRevision,
    artifacts: list[SourceArtifact],
) -> None:
    if revision.data_origin != SYNTHETIC_DATA_ORIGIN:
        raise SyntheticIntakeInvariantError(
            "synthetic intake claimed a non-synthetic revision"
        )
    if revision.content_manifest_sha256 is None:
        raise SyntheticIntakeInvariantError(
            "synthetic intake revision is missing its content manifest digest"
        )
    if not artifacts:
        raise SyntheticIntakeInvariantError(
            "synthetic intake revision has no source artifacts"
        )
    if any(
        artifact.declared_media_type != JSON_MEDIA_TYPE
        or artifact.detected_media_type != JSON_MEDIA_TYPE
        for artifact in artifacts
    ):
        raise SyntheticIntakeInvariantError(
            "synthetic intake supports JSON source artifacts only"
        )


def _extract_json_facts(
    blob_store: BlobStore,
    artifact: SourceArtifact,
) -> tuple[tuple[str, Any], ...]:
    raw_bytes = read_source_artifact_bytes(blob_store, artifact)
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyntheticJsonExtractionError(
            "synthetic artifact is not valid UTF-8 JSON"
        ) from exc

    facts = tuple(_iter_json_facts(document))
    if not facts:
        raise SyntheticJsonExtractionError(
            "synthetic JSON contains no addressable facts"
        )
    return facts


def _iter_json_facts(document: Any) -> Any:
    stack: list[tuple[str, Any]] = [("", document)]
    while stack:
        pointer, value = stack.pop()
        if isinstance(value, dict):
            if not value:
                if pointer:
                    yield pointer, {}
                continue
            for key in sorted(value, reverse=True):
                escaped_key = key.replace("~", "~0").replace("/", "~1")
                child_pointer = f"{pointer}/{escaped_key}"
                _require_json_pointer_length(child_pointer)
                stack.append((child_pointer, value[key]))
            continue
        if isinstance(value, list):
            if not value:
                if pointer:
                    yield pointer, []
                continue
            for index in range(len(value) - 1, -1, -1):
                child_pointer = f"{pointer}/{index}"
                _require_json_pointer_length(child_pointer)
                stack.append((child_pointer, value[index]))
            continue
        if not pointer:
            raise SyntheticJsonExtractionError(
                "synthetic JSON root must be an object or array"
            )
        yield pointer, value


def _require_json_pointer_length(json_pointer: str) -> None:
    if len(json_pointer) > 2000:
        raise SyntheticJsonExtractionError(
            "synthetic JSON pointer exceeds the domain limit"
        )


async def _invalidate_extraction(
    session: AsyncSession,
    *,
    revision: PackageRevision,
    artifacts: list[SourceArtifact],
    hmac_key: bytes,
    now: datetime,
    reason_code: str,
) -> SyntheticIntakeResult:
    require_package_revision_transition(
        PackageRevisionStatus.EXTRACTING,
        PackageRevisionStatus.INVALID,
        condition=PackageRevisionTransitionCondition.INVALID_EXTRACTION_OR_REFERENCE,
    )
    for artifact in artifacts:
        artifact.extraction_status = "failed"
    revision.status = PackageRevisionStatus.INVALID.value
    revision.revision_version += 1
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=SYNTHETIC_INTAKE_ACTOR_ID,
        action="package_revision.synthetic_extraction_invalidated",
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
    return _result(
        revision,
        previous_status=PackageRevisionStatus.EXTRACTING,
        artifact_count=len(artifacts),
        proposal_count=0,
    )


def _result(
    revision: PackageRevision,
    *,
    previous_status: PackageRevisionStatus,
    artifact_count: int,
    proposal_count: int,
) -> SyntheticIntakeResult:
    return SyntheticIntakeResult(
        package_revision_id=revision.package_revision_id,
        previous_status=previous_status.value,
        status=revision.status,
        revision_version=revision.revision_version,
        artifact_count=artifact_count,
        proposal_count=proposal_count,
    )


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


__all__ = [
    "JSON_MEDIA_TYPE",
    "SYNTHETIC_DATA_ORIGIN",
    "SYNTHETIC_INTAKE_ACTOR_ID",
    "SyntheticIntakeConfigurationError",
    "SyntheticIntakeInvariantError",
    "SyntheticIntakeResult",
    "process_next_synthetic_extraction",
    "process_next_synthetic_intake",
    "process_next_synthetic_scan",
    "require_synthetic_intake_runtime",
]
