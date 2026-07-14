"""Deterministic ``dev_local`` intake for synthetic JSON package revisions.

This module deliberately does not provide a production malware scanner or a
customer-file extraction path. Each function mutates at most one lifecycle
transition in the caller-owned transaction so scan and extraction commits are
independently observable and replay-safe.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.blobs import BlobStore
from ato_service.db.models import PackageRevision, PackageRevisionDraft, SourceArtifact, System
from ato_service.draft_builder import DOCUMENT_SCHEMA_VERSION, DraftBuildError, build_initial_draft
from ato_service.extraction import ExtractionContext, ExtractionLimits, VisionPolicy, extract_content
from ato_service.extraction.errors import ExtractionError
from ato_service.lifecycle_transitions import (
    PackageRevisionStatus,
    PackageRevisionTransitionCondition,
    require_package_revision_transition,
)
from ato_service.runtime_config import RuntimeConfig, RuntimeConfigError
from ato_service.source_artifacts import (
    SourceTypeMismatchError,
    read_source_artifact_bytes,
)

SYNTHETIC_INTAKE_ACTOR_ID = "synthetic-intake-worker"
SYNTHETIC_DATA_ORIGIN = "synthetic"
JSON_MEDIA_TYPE = "application/json"
_SYNTHETIC_EXTRACTION_LIMITS = ExtractionLimits(
    max_pdf_pages_per_file=200,
    max_extracted_text_characters_per_file=2_000_000,
    max_zip_members_per_archive=500,
    max_zip_uncompressed_bytes_per_archive=104_857_600,
    max_zip_decompression_ratio=100,
    max_xml_depth=64,
    max_xml_elements=100_000,
    max_xml_attributes_per_element=128,
    max_xml_text_node_characters=1_048_576,
)


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
    draft_inserted: bool = False


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


def _draft_exists_statement(package_revision_id: uuid.UUID) -> Any:
    return select(
        exists().where(PackageRevisionDraft.package_revision_id == package_revision_id)
    )


def _load_system_statement(system_id: uuid.UUID) -> Any:
    return select(System).where(System.system_id == system_id)


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
    """Extract one eligible synthetic JSON revision into a package draft."""
    validated_now = _require_aware_utc(now)
    revision_result = await session.execute(
        _eligible_revision_statement(PackageRevisionStatus.EXTRACTING)
    )
    revision = revision_result.scalar_one_or_none()
    if revision is None:
        return None

    existing_result = await session.execute(
        _draft_exists_statement(revision.package_revision_id)
    )
    if existing_result.scalar_one():
        raise SyntheticIntakeInvariantError(
            "extracting revision already contains a package revision draft"
        )

    system_result = await session.execute(_load_system_statement(revision.system_id))
    system = system_result.scalar_one_or_none()
    if system is None:
        raise SyntheticIntakeInvariantError(
            "extracting revision is missing its owning system"
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

    try:
        aggregated = _build_synthetic_draft(
            revision=revision,
            system=system,
            artifacts=artifacts,
            blob_store=blob_store,
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
    except SourceTypeMismatchError:
        return await _invalidate_extraction(
            session,
            revision=revision,
            artifacts=artifacts,
            hmac_key=hmac_key,
            now=validated_now,
            reason_code="source_type_mismatch",
        )
    except DraftBuildError as exc:
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
    session.add(
        PackageRevisionDraft(
            package_revision_id=revision.package_revision_id,
            document_schema_version=DOCUMENT_SCHEMA_VERSION,
            document=aggregated.document,
            field_provenance=aggregated.field_provenance,
            updated_by=SYNTHETIC_INTAKE_ACTOR_ID,
            updated_at=validated_now,
        )
    )
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
            "segment_count": aggregated.segment_count,
            "revision_version": revision.revision_version,
        },
        occurred_at=validated_now,
    )
    return _result(
        revision,
        previous_status=PackageRevisionStatus.EXTRACTING,
        artifact_count=len(artifacts),
        proposal_count=0,
        draft_inserted=True,
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


def _build_synthetic_draft(
    *,
    revision: PackageRevision,
    system: System,
    artifacts: list[SourceArtifact],
    blob_store: BlobStore,
) -> Any:
    outcomes: list[tuple[SourceArtifact, Any]] = []
    for artifact in artifacts:
        raw_bytes = read_source_artifact_bytes(blob_store, artifact)
        try:
            json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SyntheticJsonExtractionError(
                "synthetic artifact is not valid UTF-8 JSON"
            ) from exc
        try:
            outcome = extract_content(
                content_bytes=raw_bytes,
                sha256=artifact.sha256,
                limits=_SYNTHETIC_EXTRACTION_LIMITS,
                context=ExtractionContext(
                    declared_media_type=artifact.declared_media_type,
                    detected_media_type=artifact.detected_media_type,
                    declared_format="json",
                    artifact_kind=artifact.artifact_kind,
                    filename=artifact.display_filename,
                ),
                vision_policy=VisionPolicy(vision_allowed=False),
            )
        except ExtractionError as exc:
            raise SyntheticJsonExtractionError(
                str(exc),
                error_code=exc.error_code,
            ) from exc
        if not outcome.segments:
            raise SyntheticJsonExtractionError(
                "synthetic JSON contains no addressable facts"
            )
        outcomes.append((artifact, outcome))
    return build_initial_draft(
        revision=revision,
        system=system,
        artifacts=artifacts,
        artifact_outcomes=outcomes,
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
    draft_inserted: bool = False,
) -> SyntheticIntakeResult:
    return SyntheticIntakeResult(
        package_revision_id=revision.package_revision_id,
        previous_status=previous_status.value,
        status=revision.status,
        revision_version=revision.revision_version,
        artifact_count=artifact_count,
        proposal_count=proposal_count,
        draft_inserted=draft_inserted,
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
