"""Transactional persistence operations for immutable SSP workspace revisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.ssp_workspace.contracts import (
    ProfileState,
    RevisionContent,
    RevisionState,
    WorkspaceState,
    revision_content_sha256,
)


class WorkspacePersistenceError(Exception):
    """Base class for explicit SSP workspace persistence failures."""


class WorkspaceNotFoundError(WorkspacePersistenceError):
    error_code = "resource_not_found"


class ProfileVersionNotFoundError(WorkspacePersistenceError):
    error_code = "resource_not_found"


class IllegalWorkspaceStateError(WorkspacePersistenceError):
    error_code = "illegal_state_transition"


class StaleWorkspaceRevisionError(WorkspacePersistenceError):
    error_code = "revision_stale"


class RevisionIntegrityError(WorkspacePersistenceError):
    error_code = "revision_integrity_failed"


class EvidenceReferenceError(WorkspacePersistenceError):
    error_code = "evidence_reference_invalid"


async def create_workspace(
    session: AsyncSession,
    *,
    system_id: uuid.UUID,
    profile_version_id: uuid.UUID,
    created_by: str,
    now: datetime,
) -> Any:
    """Create a working workspace pinned to one immutable profile version."""

    from ato_service.db.models import SspProfileVersion, SspWorkspace, System

    actor = created_by.strip()
    if not actor:
        raise ValueError("created_by cannot be empty")
    system = (
        await session.execute(
            select(System).where(System.system_id == system_id).with_for_update()
        )
    ).scalar_one_or_none()
    if system is None:
        raise WorkspaceNotFoundError("system not found")
    profile = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_version_id == profile_version_id
            )
        )
    ).scalar_one_or_none()
    if profile is None or profile.status == ProfileState.ARCHIVED.value:
        raise ProfileVersionNotFoundError("available profile version not found")
    workspace = SspWorkspace(
        workspace_id=uuid.uuid4(),
        system_id=system_id,
        profile_version_id=profile_version_id,
        current_revision_id=None,
        status=WorkspaceState.WORKING.value,
        created_by=actor,
        created_at=now,
        archived_at=None,
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def archive_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    now: datetime,
) -> Any:
    """Apply the only legal workspace transition: working to archived."""

    workspace = await _load_workspace_for_update(session, workspace_id=workspace_id)
    if workspace.status != WorkspaceState.WORKING.value:
        raise IllegalWorkspaceStateError("workspace is not working")
    workspace.status = WorkspaceState.ARCHIVED.value
    workspace.archived_at = now
    await session.flush()
    return workspace


async def save_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    content: RevisionContent,
    created_by: str,
    now: datetime,
    expected_revision_id: uuid.UUID | None,
) -> Any:
    """Save a new immutable revision after an exact optimistic-lock check."""

    from ato_service.db.models import SspWorkspaceRevision

    actor = created_by.strip()
    if not actor:
        raise ValueError("created_by cannot be empty")
    content_hash = revision_content_sha256(content)
    document = content.model_dump(mode="json")
    workspace = await _load_workspace_for_update(session, workspace_id=workspace_id)
    if workspace.status != WorkspaceState.WORKING.value:
        raise IllegalWorkspaceStateError("archived workspaces cannot be edited")
    if workspace.current_revision_id != expected_revision_id:
        raise StaleWorkspaceRevisionError("workspace revision changed")

    current = None
    if workspace.current_revision_id is not None:
        current = (
            await session.execute(
                select(SspWorkspaceRevision)
                .where(
                    SspWorkspaceRevision.revision_id
                    == workspace.current_revision_id,
                    SspWorkspaceRevision.workspace_id == workspace_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if current is None:
            raise RevisionIntegrityError("current revision does not belong to workspace")
        if current.status == RevisionState.SUPERSEDED.value:
            raise RevisionIntegrityError("current revision is already superseded")

    await _validate_evidence_references(
        session,
        workspace_id=workspace_id,
        content=content,
    )
    max_version = (
        await session.execute(
            select(func.max(SspWorkspaceRevision.version)).where(
                SspWorkspaceRevision.workspace_id == workspace_id
            )
        )
    ).scalar_one()
    revision = SspWorkspaceRevision(
        revision_id=uuid.uuid4(),
        workspace_id=workspace_id,
        parent_revision_id=workspace.current_revision_id,
        version=(max_version or 0) + 1,
        status=RevisionState.WORKING.value,
        content_sha256=content_hash,
        content=document,
        created_by=actor,
        created_at=now,
    )
    if current is not None and current.status == RevisionState.WORKING.value:
        current.status = RevisionState.SUPERSEDED.value
    session.add(revision)
    await session.flush()
    await _materialize_revision(session, revision=revision, content=content)
    workspace.current_revision_id = revision.revision_id
    await session.flush()
    return revision


async def approve_current_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    revision_id: uuid.UUID,
    approved_by: str,
    now: datetime,
) -> Any:
    """Approve the exact current revision and bind approval to its verified hash."""

    from ato_service.db.models import SspApprovalSnapshot, SspWorkspaceRevision

    actor = approved_by.strip()
    if not actor:
        raise ValueError("approved_by cannot be empty")
    workspace = await _load_workspace_for_update(session, workspace_id=workspace_id)
    if workspace.status != WorkspaceState.WORKING.value:
        raise IllegalWorkspaceStateError("archived workspaces cannot be approved")
    if workspace.current_revision_id != revision_id:
        raise StaleWorkspaceRevisionError("only the current revision can be approved")
    revision = (
        await session.execute(
            select(SspWorkspaceRevision)
            .where(
                SspWorkspaceRevision.revision_id == revision_id,
                SspWorkspaceRevision.workspace_id == workspace_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revision is None:
        raise WorkspaceNotFoundError("revision not found")
    if revision.status != RevisionState.WORKING.value:
        raise IllegalWorkspaceStateError("revision is not working")
    validated_content = RevisionContent.model_validate(revision.content)
    actual_hash = revision_content_sha256(validated_content)
    if actual_hash != revision.content_sha256:
        raise RevisionIntegrityError("revision content hash does not match")
    approval = SspApprovalSnapshot(
        approval_snapshot_id=uuid.uuid4(),
        workspace_id=workspace_id,
        revision_id=revision_id,
        revision_sha256=actual_hash,
        approved_by=actor,
        approved_at=now,
    )
    revision.status = RevisionState.APPROVED.value
    session.add(approval)
    await session.flush()
    return approval


async def _load_workspace_for_update(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> Any:
    from ato_service.db.models import SspWorkspace

    workspace = (
        await session.execute(
            select(SspWorkspace)
            .where(SspWorkspace.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError("workspace not found")
    return workspace


def _content_evidence_ids(content: RevisionContent) -> set[uuid.UUID]:
    return {
        link.artifact_id
        for item in (*content.facts, *content.sections, *content.controls)
        for link in item.evidence
    }


async def _validate_evidence_references(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    content: RevisionContent,
) -> None:
    from ato_service.db.models import SspEvidenceArtifact

    expected = _content_evidence_ids(content)
    if not expected:
        return
    result = await session.execute(
        select(SspEvidenceArtifact.evidence_artifact_id).where(
            SspEvidenceArtifact.workspace_id == workspace_id,
            SspEvidenceArtifact.removed_at.is_(None),
            SspEvidenceArtifact.evidence_artifact_id.in_(expected),
        )
    )
    actual = set(result.scalars().all())
    if actual != expected:
        raise EvidenceReferenceError("revision contains foreign or missing evidence")


async def _materialize_revision(
    session: AsyncSession,
    *,
    revision: Any,
    content: RevisionContent,
) -> None:
    from ato_service.db.models import (
        SspControlStatement,
        SspQuestion,
        SspSection,
        SspSystemFact,
    )

    evidence_links: list[Any] = []
    for fact in content.facts:
        session.add(
            SspSystemFact(
                fact_id=uuid.uuid4(),
                revision_id=revision.revision_id,
                fact_key=fact.key,
                value=fact.value,
                provenance=fact.provenance.value,
                status=fact.state.value,
            )
        )
        evidence_links.extend(
            _link_rows(
                revision_id=revision.revision_id,
                target_type="fact",
                target_key=fact.key,
                links=fact.evidence,
            )
        )
    for section in content.sections:
        session.add(
            SspSection(
                section_id=uuid.uuid4(),
                revision_id=revision.revision_id,
                section_key=section.key,
                title=section.title,
                content=section.content,
                status=section.state.value,
            )
        )
        evidence_links.extend(
            _link_rows(
                revision_id=revision.revision_id,
                target_type="ssp_section",
                target_key=section.key,
                links=section.evidence,
            )
        )
    for control in content.controls:
        session.add(
            SspControlStatement(
                control_statement_id=uuid.uuid4(),
                revision_id=revision.revision_id,
                control_id=control.control_id,
                title=control.title,
                implementation_status=control.implementation_status,
                implementation_statement=control.implementation_statement,
                responsibility=control.responsibility,
                status=control.state.value,
                unresolved_reason=control.unresolved_reason,
            )
        )
        evidence_links.extend(
            _link_rows(
                revision_id=revision.revision_id,
                target_type="control",
                target_key=control.control_id,
                links=control.evidence,
            )
        )
    for question in content.questions:
        session.add(
            SspQuestion(
                question_record_id=uuid.uuid4(),
                question_id=question.question_id,
                revision_id=revision.revision_id,
                question=question.question,
                target_type=question.target_type,
                target_key=question.target_key,
                owner_type=question.owner_type,
                status=question.state.value,
                answer=question.answer,
            )
        )
    session.add_all(evidence_links)
    await session.flush()


def _link_rows(
    *,
    revision_id: uuid.UUID,
    target_type: str,
    target_key: str,
    links: tuple[Any, ...],
) -> list[Any]:
    from ato_service.db.models import SspEvidenceLink

    return [
        SspEvidenceLink(
            evidence_link_id=uuid.uuid4(),
            revision_id=revision_id,
            evidence_artifact_id=link.artifact_id,
            target_type=target_type,
            target_key=target_key,
            locator=link.locator,
        )
        for link in links
    ]
