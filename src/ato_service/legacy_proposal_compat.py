"""Bounded compatibility helpers for legacy FactProposal revisions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.blobs import BlobStore
from ato_service.db.models import FactProposal, PackageRevision, PackageRevisionDraft, System
from ato_service.draft_builder import (
    DOCUMENT_SCHEMA_VERSION,
    AggregatedIntakeDraft,
    DraftBuildError,
    build_initial_draft,
    validate_package_draft_document,
)
from ato_service.extraction import ExtractionContext, ExtractionLimits, VisionPolicy, extract_content
from ato_service.lifecycle_transitions import PackageRevisionStatus
from ato_service.source_artifacts import read_source_artifact_bytes

_LEGACY_MIGRATION_ACTOR_ID = "legacy-proposal-compat"
_DEV_LOCAL_EXTRACTION_LIMITS = ExtractionLimits(
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


class LegacyProposalMigrationError(ValueError):
    """Raised when legacy proposals cannot be assembled into a draft safely."""

    error_code = "reconciliation_required"


def _set_json_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    if pointer == "":
        raise LegacyProposalMigrationError("legacy proposal used the empty JSON pointer")
    parts = pointer.lstrip("/").split("/")
    current: Any = document
    for raw_part in parts[:-1]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            index = int(part)
            current = current[index]
            continue
        if part not in current or not isinstance(current[part], (dict, list)):
            current[part] = {}
        current = current[part]
    leaf = parts[-1].replace("~1", "/").replace("~0", "~")
    if isinstance(current, list):
        current[int(leaf)] = value
    else:
        current[leaf] = value


def _reconstruct_manifest_from_proposals(
    proposals: Sequence[FactProposal],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    for proposal in sorted(proposals, key=lambda row: row.json_pointer):
        _set_json_pointer(manifest, proposal.json_pointer, proposal.proposed_value)
    if not manifest:
        raise LegacyProposalMigrationError("legacy revision has no proposal values")
    return manifest


def _synthetic_artifact_from_manifest(
    *,
    revision: PackageRevision,
    manifest: dict[str, Any],
    source_proposal: FactProposal,
) -> Any:
    return type(
        "LegacySyntheticArtifact",
        (),
        {
            "artifact_id": source_proposal.source_artifact_id,
            "sha256": source_proposal.source_sha256,
            "size_bytes": len(json.dumps(manifest, sort_keys=True).encode("utf-8")),
            "display_filename": "legacy-proposals.json",
            "declared_media_type": "application/json",
            "detected_media_type": "application/json",
            "artifact_kind": "manifest",
        },
    )()


def assemble_draft_from_legacy_proposals(
    *,
    revision: PackageRevision,
    system: System,
    proposals: Sequence[FactProposal],
) -> AggregatedIntakeDraft:
    """Assemble one schema-valid draft from published legacy proposal rows."""
    if revision.status != PackageRevisionStatus.AWAITING_CONFIRMATION.value:
        raise LegacyProposalMigrationError(
            "legacy proposal migration requires awaiting_confirmation"
        )
    if not proposals:
        raise LegacyProposalMigrationError("legacy revision has no fact proposals")

    manifest = _reconstruct_manifest_from_proposals(proposals)
    artifact = _synthetic_artifact_from_manifest(
        revision=revision,
        manifest=manifest,
        source_proposal=proposals[0],
    )
    content = json.dumps(manifest, sort_keys=True).encode("utf-8")
    outcome = extract_content(
        content_bytes=content,
        sha256=artifact.sha256,
        limits=_DEV_LOCAL_EXTRACTION_LIMITS,
        context=ExtractionContext(
            declared_media_type="application/json",
            detected_media_type="application/json",
            declared_format="json",
            artifact_kind="manifest",
            filename="legacy-proposals.json",
        ),
        vision_policy=VisionPolicy(vision_allowed=False),
    )
    try:
        draft = build_initial_draft(
            revision=revision,
            system=system,
            artifacts=[artifact],
            artifact_outcomes=[(artifact, outcome)],
        )
    except DraftBuildError as exc:
        raise LegacyProposalMigrationError(str(exc)) from exc
    validate_package_draft_document(draft.document)
    return draft


def assemble_draft_from_source_artifacts(
    *,
    revision: PackageRevision,
    system: System,
    artifacts: Sequence[Any],
    blob_store: BlobStore,
) -> AggregatedIntakeDraft | None:
    """Re-extract a draft from persisted source artifacts when available."""
    if not artifacts:
        return None
    outcomes: list[tuple[Any, Any]] = []
    for artifact in artifacts:
        content = read_source_artifact_bytes(blob_store, artifact)
        outcome = extract_content(
            content_bytes=content,
            sha256=artifact.sha256,
            limits=_DEV_LOCAL_EXTRACTION_LIMITS,
            context=ExtractionContext(
                declared_media_type=artifact.declared_media_type,
                detected_media_type=artifact.detected_media_type,
                declared_format=getattr(artifact, "declared_format", "json"),
                artifact_kind=artifact.artifact_kind,
                filename=artifact.display_filename,
            ),
            vision_policy=VisionPolicy(vision_allowed=False),
        )
        outcomes.append((artifact, outcome))
    try:
        return build_initial_draft(
            revision=revision,
            system=system,
            artifacts=list(artifacts),
            artifact_outcomes=outcomes,
        )
    except DraftBuildError:
        return None


async def migrate_legacy_revision_draft(
    session: AsyncSession,
    *,
    revision: PackageRevision,
    system: System,
    proposals: Sequence[FactProposal],
    hmac_key: bytes,
    actor_id: str = _LEGACY_MIGRATION_ACTOR_ID,
    occurred_at: datetime | None = None,
) -> PackageRevisionDraft:
    """Persist one draft assembled from compatible legacy proposals without deleting history."""
    aggregated = assemble_draft_from_legacy_proposals(
        revision=revision,
        system=system,
        proposals=proposals,
    )
    now = occurred_at or datetime.now(timezone.utc)
    draft = PackageRevisionDraft(
        package_revision_id=revision.package_revision_id,
        document_schema_version=DOCUMENT_SCHEMA_VERSION,
        document=aggregated.document,
        field_provenance=aggregated.field_provenance,
        updated_by=actor_id,
        updated_at=now,
    )
    session.add(draft)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=actor_id,
        action="package_revision.legacy_proposal_draft_migrated",
        object_type="package_revision",
        object_id=str(revision.package_revision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "proposal_count": len(proposals),
            "revision_version": revision.revision_version,
        },
        occurred_at=now,
    )
    return draft


async def ensure_legacy_draft_for_read(
    session: AsyncSession,
    *,
    revision: PackageRevision,
    system: System,
    hmac_key: bytes | None,
) -> PackageRevisionDraft | None:
    """Lazy one-time draft migration for legacy awaiting_confirmation revisions."""
    if revision.status != PackageRevisionStatus.AWAITING_CONFIRMATION.value:
        return None
    if hmac_key is None:
        return None

    proposal_result = await session.execute(
        select(FactProposal)
        .where(FactProposal.package_revision_id == revision.package_revision_id)
        .order_by(FactProposal.json_pointer.asc(), FactProposal.fact_proposal_id.asc())
    )
    proposals = list(proposal_result.scalars())
    if not proposals:
        return None

    try:
        return await migrate_legacy_revision_draft(
            session,
            revision=revision,
            system=system,
            proposals=proposals,
            hmac_key=hmac_key,
        )
    except LegacyProposalMigrationError:
        return None


__all__ = [
    "LegacyProposalMigrationError",
    "assemble_draft_from_legacy_proposals",
    "assemble_draft_from_source_artifacts",
    "ensure_legacy_draft_for_read",
    "migrate_legacy_revision_draft",
]
