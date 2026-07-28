"""Application service for the internal SSP drafting workspace."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceState,
    FactContent,
    ProfileRequirement,
    Provenance,
    QuestionState,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.editing import (
    answer_question,
    apply_agent_patch,
    edit_control,
    edit_section,
    merge_generation,
)
from ato_service.ssp_workspace.export import (
    build_workspace_docx_export,
    build_workspace_json_export,
)
from ato_service.ssp_workspace.generation import (
    ContextualEditRequest,
    ControlState as GenerationControlState,
    EvidenceFact,
    InitialGenerationRequest,
    ModelCallable,
    OpenQuestionState,
    SspSectionState,
    generate_contextual_patch,
    generate_initial_ssp,
)
from ato_service.ssp_workspace.generation_contracts import (
    GeneratedQuestion,
    PatchResult,
    TargetedPatch,
)
from ato_service.ssp_workspace.metrics import (
    EvidenceMetricRecord,
    calculate_workspace_metrics,
    requirement_is_satisfied,
)
from ato_service.ssp_workspace.persistence import (
    StaleWorkspaceRevisionError,
    approve_current_revision,
    create_workspace,
    save_revision,
)
from ato_service.ssp_workspace.profiles import resolve_stored_profile
from ato_service.ssp_workspace.profile_bundles import diff_profiles


class AgentPatchNotFoundError(ValueError):
    error_code = "resource_not_found"


class AgentPatchStateError(ValueError):
    error_code = "illegal_state_transition"


class ApprovalNotFoundError(ValueError):
    error_code = "approval_not_found"


class WorkspaceNotReviewableError(ValueError):
    error_code = "workspace_not_reviewable"


async def create_initialized_workspace(
    session: AsyncSession,
    *,
    system_id: uuid.UUID,
    profile_version_id: uuid.UUID,
    impact_level: str,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    """Create a workspace and its first empty, profile-pinned revision."""

    from ato_service.db.models import SspProfileVersion, System

    profile_row = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_version_id == profile_version_id
            )
        )
    ).scalar_one_or_none()
    if profile_row is None:
        from ato_service.ssp_workspace.persistence import ProfileVersionNotFoundError

        raise ProfileVersionNotFoundError("profile version not found")
    if profile_row.status != "active":
        from ato_service.ssp_workspace.persistence import ProfileVersionNotFoundError

        raise ProfileVersionNotFoundError("profile version is not active")
    profile = resolve_stored_profile(profile_row, impact_level)
    system = (
        await session.execute(select(System).where(System.system_id == system_id))
    ).scalar_one()
    workspace = await create_workspace(
        session,
        system_id=system_id,
        profile_version_id=profile_version_id,
        created_by=actor_id,
        now=now,
    )
    content = RevisionContent(
        facts=(
            FactContent(
                key="profile_id",
                value=profile.profile_id,
                provenance=Provenance.ISSO_ENTERED,
            ),
            FactContent(
                key="profile_version",
                value=profile.profile_version,
                provenance=Provenance.ISSO_ENTERED,
            ),
            FactContent(
                key="system.name",
                value=system.display_name,
                provenance=Provenance.ISSO_ENTERED,
            ),
            FactContent(
                key="system.impact_level",
                value=impact_level,
                provenance=Provenance.ISSO_ENTERED,
            ),
            *(
                (
                    FactContent(
                        key="system.identifier",
                        value=system.external_system_id,
                        provenance=Provenance.ISSO_ENTERED,
                    ),
                )
                if system.external_system_id
                else ()
            ),
        ),
        sections=tuple(
            SectionContent(
                key=item.item_id,
                title=item.title,
                content="",
                state=SectionState.EMPTY,
            )
            for item in profile.ssp_required_items
        ),
        controls=tuple(
            ControlContent(
                control_id=control.control_id,
                title=control.title,
                implementation_status="unknown",
                responsibility="unknown",
                state=ControlState.EMPTY,
            )
            for control in profile.controls
        ),
    )
    revision = await save_revision(
        session,
        workspace_id=workspace.workspace_id,
        content=content,
        created_by=actor_id,
        now=now,
        expected_revision_id=None,
    )
    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action="ssp_workspace_created",
        object_type="ssp_workspace",
        object_id=str(workspace.workspace_id),
        metadata={
            "profile_version_id": str(profile_version_id),
            "revision_id": str(revision.revision_id),
        },
        now=now,
    )
    return workspace


async def list_workspace_rows(session: AsyncSession) -> list[Any]:
    from ato_service.db.models import SspWorkspace

    result = await session.execute(
        select(SspWorkspace).order_by(SspWorkspace.created_at.desc())
    )
    return list(result.scalars())


async def load_workspace_envelope(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Return one explicit UI contract computed only from persisted records."""

    from ato_service.db.models import (
        SspApprovalSnapshot,
        SspAgentPatch,
        SspEvidenceArtifact,
        SspProfileVersion,
        SspWorkspace,
        SspWorkspaceRevision,
        System,
    )

    row = (
        await session.execute(
            select(SspWorkspace, System, SspProfileVersion)
            .join(System, System.system_id == SspWorkspace.system_id)
            .join(
                SspProfileVersion,
                SspProfileVersion.profile_version_id
                == SspWorkspace.profile_version_id,
            )
            .where(SspWorkspace.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        from ato_service.ssp_workspace.persistence import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("workspace not found")
    workspace, system, profile_row = row
    revision = None
    content = RevisionContent()
    if workspace.current_revision_id is not None:
        revision = (
            await session.execute(
                select(SspWorkspaceRevision).where(
                    SspWorkspaceRevision.revision_id
                    == workspace.current_revision_id
                )
            )
        ).scalar_one()
        content = RevisionContent.model_validate(revision.content)
    evidence = list(
        (
            await session.execute(
                select(SspEvidenceArtifact)
                .where(SspEvidenceArtifact.workspace_id == workspace_id)
                .order_by(SspEvidenceArtifact.uploaded_at.desc())
            )
        ).scalars()
    )
    approvals = list(
        (
            await session.execute(
                select(SspApprovalSnapshot)
                .where(SspApprovalSnapshot.workspace_id == workspace_id)
                .order_by(SspApprovalSnapshot.approved_at.desc())
            )
        ).scalars()
    )
    patches = list(
        (
            await session.execute(
                select(SspAgentPatch)
                .where(SspAgentPatch.workspace_id == workspace_id)
                .order_by(SspAgentPatch.created_at.desc())
            )
        ).scalars()
    )
    requirements = _metric_requirements(profile_row)
    effective_facts = _effective_metric_facts(content, requirements)
    fact_by_key = {item.key: item for item in effective_facts}
    satisfied_requirement_ids = [
        item.key
        for item in requirements
        if requirement_is_satisfied(item, fact_by_key.get(item.key))
    ]
    evidence_link_count = sum(
        len(item.evidence)
        for item in (*content.facts, *content.sections, *content.controls)
    )
    metrics = calculate_workspace_metrics(
        evidence=(
            EvidenceMetricRecord(
                state=EvidenceState(item.status),
                media_type=item.media_type,
            )
            for item in evidence
        ),
        facts=effective_facts,
        requirements=requirements,
        controls=content.controls,
        questions=content.questions,
        evidence_link_count=evidence_link_count,
    )
    metric_document = asdict(metrics)
    impact_level = _impact_level(content)
    return {
        "workspace_id": str(workspace.workspace_id),
        "system_id": str(workspace.system_id),
        "status": workspace.status,
        "created_by": workspace.created_by,
        "created_at": workspace.created_at.isoformat(),
        "system": {
            "display_name": system.display_name,
            "external_system_id": system.external_system_id,
            "owner_group": system.owner_group,
        },
        "profile": {
            "profile_version_id": str(profile_row.profile_version_id),
            "profile_id": profile_row.profile_key,
            "version": profile_row.version,
            "status": profile_row.status,
            "impact_level": impact_level,
        },
        "current_revision": (
            {
                "revision_id": str(revision.revision_id),
                "version": revision.version,
                "status": revision.status,
                "content_sha256": revision.content_sha256,
                "created_by": revision.created_by,
                "created_at": revision.created_at.isoformat(),
                "content": content.model_dump(mode="json"),
            }
            if revision is not None
            else None
        ),
        "evidence": [_evidence_document(item) for item in evidence],
        "approvals": [
            {
                "approval_snapshot_id": str(item.approval_snapshot_id),
                "revision_id": str(item.revision_id),
                "revision_sha256": item.revision_sha256,
                "approved_by": item.approved_by,
                "approved_at": item.approved_at.isoformat(),
            }
            for item in approvals
        ],
        "agent_patches": [
            {
                "patch_id": str(item.patch_id),
                "base_revision_id": str(item.base_revision_id),
                "applied_revision_id": (
                    str(item.applied_revision_id)
                    if item.applied_revision_id
                    else None
                ),
                "summary": item.summary,
                "status": item.status,
                "operations": item.operations,
                "created_at": item.created_at.isoformat(),
                "resolved_by": item.resolved_by,
                "resolved_at": (
                    item.resolved_at.isoformat() if item.resolved_at else None
                ),
            }
            for item in patches
        ],
        "requirements": [
            item.model_dump(mode="json") for item in requirements
        ],
        "satisfied_requirement_ids": satisfied_requirement_ids,
        "metrics": metric_document,
    }


async def save_section_edit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    section_key: str,
    content: str,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    revision = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    updated = edit_section(
        RevisionContent.model_validate(revision.content),
        section_key=section_key,
        text=content,
    )
    return await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_section_edited",
        metadata={"section_key": section_key},
    )


async def save_control_edit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    control_id: str,
    implementation_statement: str,
    implementation_status: str | None,
    responsibility: str | None,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    revision = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    updated = edit_control(
        RevisionContent.model_validate(revision.content),
        control_id=control_id,
        implementation_statement=implementation_statement,
        implementation_status=implementation_status,
        responsibility=responsibility,
    )
    return await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_control_edited",
        metadata={"control_id": control_id},
    )


async def save_question_answer(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    question_id: uuid.UUID,
    answer: str,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    revision = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    updated = answer_question(
        RevisionContent.model_validate(revision.content),
        question_id=question_id,
        answer=answer,
    )
    return await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_question_answered",
        metadata={"question_id": str(question_id)},
    )


async def generate_workspace_draft(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    model: ModelCallable,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    workspace, revision, system, profile = await _generation_context(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
    )
    content = RevisionContent.model_validate(revision.content)
    execution = await generate_initial_ssp(
        InitialGenerationRequest(
            system_name=system.display_name,
            profile=profile,
            source_ids=tuple(
                sorted(
                    {
                        str(link.artifact_id)
                        for fact in content.facts
                        for link in fact.evidence
                    }
                )
            ),
            facts=_generation_facts(content),
        ),
        model,
    )
    updated = merge_generation(content, execution.value)
    return await _save_edited_revision(
        session,
        workspace_id=workspace.workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_draft_generated",
        metadata={"model_attempts": execution.attempts},
    )


async def propose_agent_patch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    instruction: str,
    model: ModelCallable,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    from ato_service.db.models import SspAgentPatch

    workspace, revision, system, profile = await _generation_context(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
    )
    content = RevisionContent.model_validate(revision.content)
    execution = await generate_contextual_patch(
        ContextualEditRequest(
            system_name=system.display_name,
            profile=profile,
            source_ids=tuple(
                sorted(
                    {
                        str(link.artifact_id)
                        for fact in content.facts
                        for link in fact.evidence
                    }
                )
            ),
            facts=_generation_facts(content),
            sections=tuple(
                SspSectionState(
                    section_id=item.key,
                    revision=revision.version,
                    content=item.content,
                )
                for item in content.sections
            ),
            controls=tuple(
                GenerationControlState(
                    control_id=item.control_id,
                    revision=revision.version,
                    implementation_status=item.implementation_status or "unknown",
                    responsibility=item.responsibility or "unknown",
                    implementation_statement=item.implementation_statement,
                )
                for item in content.controls
            ),
            open_questions=tuple(
                OpenQuestionState(
                    question_id=str(item.question_id),
                    target_type=item.target_type,  # type: ignore[arg-type]
                    target_id=item.target_key,
                    question=(
                        f"{item.question}\nProvided answer: {item.answer}"
                        if item.answer
                        else item.question
                    ),
                )
                for item in content.questions
                if item.state is not QuestionState.DISMISSED
                and item.target_type != "fact"
            ),
            instruction=instruction,
        ),
        model,
    )
    row = SspAgentPatch(
        patch_id=uuid.uuid4(),
        workspace_id=workspace.workspace_id,
        base_revision_id=revision.revision_id,
        applied_revision_id=None,
        operations=[_serialize_patch_result(execution.value)],
        summary=execution.value.change_summary,
        status="proposed",
        created_at=now,
        resolved_by=None,
        resolved_at=None,
    )
    session.add(row)
    await session.flush()
    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action="ssp_agent_patch_proposed",
        object_type="ssp_workspace",
        object_id=str(workspace_id),
        metadata={"patch_id": str(row.patch_id)},
        now=now,
    )
    return row


async def apply_proposed_patch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    patch_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    patch = await _load_patch_for_update(
        session, workspace_id=workspace_id, patch_id=patch_id
    )
    if patch.status != "proposed":
        raise AgentPatchStateError("agent patch is not proposed")
    if patch.base_revision_id != expected_revision_id:
        patch.status = "stale"
        patch.resolved_by = actor_id
        patch.resolved_at = now
        await session.flush()
        raise StaleWorkspaceRevisionError("agent patch base revision is stale")
    revision = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    result = _deserialize_patch_result(patch.operations)
    updated = apply_agent_patch(
        RevisionContent.model_validate(revision.content),
        result,
        current_revision=revision.version,
    )
    saved = await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_agent_patch_applied",
        metadata={"patch_id": str(patch_id)},
    )
    patch.status = "applied"
    patch.applied_revision_id = saved.revision_id
    patch.resolved_by = actor_id
    patch.resolved_at = now
    await session.flush()
    return patch


async def reject_proposed_patch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    patch_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    patch = await _load_patch_for_update(
        session, workspace_id=workspace_id, patch_id=patch_id
    )
    if patch.status != "proposed":
        raise AgentPatchStateError("agent patch is not proposed")
    patch.status = "rejected"
    patch.resolved_by = actor_id
    patch.resolved_at = now
    await session.flush()
    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action="ssp_agent_patch_rejected",
        object_type="ssp_workspace",
        object_id=str(workspace_id),
        metadata={"patch_id": str(patch_id)},
        now=now,
    )
    return patch


async def approve_workspace_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    revision_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    from ato_service.db.models import SspEvidenceArtifact, SspWorkspaceRevision

    revision = (
        await session.execute(
            select(SspWorkspaceRevision).where(
                SspWorkspaceRevision.workspace_id == workspace_id,
                SspWorkspaceRevision.revision_id == revision_id,
            )
        )
    ).scalar_one_or_none()
    if revision is None:
        raise WorkspaceNotReviewableError("current revision is unavailable")
    content = RevisionContent.model_validate(revision.content)
    nonterminal_evidence = (
        await session.execute(
            select(SspEvidenceArtifact.evidence_artifact_id).where(
                SspEvidenceArtifact.workspace_id == workspace_id,
                SspEvidenceArtifact.status.in_(("uploaded", "processing")),
            )
        )
    ).first()
    open_targets = {
        (item.target_type, item.target_key)
        for item in content.questions
        if item.state is QuestionState.OPEN
    }
    envelope = await load_workspace_envelope(session, workspace_id=workspace_id)
    satisfied = set(envelope["satisfied_requirement_ids"])
    sections_resolved = all(
        item.key in satisfied or ("ssp_section", item.key) in open_targets
        for item in content.sections
    )
    controls_resolved = all(
        item.implementation_statement.strip()
        or bool(item.unresolved_reason and item.unresolved_reason.strip())
        or ("control", item.control_id) in open_targets
        for item in content.controls
    )
    if nonterminal_evidence is not None or not sections_resolved or not controls_resolved:
        raise WorkspaceNotReviewableError(
            "workspace has untracked required information gaps"
        )
    approval = await approve_current_revision(
        session,
        workspace_id=workspace_id,
        revision_id=revision_id,
        approved_by=actor_id,
        now=now,
    )
    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action="ssp_revision_approved",
        object_type="ssp_workspace",
        object_id=str(workspace_id),
        metadata={"revision_id": str(revision_id)},
        now=now,
    )
    return approval


async def render_approved_export(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    revision_id: uuid.UUID,
    export_format: str,
    include_open_questions: bool,
) -> bytes:
    snapshot = await _approved_export_snapshot(
        session, workspace_id=workspace_id, revision_id=revision_id
    )
    if export_format == "json":
        return build_workspace_json_export(
            snapshot, include_open_questions=include_open_questions
        )
    if export_format == "docx":
        return build_workspace_docx_export(
            snapshot, include_open_questions=include_open_questions
        )
    raise ValueError("export_format must be json or docx")


async def restore_workspace_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    """Restore prior exact content by creating a new working revision."""

    from ato_service.db.models import (
        SspProfileVersion,
        SspWorkspace,
        SspWorkspaceRevision,
    )

    await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    target = (
        await session.execute(
            select(SspWorkspaceRevision).where(
                SspWorkspaceRevision.workspace_id == workspace_id,
                SspWorkspaceRevision.revision_id == revision_id,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise StaleWorkspaceRevisionError("restore revision was not found")
    content = RevisionContent.model_validate(target.content)
    fact_values = {item.key: item.value for item in content.facts}
    profile_row = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_key == fact_values.get("profile_id"),
                SspProfileVersion.version == fact_values.get("profile_version"),
            )
        )
    ).scalar_one_or_none()
    if profile_row is None:
        raise ValueError("restored revision profile is unavailable")
    workspace = (
        await session.execute(
            select(SspWorkspace)
            .where(SspWorkspace.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalar_one()
    workspace.profile_version_id = profile_row.profile_version_id
    return await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=content,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_revision_restored",
        metadata={"restored_revision_id": str(revision_id)},
    )


async def migrate_workspace_profile(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    profile_version_id: uuid.UUID,
    impact_level: str,
    expected_revision_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> tuple[Any, Any]:
    """Create a new working revision reconciled to an active profile version."""

    from ato_service.db.models import SspProfileVersion, SspWorkspace

    current = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    workspace = (
        await session.execute(
            select(SspWorkspace)
            .where(SspWorkspace.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalar_one()
    old_profile_row = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_version_id
                == workspace.profile_version_id
            )
        )
    ).scalar_one()
    new_profile_row = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_version_id == profile_version_id,
                SspProfileVersion.status == "active",
            )
        )
    ).scalar_one_or_none()
    if new_profile_row is None:
        raise ValueError("target profile version is not active")
    old_profile = resolve_stored_profile(old_profile_row, _impact_level(
        RevisionContent.model_validate(current.content)
    ))
    new_profile = resolve_stored_profile(new_profile_row, impact_level)
    if old_profile.profile_id != new_profile.profile_id:
        raise ValueError("profile migration requires the same profile_id")
    profile_diff = diff_profiles(old_profile, new_profile)
    content = RevisionContent.model_validate(current.content)
    old_sections = {item.key: item for item in content.sections}
    old_controls = {item.control_id: item for item in content.controls}
    facts = {item.key: item for item in content.facts}
    for key, value in (
        ("profile_id", new_profile.profile_id),
        ("profile_version", new_profile.profile_version),
        ("system.impact_level", impact_level),
    ):
        facts[key] = FactContent(
            key=key,
            value=value,
            provenance=Provenance.ISSO_ENTERED,
        )
    migrated = replace(
        content,
        facts=tuple(facts[key] for key in sorted(facts)),
        sections=tuple(
            old_sections.get(item.item_id)
            or SectionContent(
                key=item.item_id,
                title=item.title,
                content="",
                state=SectionState.EMPTY,
            )
            for item in new_profile.ssp_required_items
        ),
        controls=tuple(
            old_controls.get(item.control_id)
            or ControlContent(
                control_id=item.control_id,
                title=item.title,
                implementation_status="unknown",
                responsibility="unknown",
                state=ControlState.EMPTY,
            )
            for item in new_profile.controls
        ),
    )
    workspace.profile_version_id = profile_version_id
    saved = await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=migrated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_profile_migrated",
        metadata={
            "old_profile_version": old_profile.profile_version,
            "new_profile_version": new_profile.profile_version,
        },
    )
    return saved, profile_diff


async def _save_edited_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    content: RevisionContent,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
    action: str,
    metadata: dict[str, Any],
) -> Any:
    saved = await save_revision(
        session,
        workspace_id=workspace_id,
        content=content,
        created_by=actor_id,
        now=now,
        expected_revision_id=expected_revision_id,
    )
    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action=action,
        object_type="ssp_workspace",
        object_id=str(workspace_id),
        metadata={**metadata, "revision_id": str(saved.revision_id)},
        now=now,
    )
    return saved


async def _load_exact_current_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    revision_id: uuid.UUID,
) -> Any:
    from ato_service.db.models import SspWorkspace, SspWorkspaceRevision

    workspace = (
        await session.execute(
            select(SspWorkspace).where(SspWorkspace.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if workspace is None or workspace.current_revision_id != revision_id:
        raise StaleWorkspaceRevisionError("workspace revision changed")
    revision = (
        await session.execute(
            select(SspWorkspaceRevision).where(
                SspWorkspaceRevision.revision_id == revision_id,
                SspWorkspaceRevision.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if revision is None:
        raise StaleWorkspaceRevisionError("workspace revision changed")
    return revision


async def _generation_context(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
) -> tuple[Any, Any, Any, Any]:
    from ato_service.db.models import SspProfileVersion, SspWorkspace, System

    revision = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    workspace, system, profile_row = (
        await session.execute(
            select(SspWorkspace, System, SspProfileVersion)
            .join(System, System.system_id == SspWorkspace.system_id)
            .join(
                SspProfileVersion,
                SspProfileVersion.profile_version_id
                == SspWorkspace.profile_version_id,
            )
            .where(SspWorkspace.workspace_id == workspace_id)
        )
    ).one()
    content = RevisionContent.model_validate(revision.content)
    return (
        workspace,
        revision,
        system,
        resolve_stored_profile(profile_row, _impact_level(content)),
    )


def _generation_facts(content: RevisionContent) -> tuple[EvidenceFact, ...]:
    facts: list[EvidenceFact] = []
    for fact in content.facts:
        if not fact.evidence:
            continue
        source_id = str(fact.evidence[0].artifact_id)
        text = fact.value if isinstance(fact.value, str) else str(fact.value)
        facts.append(EvidenceFact(fact_id=fact.key, source_id=source_id, text=text))
    return tuple(facts)


def _impact_level(content: RevisionContent) -> str:
    for fact in content.facts:
        if fact.key in {"system.impact_level", "impact_level"} and fact.value in {
            "low",
            "moderate",
            "high",
        }:
            return str(fact.value)
    raise ValueError("workspace is missing a valid impact_level fact")


def _metric_requirements(profile_row: Any) -> tuple[ProfileRequirement, ...]:
    bundle = profile_row.bundle
    raw_items = bundle.get("ssp_required_items", []) if isinstance(bundle, dict) else []
    return tuple(
        ProfileRequirement(
            key=item["item_id"],
            value_type="array" if item["value_type"] == "string_list" else "string",
            required=True,
            enum_values=tuple(item.get("allowed_values", ())),
            min_length=item.get("min_length") or 1,
            evidence_required_for_agent_value=item.get(
                "evidence_required_for_agent", True
            ),
        )
        for item in raw_items
    )


def _effective_metric_facts(
    content: RevisionContent,
    requirements: tuple[ProfileRequirement, ...],
) -> tuple[FactContent, ...]:
    """Project edited/generated SSP fields into typed requirement facts."""

    facts = {item.key: item for item in content.facts}
    requirements_by_key = {item.key: item for item in requirements}
    for section in content.sections:
        requirement = requirements_by_key.get(section.key)
        if requirement is None or not section.content.strip():
            continue
        if requirement.value_type == "array":
            value: Any = [
                line.strip().lstrip("-*•").strip()
                for line in section.content.splitlines()
                if line.strip().lstrip("-*•").strip()
            ]
        else:
            value = section.content.strip()
        provenance = (
            Provenance.ISSO_ENTERED
            if section.state in {SectionState.EDITED, SectionState.REVIEWED}
            else Provenance.AGENT_GENERATED
        )
        if provenance is Provenance.AGENT_GENERATED and not section.evidence:
            continue
        facts[section.key] = FactContent(
            key=section.key,
            value=value,
            provenance=provenance,
            evidence=section.evidence,
        )
    return tuple(facts[key] for key in sorted(facts))


def _evidence_document(item: Any) -> dict[str, Any]:
    document = {
        "evidence_artifact_id": str(item.evidence_artifact_id),
        "display_filename": item.display_filename,
        "media_type": item.media_type,
        "sha256": item.sha256,
        "status": item.status,
        "uploaded_by": item.uploaded_by,
        "uploaded_at": item.uploaded_at.isoformat(),
        "processed_at": item.processed_at.isoformat() if item.processed_at else None,
        "failure_code": item.failure_code,
    }
    for name in ("size_bytes", "detected_format", "extracted_segments"):
        if hasattr(item, name):
            document[name] = getattr(item, name)
    return document


def _serialize_patch_result(result: PatchResult) -> dict[str, Any]:
    return {
        "patches": [asdict(item) for item in result.patches],
        "questions_to_add": [asdict(item) for item in result.questions_to_add],
        "question_ids_to_resolve": list(result.question_ids_to_resolve),
        "change_summary": result.change_summary,
    }


def _deserialize_patch_result(operations: Any) -> PatchResult:
    if (
        not isinstance(operations, list)
        or len(operations) != 1
        or not isinstance(operations[0], dict)
    ):
        raise AgentPatchStateError("stored agent patch is malformed")
    document = operations[0]
    try:
        return PatchResult(
            patches=tuple(
                TargetedPatch(
                    target_type=item["target_type"],
                    target_id=item["target_id"],
                    expected_revision=item["expected_revision"],
                    changes=dict(item["changes"]),
                    supporting_fact_ids=tuple(item["supporting_fact_ids"]),
                )
                for item in document["patches"]
            ),
            questions_to_add=tuple(
                GeneratedQuestion(
                    question_key=item["question_key"],
                    target_type=item["target_type"],
                    target_id=item["target_id"],
                    question=item["question"],
                    owner_type=item["owner_type"],
                )
                for item in document["questions_to_add"]
            ),
            question_ids_to_resolve=tuple(document["question_ids_to_resolve"]),
            change_summary=document["change_summary"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentPatchStateError("stored agent patch is malformed") from exc


async def _load_patch_for_update(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    patch_id: uuid.UUID,
) -> Any:
    from ato_service.db.models import SspAgentPatch

    patch = (
        await session.execute(
            select(SspAgentPatch)
            .where(
                SspAgentPatch.workspace_id == workspace_id,
                SspAgentPatch.patch_id == patch_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if patch is None:
        raise AgentPatchNotFoundError("agent patch not found")
    return patch


async def _approved_export_snapshot(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    revision_id: uuid.UUID,
) -> dict[str, Any]:
    from ato_service.db.models import (
        SspApprovalSnapshot,
        SspProfileVersion,
        SspWorkspace,
        SspWorkspaceRevision,
        System,
    )

    row = (
        await session.execute(
            select(
                SspWorkspace,
                SspWorkspaceRevision,
                SspApprovalSnapshot,
                System,
                SspProfileVersion,
            )
            .join(
                SspWorkspaceRevision,
                SspWorkspaceRevision.workspace_id == SspWorkspace.workspace_id,
            )
            .join(
                SspApprovalSnapshot,
                SspApprovalSnapshot.revision_id
                == SspWorkspaceRevision.revision_id,
            )
            .join(System, System.system_id == SspWorkspace.system_id)
            .join(
                SspProfileVersion,
                SspProfileVersion.profile_version_id
                == SspWorkspace.profile_version_id,
            )
            .where(
                SspWorkspace.workspace_id == workspace_id,
                SspWorkspaceRevision.revision_id == revision_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise ApprovalNotFoundError("approved revision not found")
    workspace, revision, approval, system, profile = row
    content = RevisionContent.model_validate(revision.content)
    revision_facts = {item.key: item.value for item in content.facts}
    revision_profile_id = revision_facts.get("profile_id")
    revision_profile_version = revision_facts.get("profile_version")
    if (
        revision_profile_id
        and revision_profile_version
        and (
            profile.profile_key != revision_profile_id
            or profile.version != revision_profile_version
        )
    ):
        historical_profile = (
            await session.execute(
                select(SspProfileVersion).where(
                    SspProfileVersion.profile_key == revision_profile_id,
                    SspProfileVersion.version == revision_profile_version,
                )
            )
        ).scalar_one_or_none()
        if historical_profile is None:
            raise ApprovalNotFoundError("approved revision profile is unavailable")
        profile = historical_profile
    return {
        "workspace_id": str(workspace.workspace_id),
        "revision_id": str(revision.revision_id),
        "content_sha256": revision.content_sha256,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at.isoformat(),
        "system": {
            "display_name": system.display_name,
            "external_system_id": system.external_system_id,
        },
        "profile": {
            "profile_id": profile.profile_key,
            "version": profile.version,
            "impact_level": _impact_level(content),
        },
        "sections": [
            {
                "section_id": item.key,
                "title": item.title,
                "order": index,
                "state": item.state.value,
                "content": item.content,
            }
            for index, item in enumerate(content.sections)
        ],
        "controls": [
            {
                "control_id": item.control_id,
                "title": item.title,
                "state": item.state.value,
                "implementation_status": item.implementation_status or "unknown",
                "responsibility": item.responsibility or "unknown",
                "implementation_statement": item.implementation_statement,
                "evidence_links": [
                    f"{link.artifact_id}:{link.locator}" for link in item.evidence
                ],
            }
            for item in content.controls
        ],
        "questions": [
            {
                "question_id": str(item.question_id),
                "target": f"{item.target_type}:{item.target_key}",
                "question": item.question,
                "owner_type": item.owner_type,
                "status": item.state.value,
            }
            for item in content.questions
        ],
    }


async def _audit(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    metadata: dict[str, Any],
    now: datetime,
) -> None:
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        outcome="succeeded",
        reason_code=None,
        metadata=metadata,
        occurred_at=now,
    )
