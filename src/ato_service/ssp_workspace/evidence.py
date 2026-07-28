"""Direct workspace evidence ingestion and bounded extraction."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.blobs import BlobStore
from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.router import extract_content
from ato_service.extraction.types import ExtractionContext, VisionPolicy
from ato_service.runtime_config import RuntimeConfig
from ato_service.ssp_workspace.contracts import (
    ControlState,
    EvidenceLink,
    FactContent,
    Provenance,
    RevisionContent,
    SectionState,
)
from ato_service.ssp_workspace.persistence import (
    StaleWorkspaceRevisionError,
    WorkspaceNotFoundError,
    save_revision,
)
from ato_service.ssp_workspace.vision import (
    VisionConfigurationError,
    VisionExtractionError,
    VisionExtractionRequest,
    extract_screenshot_facts_with_config,
)


class EvidenceUploadError(ValueError):
    error_code = "evidence_upload_failed"


class EvidenceRemovalError(ValueError):
    error_code = "illegal_state_transition"


async def ingest_workspace_evidence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    filename: str,
    media_type: str,
    content: bytes,
    actor_id: str,
    now: datetime,
    blob_store: BlobStore,
    config: RuntimeConfig,
    audit_hmac_key: bytes,
) -> Any:
    """Store, extract, and revision-bind one file without legacy package objects."""

    from ato_service.db.models import (
        SspEvidenceArtifact,
        SspWorkspace,
        SspWorkspaceRevision,
    )

    normalized_filename = filename.strip()
    normalized_media_type = media_type.strip().lower()
    if not normalized_filename:
        raise EvidenceUploadError("filename cannot be empty")
    if len(normalized_filename) > 255:
        raise EvidenceUploadError("filename exceeds 255 characters")
    if not normalized_media_type or len(normalized_media_type) > 255:
        raise EvidenceUploadError("media_type is invalid")
    if not content:
        raise EvidenceUploadError("evidence file cannot be empty")
    if len(content) > config.limits.max_single_file_bytes:
        raise EvidenceUploadError("evidence file exceeds configured limit")

    workspace = (
        await session.execute(
            select(SspWorkspace)
            .where(SspWorkspace.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if workspace is None:
        from ato_service.ssp_workspace.persistence import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("workspace not found")
    if workspace.current_revision_id != expected_revision_id:
        from ato_service.ssp_workspace.persistence import StaleWorkspaceRevisionError

        raise StaleWorkspaceRevisionError("workspace revision changed")
    revision = (
        await session.execute(
            select(SspWorkspaceRevision).where(
                SspWorkspaceRevision.revision_id == expected_revision_id,
                SspWorkspaceRevision.workspace_id == workspace_id,
            )
        )
    ).scalar_one()
    stored = blob_store.store_stream(
        BytesIO(content),
        max_bytes=config.limits.max_single_file_bytes,
    )
    duplicate = (
        await session.execute(
            select(SspEvidenceArtifact).where(
                SspEvidenceArtifact.workspace_id == workspace_id,
                SspEvidenceArtifact.sha256 == stored.sha256,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        if duplicate.removed_at is not None:
            duplicate.removed_at = None
            duplicate.removed_by = None
            restored_facts = tuple(
                FactContent(
                    key=str(segment["fact_key"]),
                    value=segment["text"],
                    provenance=Provenance.EXTRACTED,
                    evidence=(
                        EvidenceLink(
                            artifact_id=duplicate.evidence_artifact_id,
                            locator=dict(segment["locator"]),
                        ),
                    ),
                )
                for segment in duplicate.extracted_segments
                if all(
                    key in segment
                    for key in ("fact_key", "text", "locator")
                )
                and isinstance(segment["locator"], dict)
            )
            if restored_facts:
                current = RevisionContent.model_validate(revision.content)
                fact_by_key = {item.key: item for item in current.facts}
                for fact in restored_facts:
                    fact_by_key[fact.key] = fact
                await save_revision(
                    session,
                    workspace_id=workspace_id,
                    content=current.model_copy(
                        update={
                            "facts": tuple(
                                fact_by_key[key] for key in sorted(fact_by_key)
                            )
                        }
                    ),
                    created_by=actor_id,
                    now=now,
                    expected_revision_id=expected_revision_id,
                )
            await append_audit_event(
                session,
                hmac_key=audit_hmac_key,
                actor_type="user",
                actor_id=actor_id,
                action="ssp_evidence_restored",
                object_type="ssp_workspace",
                object_id=str(workspace_id),
                outcome="succeeded",
                reason_code=None,
                metadata={
                    "evidence_artifact_id": str(
                        duplicate.evidence_artifact_id
                    ),
                    "sha256": duplicate.sha256,
                    "fact_count": len(restored_facts),
                },
                occurred_at=now,
            )
            await session.flush()
        return duplicate

    artifact_id = uuid.uuid4()
    facts: list[FactContent] = []
    extracted_segments: list[dict[str, Any]] = []
    detected_format: str | None = None
    status = "processed"
    failure_code: str | None = None
    try:
        outcome = extract_content(
            content_bytes=content,
            sha256=stored.sha256,
            context=ExtractionContext(
                declared_media_type=normalized_media_type,
                detected_media_type=None,
                declared_format=None,
                artifact_kind=None,
                filename=normalized_filename,
            ),
            limits=config.extraction_limits,
            vision_policy=VisionPolicy(
                vision_allowed=config.vision_model_enabled
            ),
        )
        detected_format = outcome.detected_format
        for segment in outcome.segments:
            key = f"evidence.{artifact_id}.{segment.segment_index}"
            link = EvidenceLink(
                artifact_id=artifact_id,
                locator=segment.locator,
            )
            facts.append(
                FactContent(
                    key=key,
                    value=segment.text,
                    provenance=Provenance.EXTRACTED,
                    evidence=(link,),
                )
            )
            extracted_segments.append(
                {
                    "fact_key": key,
                    "text": segment.text,
                    "locator": segment.locator,
                    "extraction_method": segment.extraction_method,
                }
            )
        if outcome.status == "vision_deferred":
            if not config.vision_model_enabled:
                extracted_segments.append(
                    {
                        "status": "vision_deferred",
                        "reason": "vision_not_configured",
                    }
                )
            elif outcome.detected_format in {"png", "jpeg", "webp"}:
                vision_result = await extract_screenshot_facts_with_config(
                    VisionExtractionRequest(
                        source_id=str(artifact_id),
                        content=content,
                        declared_media_type=normalized_media_type,
                        filename=normalized_filename,
                    ),
                    config=config,
                )
                for item in vision_result.facts:
                    locator = {
                        "kind": "image_region",
                        "x": item.locator.x,
                        "y": item.locator.y,
                        "width": item.locator.width,
                        "height": item.locator.height,
                        "excerpt": item.excerpt,
                    }
                    facts.append(
                        FactContent(
                            key=item.fact_id,
                            value=item.text,
                            provenance=Provenance.EXTRACTED,
                            evidence=(
                                EvidenceLink(
                                    artifact_id=artifact_id,
                                    locator=locator,
                                ),
                            ),
                        )
                    )
                    extracted_segments.append(
                        {
                            "fact_key": item.fact_id,
                            "text": item.text,
                            "locator": locator,
                            "extraction_method": "vision",
                        }
                    )
            else:
                extracted_segments.append(
                    {
                        "status": "vision_deferred",
                        "reason": "scanned_pdf_requires_page_rendering",
                    }
                )
    except (ExtractionError, VisionConfigurationError, VisionExtractionError):
        status = "failed"
        failure_code = "evidence_extraction_failed"

    artifact = SspEvidenceArtifact(
        evidence_artifact_id=artifact_id,
        workspace_id=workspace_id,
        source_artifact_id=None,
        storage_key=stored.storage_key,
        size_bytes=stored.size_bytes,
        display_filename=normalized_filename,
        media_type=normalized_media_type,
        detected_format=detected_format,
        sha256=stored.sha256,
        status=status,
        extracted_segments=extracted_segments,
        uploaded_by=actor_id,
        uploaded_at=now,
        processed_at=now,
        failure_code=failure_code,
        removed_at=None,
        removed_by=None,
    )
    session.add(artifact)
    await session.flush()
    current = RevisionContent.model_validate(revision.content)
    if facts:
        fact_by_key = {item.key: item for item in current.facts}
        for fact in facts:
            fact_by_key[fact.key] = fact
        await save_revision(
            session,
            workspace_id=workspace_id,
            content=current.model_copy(
                update={"facts": tuple(fact_by_key[key] for key in sorted(fact_by_key))}
            ),
            created_by=actor_id,
            now=now,
            expected_revision_id=expected_revision_id,
        )
    await append_audit_event(
        session,
        hmac_key=audit_hmac_key,
        actor_type="user",
        actor_id=actor_id,
        action="ssp_evidence_uploaded",
        object_type="ssp_workspace",
        object_id=str(workspace_id),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "evidence_artifact_id": str(artifact_id),
            "sha256": stored.sha256,
            "status": status,
            "fact_count": len(facts),
        },
        occurred_at=now,
    )
    return artifact


async def remove_workspace_evidence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    evidence_artifact_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    """Hide one artifact and remove its extracted facts before analysis begins."""

    from ato_service.db.models import (
        SspAgentPatch,
        SspEvidenceArtifact,
        SspWorkspace,
        SspWorkspaceRevision,
    )

    workspace = (
        await session.execute(
            select(SspWorkspace)
            .where(SspWorkspace.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError("workspace not found")
    if workspace.current_revision_id != expected_revision_id:
        raise StaleWorkspaceRevisionError("workspace revision changed")
    revision = (
        await session.execute(
            select(SspWorkspaceRevision).where(
                SspWorkspaceRevision.revision_id == expected_revision_id,
                SspWorkspaceRevision.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if revision is None:
        raise WorkspaceNotFoundError("workspace revision not found")

    content = RevisionContent.model_validate(revision.content)
    analysis_started = (
        any(item.state is not SectionState.EMPTY for item in content.sections)
        or any(item.state is not ControlState.EMPTY for item in content.controls)
        or bool(content.questions)
        or (
            await session.execute(
                select(SspAgentPatch.patch_id).where(
                    SspAgentPatch.workspace_id == workspace_id
                )
            )
        ).first()
        is not None
    )
    if analysis_started:
        raise EvidenceRemovalError(
            "evidence can be removed only before generation or agent analysis"
        )

    artifact = (
        await session.execute(
            select(SspEvidenceArtifact)
            .where(
                SspEvidenceArtifact.workspace_id == workspace_id,
                SspEvidenceArtifact.evidence_artifact_id == evidence_artifact_id,
                SspEvidenceArtifact.removed_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise WorkspaceNotFoundError("active evidence artifact not found")

    def without_artifact(links: tuple[EvidenceLink, ...]) -> tuple[EvidenceLink, ...]:
        return tuple(
            link for link in links if link.artifact_id != evidence_artifact_id
        )

    retained_facts: list[FactContent] = []
    for fact in content.facts:
        links = without_artifact(fact.evidence)
        if not links and fact.evidence:
            continue
        retained_facts.append(fact.model_copy(update={"evidence": links}))
    updated = content.model_copy(
        update={
            "facts": tuple(retained_facts),
            "sections": tuple(
                item.model_copy(update={"evidence": without_artifact(item.evidence)})
                for item in content.sections
            ),
            "controls": tuple(
                item.model_copy(update={"evidence": without_artifact(item.evidence)})
                for item in content.controls
            ),
        }
    )
    await save_revision(
        session,
        workspace_id=workspace_id,
        content=updated,
        created_by=actor_id,
        now=now,
        expected_revision_id=expected_revision_id,
    )
    artifact.removed_at = now
    artifact.removed_by = actor_id
    await session.flush()
    await append_audit_event(
        session,
        hmac_key=audit_hmac_key,
        actor_type="user",
        actor_id=actor_id,
        action="ssp_evidence_removed",
        object_type="ssp_workspace",
        object_id=str(workspace_id),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "evidence_artifact_id": str(evidence_artifact_id),
            "sha256": artifact.sha256,
        },
        occurred_at=now,
    )
    return artifact
