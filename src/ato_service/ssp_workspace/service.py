"""Application service for the internal SSP drafting workspace."""

from __future__ import annotations

import hashlib
import asyncio
from contextlib import asynccontextmanager
import inspect
import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.ssp_workspace.categorization import (
    CategorizationEvidenceInput,
    active_categorization_status,
    build_categorization_export_block,
    build_confirmed_categorization_facts,
    high_water_mark_impact,
    resolve_workspace_evidence_links,
    validate_categorization_evidence,
    validate_categorization_impacts,
    validate_categorization_rationales,
)
from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceLink,
    EvidenceState,
    FactContent,
    FactState,
    ProfileRequirement,
    Provenance,
    QuestionState,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.editing import (
    WorkspaceEditError,
    answer_question,
    apply_agent_patch,
    edit_control,
    edit_section,
    merge_categorization_proposal,
    merge_generation,
)
from ato_service.ssp_workspace.export import (
    build_workspace_docx_export,
    build_workspace_json_export,
)
from ato_service.ssp_workspace.generation import (
    CategorizationProposalRequest,
    ContextualEditRequest,
    EvidenceFact,
    InitialGenerationRequest,
    ModelCallable,
    OpenQuestionState,
    SspConfirmationContext,
    SspSectionState,
    generate_categorization_proposal,
    generate_contextual_patch,
    generate_initial_ssp,
    SspGenerationError,
)
from ato_service.ssp_workspace.generation import (
    ControlState as GenerationControlState,
)
from ato_service.ssp_workspace.generation_contracts import (
    GeneratedQuestion,
    PatchResult,
    ProfileValidationError,
    SelectedProfilePolicy,
    TargetedPatch,
    validate_applied_patch_result,
    validate_workspace_control_fields,
    validate_workspace_implementation_statement,
    validate_workspace_section_content,
)
from ato_service.ssp_workspace.metrics import (
    EvidenceMetricRecord,
    agent_control_blocks_approval,
    calculate_workspace_metrics,
    controls_have_tracked_responses,
    requirement_is_satisfied,
)
from ato_service.ssp_workspace.model_policy import require_ssp_model_allowed
from ato_service.ssp_workspace.oscal_export import build_draft_oscal_ssp_json_export
from ato_service.ssp_workspace.persistence import (
    StaleWorkspaceRevisionError,
    approve_current_revision,
    create_workspace,
    save_revision,
)
from ato_service.ssp_workspace.profile_bundles import (
    ControlResponsePolicy,
    ProfileDiff,
    diff_profiles,
)
from ato_service.ssp_workspace.profiles import (
    deserialize_profile_bundle,
    resolve_stored_profile,
)


class AgentPatchNotFoundError(ValueError):
    error_code = "resource_not_found"


class AgentPatchStateError(ValueError):
    error_code = "illegal_state_transition"


class ApprovalNotFoundError(ValueError):
    error_code = "approval_not_found"


class WorkspaceNotReviewableError(ValueError):
    error_code = "workspace_not_reviewable"


class WorkspaceProfileValidationError(ValueError):
    error_code = "profile_validation_failed"


class AgencyDocxRenderNotFoundError(ValueError):
    error_code = "resource_not_found"


class AgencyDocxRenderStateError(ValueError):
    error_code = "illegal_state_transition"


class AgencyDocxUploadError(ValueError):
    error_code = "agency_docx_upload_failed"

    def __init__(
        self,
        detail: str,
        *,
        failure_kind: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if failure_kind is not None:
            self.failure_kind = failure_kind


_SAFE_CONTEXT_BUDGET_DETAIL = (
    "SSP request exceeds the configured aggregate context budget; reduce the "
    "selected evidence or use an approved larger-context profile"
)


def _wrap_agency_docx_error(exc: Exception) -> AgencyDocxUploadError:
    """Preserve the bounded failure class without exposing provider details."""
    failure_kind = getattr(exc, "failure_kind", None)
    detail = (
        _SAFE_CONTEXT_BUDGET_DETAIL
        if failure_kind == "context_budget"
        else str(exc)
    )
    return AgencyDocxUploadError(detail, failure_kind=failure_kind)


@dataclass(frozen=True, slots=True)
class _GenerationSnapshot:
    workspace_id: uuid.UUID
    revision_id: uuid.UUID
    revision_version: int
    content: RevisionContent
    system_name: str
    profile: Any


@dataclass(frozen=True, slots=True)
class AgencyDocxRenderReference:
    """Detached render identity returned after a cache or write decision."""

    render_id: uuid.UUID
    status: str
    output_storage_key: str
    output_sha256: str


def _agency_docx_render_reference(render: Any) -> AgencyDocxRenderReference:
    return AgencyDocxRenderReference(
        render_id=render.render_id,
        status=render.status,
        output_storage_key=render.output_storage_key,
        output_sha256=render.output_sha256,
    )


async def create_initialized_workspace(
    session: AsyncSession,
    *,
    system_id: uuid.UUID,
    profile_version_id: uuid.UUID,
    impact_level: str | None,
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
    working_impact_level = impact_level or "moderate"
    profile = resolve_stored_profile(profile_row, working_impact_level)
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
                key=(
                    "system.impact_level"
                    if impact_level is not None
                    else "system.provisional_impact_level"
                ),
                value=working_impact_level,
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
        SspAgencyDocxRender,
        SspAgentPatch,
        SspApprovalSnapshot,
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
                SspProfileVersion.profile_version_id == SspWorkspace.profile_version_id,
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
                    SspWorkspaceRevision.revision_id == workspace.current_revision_id
                )
            )
        ).scalar_one()
        content = RevisionContent.model_validate(revision.content)
    evidence = list(
        (
            await session.execute(
                select(SspEvidenceArtifact)
                .where(
                    SspEvidenceArtifact.workspace_id == workspace_id,
                    SspEvidenceArtifact.removed_at.is_(None),
                )
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
    agency_docx_renders = list(
        (
            await session.execute(
                select(SspAgencyDocxRender)
                .where(SspAgencyDocxRender.workspace_id == workspace_id)
                .order_by(SspAgencyDocxRender.created_at.desc())
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
    impact_level = _confirmed_impact_level(content)
    resolved_profile = resolve_stored_profile(profile_row, _impact_level(content))
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
            "provisional_impact_level": _impact_level(content),
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
                    str(item.applied_revision_id) if item.applied_revision_id else None
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
        "requirements": [item.model_dump(mode="json") for item in requirements],
        "satisfied_requirement_ids": satisfied_requirement_ids,
        "metrics": metric_document,
        "control_response": _control_response_envelope(
            resolved_profile.control_response
        ),
        "agency_docx_renders": [
            _agency_docx_render_metadata(item) for item in agency_docx_renders
        ],
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
    profile = await _resolved_profile_for_revision(session, revision)
    profile_policy = SelectedProfilePolicy.from_resolved(profile)
    try:
        validate_workspace_section_content(section_key, content, profile_policy)
    except ProfileValidationError as exc:
        raise WorkspaceProfileValidationError(str(exc)) from exc
    requirement = profile_policy.sections.get(section_key)
    if requirement is not None and requirement.structured_kind:
        raise WorkspaceProfileValidationError(
            "structured SSP sections must be saved via dedicated confirm endpoints"
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
    profile = await _resolved_profile_for_revision(session, revision)
    profile_policy = SelectedProfilePolicy.from_resolved(profile)
    try:
        validate_workspace_control_fields(
            implementation_status=implementation_status,
            responsibility=responsibility,
            profile_policy=profile_policy,
        )
    except ProfileValidationError as exc:
        raise WorkspaceProfileValidationError(str(exc)) from exc
    try:
        validate_workspace_implementation_statement(
            implementation_statement,
            profile_policy=profile_policy,
        )
    except ProfileValidationError as exc:
        raise WorkspaceProfileValidationError(str(exc)) from exc
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
    config: Any,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    require_ssp_model_allowed(config)
    snapshot = await _generation_context(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
    )
    content = snapshot.content
    execution = await generate_initial_ssp(
        InitialGenerationRequest(
            system_name=snapshot.system_name,
            profile=snapshot.profile,
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
            categorization_confirmed=_confirmed_impact_level(content) is not None,
            confirmation_context=_workspace_confirmation_context(content),
        ),
        model,
    )
    updated = merge_generation(content, execution.value)
    await _require_current_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=snapshot.revision_id,
    )
    return await _save_edited_revision(
        session,
        workspace_id=snapshot.workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_draft_generated",
        metadata={"model_attempts": execution.attempts},
    )


async def analyze_workspace_categorization(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    model: ModelCallable,
    config: Any,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    """Propose FIPS 199 categorization from evidence without full SSP generation."""

    from ato_service.ssp_workspace.categorization import active_categorization_status
    from ato_service.ssp_workspace.contracts import FactState

    require_ssp_model_allowed(config)
    snapshot = await _generation_context(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
    )
    content = snapshot.content
    facts_by_key = {
        item.key: item for item in content.facts if item.state is FactState.ACTIVE
    }
    if active_categorization_status(facts_by_key) == "confirmed":
        raise WorkspaceEditError("categorization is already confirmed")
    execution = await generate_categorization_proposal(
        CategorizationProposalRequest(
            system_name=snapshot.system_name,
            profile=snapshot.profile,
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
    if execution.value is None:
        raise SspGenerationError(
            "insufficient evidence to propose FIPS 199 categorization"
        )
    updated = merge_categorization_proposal(content, execution.value)
    await _require_current_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=snapshot.revision_id,
    )
    return await _save_edited_revision(
        session,
        workspace_id=snapshot.workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_categorization_proposed",
        metadata={"model_attempts": execution.attempts},
    )


async def propose_agent_patch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    instruction: str,
    model: ModelCallable,
    config: Any,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    from ato_service.db.models import SspAgentPatch

    require_ssp_model_allowed(config)
    snapshot = await _generation_context(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
    )
    content = snapshot.content
    execution = await generate_contextual_patch(
        ContextualEditRequest(
            system_name=snapshot.system_name,
            profile=snapshot.profile,
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
                    revision=snapshot.revision_version,
                    content=item.content,
                )
                for item in content.sections
            ),
            controls=tuple(
                GenerationControlState(
                    control_id=item.control_id,
                    revision=snapshot.revision_version,
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
            categorization_confirmed=_confirmed_impact_level(content) is not None,
        ),
        model,
    )
    await _require_current_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=snapshot.revision_id,
    )
    row = SspAgentPatch(
        patch_id=uuid.uuid4(),
        workspace_id=snapshot.workspace_id,
        base_revision_id=snapshot.revision_id,
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
    content = RevisionContent.model_validate(revision.content)
    profile = await _resolved_profile_for_revision(session, revision)
    profile_policy = SelectedProfilePolicy.from_resolved(profile)
    allowed_fact_ids = frozenset(fact.key for fact in content.facts if fact.evidence)
    try:
        validate_applied_patch_result(
            result,
            profile_policy,
            allowed_fact_ids=allowed_fact_ids,
        )
    except ProfileValidationError as exc:
        raise WorkspaceProfileValidationError(str(exc)) from exc
    updated = apply_agent_patch(
        content,
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
    facts_by_key = {
        item.key: item for item in content.facts if item.state is FactState.ACTIVE
    }
    categorization_status = active_categorization_status(facts_by_key)
    if categorization_status == "stale":
        raise WorkspaceNotReviewableError(
            "system categorization must be re-confirmed before approval"
        )
    if categorization_status != "confirmed":
        raise WorkspaceNotReviewableError(
            "system categorization must be explicitly confirmed before approval"
        )
    from ato_service.ssp_workspace.system_definition import (
        active_system_definition_status,
    )

    system_definition_status = active_system_definition_status(facts_by_key)
    if system_definition_status == "stale":
        raise WorkspaceNotReviewableError(
            "system definition must be re-confirmed before approval"
        )
    if system_definition_status != "confirmed":
        raise WorkspaceNotReviewableError(
            "system definition must be explicitly confirmed before approval"
        )
    from ato_service.ssp_workspace.information_types import (
        active_information_types_status,
    )

    information_types_status = active_information_types_status(facts_by_key)
    if information_types_status == "stale":
        raise WorkspaceNotReviewableError(
            "information type mapping must be re-confirmed before approval"
        )
    if information_types_status != "confirmed":
        raise WorkspaceNotReviewableError(
            "information type mapping must be explicitly confirmed before approval"
        )
    from ato_service.db.models import SspProfileVersion, SspWorkspace

    profile_row = (
        await session.execute(
            select(SspProfileVersion)
            .join(
                SspWorkspace,
                SspWorkspace.profile_version_id == SspProfileVersion.profile_version_id,
            )
            .where(SspWorkspace.workspace_id == workspace_id)
        )
    ).scalar_one()
    profile = resolve_stored_profile(profile_row, _impact_level(content))
    requirements = _metric_requirements(profile_row)
    required_section_keys = {item.key for item in requirements if item.required}
    nonterminal_evidence = (
        await session.execute(
            select(SspEvidenceArtifact.evidence_artifact_id).where(
                SspEvidenceArtifact.workspace_id == workspace_id,
                SspEvidenceArtifact.removed_at.is_(None),
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
        key in satisfied or ("ssp_section", key) in open_targets
        for key in required_section_keys
    )
    profile_policy = SelectedProfilePolicy.from_resolved(profile)
    statement_policy = profile_policy.implementation_statement_rules
    controls_resolved = controls_have_tracked_responses(
        content.controls,
        open_targets,
        required=statement_policy.require_statement_gap_or_question_before_approval,
    )
    if any(
        agent_control_blocks_approval(
            item,
            evidence_required=(
                profile_policy.control_response.evidence_required_for_agent_statement
            ),
        )
        for item in content.controls
    ):
        raise WorkspaceNotReviewableError(
            "workspace has ungrounded agent control metadata"
        )
    if (
        nonterminal_evidence is not None
        or not sections_resolved
        or not controls_resolved
    ):
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
    if export_format == "oscal-json":
        return build_draft_oscal_ssp_json_export(
            snapshot,
            include_open_questions=include_open_questions,
        )
    raise ValueError("export_format must be json, docx, or oscal-json")


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
    categorization: dict[str, FactContent] | None = None,
    audit_action: str = "ssp_profile_migrated",
) -> tuple[Any, Any]:
    """Create a new working revision for an explicit profile migration.

    Existing content is carried forward only when its profile meaning is still
    compatible.  Changed requirements are made reviewable, while incompatible
    control enum values fail before the workspace or revision is mutated.
    """

    from ato_service.db.models import SspProfileVersion, SspWorkspace
    from ato_service.ssp_workspace.information_types import (
        INFORMATION_TYPES_STATUS_KEY,
        active_information_types_status,
    )
    from ato_service.ssp_workspace.system_definition import (
        SYSTEM_DEFINITION_STATUS_KEY,
        active_system_definition_status,
    )

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
    if workspace.current_revision_id != expected_revision_id:
        raise StaleWorkspaceRevisionError("workspace revision changed")
    old_profile_row = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_version_id == workspace.profile_version_id
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
    old_profile = resolve_stored_profile(
        old_profile_row, _impact_level(RevisionContent.model_validate(current.content))
    )
    new_profile = resolve_stored_profile(new_profile_row, impact_level)
    if old_profile.profile_id != new_profile.profile_id:
        raise ValueError("profile migration requires the same profile_id")

    content = RevisionContent.model_validate(current.content)
    current_impact_level = _impact_level(content)
    if (
        categorization is None
        and workspace.profile_version_id == profile_version_id
        and current_impact_level == impact_level
    ):
        return current, _impact_profile_diff(old_profile, new_profile)

    if old_profile.profile_version == new_profile.profile_version:
        profile_diff = (
            _impact_profile_diff(old_profile, new_profile)
            if old_profile.impact_level != new_profile.impact_level
            else diff_profiles(old_profile, new_profile)
        )
    else:
        # ``diff_profiles`` compares one impact level at a time.  Use it for
        # version-independent semantics at the source impact, then correct
        # the baseline sets and changed IDs to the actual source/target pair.
        target_at_source_impact = (
            new_profile
            if old_profile.impact_level == new_profile.impact_level
            else resolve_stored_profile(new_profile_row, old_profile.impact_level)
        )
        source_diff = diff_profiles(old_profile, target_at_source_impact)
        old_controls_by_id = {
            control.control_id: control for control in old_profile.controls
        }
        new_controls_by_id = {
            control.control_id: control for control in new_profile.controls
        }
        actual_common_control_ids = (
            old_controls_by_id.keys() & new_controls_by_id.keys()
        )
        profile_diff = replace(
            source_diff,
            impact_level=new_profile.impact_level,
            added_control_ids=tuple(
                sorted(new_controls_by_id.keys() - old_controls_by_id.keys())
            ),
            removed_control_ids=tuple(
                sorted(old_controls_by_id.keys() - new_controls_by_id.keys())
            ),
            changed_control_ids=tuple(
                sorted(
                    control_id
                    for control_id in actual_common_control_ids
                    if (
                        old_controls_by_id[control_id].title,
                        old_controls_by_id[control_id].requirement_text,
                    )
                    != (
                        new_controls_by_id[control_id].title,
                        new_controls_by_id[control_id].requirement_text,
                    )
                )
            ),
        )
    old_sections = {item.key: item for item in content.sections}
    old_controls = {item.control_id: item for item in content.controls}
    new_section_ids = {item.item_id for item in new_profile.ssp_required_items}
    new_control_ids = {item.control_id for item in new_profile.controls}

    incompatible_values: list[str] = []
    allowed_statuses = set(new_profile.control_response.implementation_statuses)
    allowed_responsibilities = set(new_profile.control_response.responsibilities)
    for control_id in sorted(old_controls.keys() & new_control_ids):
        control = old_controls[control_id]
        if (
            control.implementation_status is not None
            and control.implementation_status not in allowed_statuses
        ):
            incompatible_values.append(
                f"{control_id}.implementation_status={control.implementation_status!r}"
            )
        if (
            control.responsibility is not None
            and control.responsibility not in allowed_responsibilities
        ):
            incompatible_values.append(
                f"{control_id}.responsibility={control.responsibility!r}"
            )
    if incompatible_values:
        raise WorkspaceProfileValidationError(
            "profile migration cannot preserve incompatible control enum values: "
            + ", ".join(incompatible_values)
        )

    changed_section_ids = set(profile_diff.changed_ssp_item_ids)
    changed_control_ids = set(profile_diff.changed_control_ids)
    if profile_diff.implementation_statement_policy_changed:
        changed_control_ids.update(old_controls.keys() & new_control_ids)
    if old_profile.control_response != new_profile.control_response:
        changed_control_ids.update(old_controls.keys() & new_control_ids)

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
    if categorization is not None:
        facts.pop("system.provisional_impact_level", None)
        for key, fact in categorization.items():
            facts[key] = fact
    elif active_categorization_status(facts) == "confirmed":
        # A profile or baseline migration invalidates the prior confirmation;
        # keep the selected impact visible as provisional until re-confirmed.
        facts["system.categorization_status"] = FactContent(
            key="system.categorization_status",
            value="stale",
            provenance=Provenance.ISSO_ENTERED,
        )
        facts["system.provisional_impact_level"] = FactContent(
            key="system.provisional_impact_level",
            value=impact_level,
            provenance=Provenance.ISSO_ENTERED,
        )

    affected_foundational_items = (
        set(profile_diff.added_ssp_item_ids)
        | set(profile_diff.removed_ssp_item_ids)
        | set(profile_diff.changed_ssp_item_ids)
    )
    if (
        affected_foundational_items
        & {
            "system.authorization_boundary",
            "system.components",
            "system.interconnections",
        }
        and active_system_definition_status(facts) == "confirmed"
    ):
        facts[SYSTEM_DEFINITION_STATUS_KEY] = FactContent(
            key=SYSTEM_DEFINITION_STATUS_KEY,
            value="stale",
            provenance=Provenance.ISSO_ENTERED,
        )
    if (
        "system.data_types" in affected_foundational_items
        and active_information_types_status(facts) == "confirmed"
    ):
        facts[INFORMATION_TYPES_STATUS_KEY] = FactContent(
            key=INFORMATION_TYPES_STATUS_KEY,
            value="stale",
            provenance=Provenance.ISSO_ENTERED,
        )

    def migrated_section(item: Any) -> SectionContent:
        existing = old_sections.get(item.item_id)
        if existing is None:
            return SectionContent(
                key=item.item_id,
                title=item.title,
                content="",
                state=SectionState.EMPTY,
            )
        updates: dict[str, Any] = {"title": item.title}
        if item.item_id in changed_section_ids and existing.state is SectionState.REVIEWED:
            updates["state"] = SectionState.EDITED
        return existing.model_copy(update=updates)

    def migrated_control(item: Any) -> ControlContent:
        existing = old_controls.get(item.control_id)
        if existing is None:
            return ControlContent(
                control_id=item.control_id,
                title=item.title,
                implementation_status=(
                    "unknown"
                    if "unknown" in allowed_statuses
                    else None
                ),
                responsibility=(
                    "unknown"
                    if "unknown" in allowed_responsibilities
                    else None
                ),
                state=ControlState.EMPTY,
            )
        updates: dict[str, Any] = {"title": item.title}
        if item.control_id in changed_control_ids:
            updates["state"] = ControlState.PARTIAL
            marker = (
                "Profile migration changed this control requirement; review it "
                f"against profile {new_profile.profile_version} before approval."
            )
            updates["unresolved_reason"] = (
                f"{existing.unresolved_reason.strip()}\n{marker}"
                if existing.unresolved_reason
                and existing.unresolved_reason.strip()
                else marker
            )
        return existing.model_copy(update=updates)

    migrated = content.model_copy(
        update={
            "facts": tuple(facts[key] for key in sorted(facts)),
            "sections": tuple(migrated_section(item) for item in new_profile.ssp_required_items),
            "controls": tuple(migrated_control(item) for item in new_profile.controls),
            "questions": tuple(
                question.model_copy(update={"state": QuestionState.DISMISSED})
                if question.state is QuestionState.OPEN
                and (
                    (
                        question.target_type == "control"
                        and question.target_key not in new_control_ids
                    )
                    or (
                        question.target_type == "ssp_section"
                        and question.target_key not in new_section_ids
                    )
                )
                else question
                for question in content.questions
            ),
        }
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
        action=audit_action,
        metadata={
            "old_profile_version": old_profile.profile_version,
            "new_profile_version": new_profile.profile_version,
            "old_impact_level": old_profile.impact_level,
            "new_impact_level": new_profile.impact_level,
        },
    )
    return saved, profile_diff


async def save_system_categorization(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    confidentiality: str,
    integrity: str,
    availability: str,
    confidentiality_rationale: str,
    integrity_rationale: str,
    availability_rationale: str,
    confidentiality_evidence: tuple[EvidenceLink, ...],
    integrity_evidence: tuple[EvidenceLink, ...],
    availability_evidence: tuple[EvidenceLink, ...],
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> tuple[Any, Any]:
    """Confirm FIPS 199 impacts and reconcile the selected control baseline."""

    from ato_service.db.models import SspWorkspace

    validate_categorization_impacts(confidentiality, integrity, availability)
    rationales = validate_categorization_rationales(
        confidentiality_rationale,
        integrity_rationale,
        availability_rationale,
    )
    evidence = CategorizationEvidenceInput(
        confidentiality=await resolve_workspace_evidence_links(
            session,
            workspace_id=workspace_id,
            links=confidentiality_evidence,
        ),
        integrity=await resolve_workspace_evidence_links(
            session,
            workspace_id=workspace_id,
            links=integrity_evidence,
        ),
        availability=await resolve_workspace_evidence_links(
            session,
            workspace_id=workspace_id,
            links=availability_evidence,
        ),
    )
    validate_categorization_evidence(evidence)
    workspace = (
        await session.execute(
            select(SspWorkspace).where(SspWorkspace.workspace_id == workspace_id)
        )
    ).scalar_one()
    overall = high_water_mark_impact(confidentiality, integrity, availability)
    categorization = build_confirmed_categorization_facts(
        confidentiality=confidentiality,
        integrity=integrity,
        availability=availability,
        rationales=rationales,
        evidence=evidence,
    )
    return await migrate_workspace_profile(
        session,
        workspace_id=workspace_id,
        profile_version_id=workspace.profile_version_id,
        impact_level=overall,
        expected_revision_id=expected_revision_id,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        categorization=categorization,
        audit_action="ssp_categorization_confirmed",
    )


async def save_system_definition(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    boundary_narrative: str,
    diagram_links: tuple[dict[str, Any], ...],
    components: tuple[dict[str, Any], ...],
    interconnections: tuple[dict[str, Any], ...],
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    """Confirm structured authorization boundary, components, and interconnections."""

    from ato_service.ssp_workspace.contracts import EvidenceLink
    from ato_service.ssp_workspace.system_definition import (
        AuthorizationBoundary,
        DiagramLink,
        Interconnection,
        SystemComponent,
        apply_system_definition_sections,
        resolve_workspace_evidence_links,
        validate_authorization_boundary,
        validate_component_inventory,
        validate_interconnection_register,
    )

    revision = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    content = RevisionContent.model_validate(revision.content)

    links = tuple(
        DiagramLink(
            artifact_id=uuid.UUID(str(entry["artifact_id"])),
            locator=dict(entry["locator"]),
            label=str(entry.get("label") or ""),
        )
        for entry in diagram_links
    )
    boundary = AuthorizationBoundary(narrative=boundary_narrative, diagram_links=links)
    validate_authorization_boundary(boundary)

    parsed_components: list[SystemComponent] = []
    for raw in components:
        evidence = tuple(
            EvidenceLink(
                artifact_id=uuid.UUID(str(item["artifact_id"])),
                locator=dict(item["locator"]),
            )
            for item in raw.get("evidence") or ()
        )
        evidence = await resolve_workspace_evidence_links(
            session,
            workspace_id=workspace_id,
            links=evidence,
        )
        parsed_components.append(
            SystemComponent(
                component_id=str(raw.get("component_id") or uuid.uuid4()),
                name=str(raw.get("name") or ""),
                purpose=str(raw.get("purpose") or ""),
                placement=raw.get("placement"),  # type: ignore[arg-type]
                evidence=evidence,
            )
        )
    component_tuple = tuple(parsed_components)
    validate_component_inventory(component_tuple)

    parsed_interconnections: list[Interconnection] = []
    for raw in interconnections:
        evidence = tuple(
            EvidenceLink(
                artifact_id=uuid.UUID(str(item["artifact_id"])),
                locator=dict(item["locator"]),
            )
            for item in raw.get("evidence") or ()
        )
        evidence = await resolve_workspace_evidence_links(
            session,
            workspace_id=workspace_id,
            links=evidence,
        )
        parsed_interconnections.append(
            Interconnection(
                interconnection_id=str(raw.get("interconnection_id") or uuid.uuid4()),
                connected_organization=str(raw.get("connected_organization") or ""),
                connected_system=str(raw.get("connected_system") or ""),
                direction=raw.get("direction"),  # type: ignore[arg-type]
                data_types=tuple(str(item) for item in raw.get("data_types") or ()),
                interface_protocol=str(raw.get("interface_protocol") or ""),
                connection_owner=str(raw.get("connection_owner") or ""),
                agreement_type=str(raw.get("agreement_type") or ""),
                agreement_id=str(raw.get("agreement_id") or ""),
                agreement_status=str(raw.get("agreement_status") or ""),
                agreement_expiration=str(raw.get("agreement_expiration") or ""),
                boundary_protections=str(raw.get("boundary_protections") or ""),
                evidence=evidence,
            )
        )
    interconnection_tuple = tuple(parsed_interconnections)
    validate_interconnection_register(interconnection_tuple)

    for link in links:
        await resolve_workspace_evidence_links(
            session,
            workspace_id=workspace_id,
            links=(EvidenceLink(artifact_id=link.artifact_id, locator=link.locator),),
        )

    updated = apply_system_definition_sections(
        content,
        boundary=boundary,
        components=component_tuple,
        interconnections=interconnection_tuple,
    )
    return await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_system_definition_confirmed",
        metadata={
            "component_count": len(component_tuple),
            "interconnection_count": len(interconnection_tuple),
        },
    )


async def analyze_workspace_diagram(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    artifact_id: uuid.UUID,
    page_number: int,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
    blob_store: Any,
    config: Any,
    vision_client: Any | None = None,
) -> Any:
    """Analyze one architecture diagram artifact and propose system definition draft."""

    from ato_service.blobs import BlobStore
    from ato_service.db.models import SspEvidenceArtifact
    from ato_service.extraction.detect import media_type_for_format
    from ato_service.extraction.pdf import render_page_png
    from ato_service.ssp_workspace.diagram_analysis import (
        DiagramAnalysisError,
        analyze_architecture_diagram,
        apply_system_definition_proposal,
        build_system_definition_from_analysis,
        collect_text_evidence_context,
        proposal_metadata_from_analysis,
    )
    from ato_service.ssp_workspace.vision import (
        VisionConfigurationError,
        build_vision_model_client,
    )

    if not isinstance(blob_store, BlobStore):
        raise TypeError("blob_store must be a BlobStore")
    if page_number < 1:
        raise ValueError("page_number must be >= 1")

    require_ssp_model_allowed(config, vision=True)
    async with _read_snapshot_transaction(session):
        revision = await _load_exact_current_revision(
            session, workspace_id=workspace_id, revision_id=expected_revision_id
        )
        content = RevisionContent.model_validate(revision.content)
        artifact = (
            await session.execute(
                select(SspEvidenceArtifact).where(
                    SspEvidenceArtifact.workspace_id == workspace_id,
                    SspEvidenceArtifact.evidence_artifact_id == artifact_id,
                    SspEvidenceArtifact.removed_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if artifact is None:
            raise ValueError("diagram evidence artifact not found")
        if artifact.status != "processed":
            raise ValueError("diagram evidence must be processed before analysis")
        artifact_storage_key = artifact.storage_key
        artifact_sha256 = artifact.sha256
        artifact_detected_format = artifact.detected_format or ""
        artifact_display_filename = artifact.display_filename

    payload = await asyncio.to_thread(
        _read_blob_bytes, blob_store, artifact_storage_key, artifact_sha256
    )
    detected_format = artifact_detected_format
    locator: dict[str, Any] = {"page": page_number}
    if detected_format == "pdf":
        image_bytes = await asyncio.to_thread(
            render_page_png,
            payload,
            page_number=page_number,
            limits=config.extraction_limits,
        )
        media_type = media_type_for_format("png")
        locator = {"page": page_number, "kind": "rendered_page"}
    elif detected_format in {"png", "jpeg", "webp"}:
        image_bytes = payload
        media_type = media_type_for_format(detected_format)
        locator = {"kind": "image", "page": page_number}
    else:
        raise ValueError(
            "diagram analysis requires PNG, JPEG, WebP, or PDF evidence"
        )

    text_context = collect_text_evidence_context(content, artifact_id=artifact_id)
    resolved_vision_client = vision_client
    owns_vision_client = resolved_vision_client is None
    try:
        if owns_vision_client:
            try:
                resolved_vision_client = build_vision_model_client(config)
            except VisionConfigurationError as exc:
                raise ValueError(
                    "vision model is not configured for diagram analysis"
                ) from exc
        analysis_kwargs = {
            "artifact_id": artifact_id,
            "image_bytes": image_bytes,
            "media_type": media_type,
            "text_context": text_context,
            "model": resolved_vision_client,
        }
        model_call = getattr(resolved_vision_client, "__call__", resolved_vision_client)
        if inspect.iscoroutinefunction(model_call):
            analysis = await analyze_architecture_diagram(**analysis_kwargs)
        else:
            analysis = await asyncio.to_thread(
                lambda: asyncio.run(analyze_architecture_diagram(**analysis_kwargs))
            )
    except DiagramAnalysisError:
        raise
    finally:
        if owns_vision_client and resolved_vision_client is not None:
            await resolved_vision_client.aclose()

    await _require_current_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
    )
    current_artifact = (
        await session.execute(
            select(SspEvidenceArtifact).where(
                SspEvidenceArtifact.workspace_id == workspace_id,
                SspEvidenceArtifact.evidence_artifact_id == artifact_id,
                SspEvidenceArtifact.removed_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if (
        current_artifact is None
        or current_artifact.status != "processed"
        or current_artifact.storage_key != artifact_storage_key
        or current_artifact.sha256 != artifact_sha256
    ):
        raise StaleWorkspaceRevisionError("diagram evidence changed")

    boundary, components, interconnections = build_system_definition_from_analysis(
        analysis,
        artifact_id=artifact_id,
        locator=locator,
        diagram_label=artifact_display_filename,
    )
    metadata = proposal_metadata_from_analysis(
        analysis,
        artifact_id=artifact_id,
        locator=locator,
        display_filename=artifact_display_filename,
        artifact_sha256=artifact_sha256,
        source_revision_id=expected_revision_id,
    )
    updated = apply_system_definition_proposal(
        content,
        boundary=boundary,
        components=components,
        interconnections=interconnections,
        proposal_metadata=metadata,
    )
    return await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_diagram_analysis_applied",
        metadata={
            "artifact_id": str(artifact_id),
            "component_count": len(components),
            "interconnection_count": len(interconnections),
            "conflict_count": len(analysis.conflicts),
        },
    )


async def save_information_types(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    mappings: tuple[dict[str, Any], ...],
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    """Confirm SP 800-60 information type mappings."""

    from ato_service.ssp_workspace.contracts import EvidenceLink
    from ato_service.ssp_workspace.information_types import (
        InformationTypeMapping,
        apply_information_type_register,
        resolve_workspace_evidence_links,
    )

    revision = await _load_exact_current_revision(
        session, workspace_id=workspace_id, revision_id=expected_revision_id
    )
    content = RevisionContent.model_validate(revision.content)

    parsed: list[InformationTypeMapping] = []
    for raw in mappings:
        evidence = tuple(
            EvidenceLink(
                artifact_id=uuid.UUID(str(item["artifact_id"])),
                locator=dict(item["locator"]),
            )
            for item in raw.get("evidence") or ()
        )
        evidence = await resolve_workspace_evidence_links(
            session,
            workspace_id=workspace_id,
            links=evidence,
        )
        parsed.append(
            InformationTypeMapping(
                entry_id=str(raw.get("entry_id") or uuid.uuid4()),
                catalog_identifier=str(raw.get("catalog_identifier") or ""),
                description=str(raw.get("description") or ""),
                adjusted_confidentiality=raw.get("adjusted_confidentiality"),
                adjusted_integrity=raw.get("adjusted_integrity"),
                adjusted_availability=raw.get("adjusted_availability"),
                adjustment_rationale=str(raw.get("adjustment_rationale") or ""),
                evidence=evidence,
            )
        )
    updated = apply_information_type_register(content, mappings=tuple(parsed))
    return await _save_edited_revision(
        session,
        workspace_id=workspace_id,
        expected_revision_id=expected_revision_id,
        content=updated,
        actor_id=actor_id,
        now=now,
        audit_hmac_key=audit_hmac_key,
        action="ssp_information_types_confirmed",
        metadata={"mapping_count": len(parsed)},
    )


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


async def _resolved_profile_for_revision(
    session: AsyncSession,
    revision: Any,
) -> Any:
    from ato_service.db.models import SspProfileVersion, SspWorkspace

    content = RevisionContent.model_validate(revision.content)
    workspace = (
        await session.execute(
            select(SspWorkspace).where(
                SspWorkspace.workspace_id == revision.workspace_id
            )
        )
    ).scalar_one()
    profile_row = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_version_id == workspace.profile_version_id
            )
        )
    ).scalar_one()
    return resolve_stored_profile(profile_row, _impact_level(content))


@asynccontextmanager
async def _read_snapshot_transaction(session: AsyncSession):
    """Run only the read snapshot in a root transaction, then end it."""
    if not isinstance(session, AsyncSession):
        yield
        return

    if session.in_nested_transaction():
        raise ValueError("SSP workflow requires a root transaction boundary")
    if session.in_transaction():
        if session.new or session.dirty or session.deleted:
            raise ValueError(
                "SSP workflow cannot start with pending database mutations"
            )
        raise ValueError("SSP workflow requires a clean transaction boundary")
    try:
        yield
    except BaseException:
        await session.rollback()
        raise
    else:
        # A root rollback ends the PostgreSQL transaction, releasing its
        # snapshot and every lock before storage, parsing, or model work.
        await session.rollback()


def _require_clean_root_transaction(session: AsyncSession) -> None:
    """Reject caller work before a service-owned optimistic write phase."""
    if not isinstance(session, AsyncSession):
        return
    if session.in_nested_transaction():
        raise ValueError("SSP workflow requires a root transaction boundary")
    if session.in_transaction():
        if session.new or session.dirty or session.deleted:
            raise ValueError(
                "SSP workflow cannot start with pending database mutations"
            )
        raise ValueError("SSP workflow requires a clean transaction boundary")


async def _require_current_revision(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
) -> Any:
    """Revalidate the optimistic input immediately before a model-derived write."""
    return await _load_exact_current_revision(
        session,
        workspace_id=workspace_id,
        revision_id=expected_revision_id,
    )


async def _generation_context(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
) -> _GenerationSnapshot:
    from ato_service.db.models import SspProfileVersion, SspWorkspace, System

    async with _read_snapshot_transaction(session):
        revision = await _load_exact_current_revision(
            session, workspace_id=workspace_id, revision_id=expected_revision_id
        )
        workspace, system, profile_row = (
            await session.execute(
                select(SspWorkspace, System, SspProfileVersion)
                .join(System, System.system_id == SspWorkspace.system_id)
                .join(
                    SspProfileVersion,
                    SspProfileVersion.profile_version_id == SspWorkspace.profile_version_id,
                )
                .where(SspWorkspace.workspace_id == workspace_id)
            )
        ).one()
        content = RevisionContent.model_validate(revision.content)
        return _GenerationSnapshot(
            workspace_id=workspace.workspace_id,
            revision_id=revision.revision_id,
            revision_version=revision.version,
            content=content,
            system_name=system.display_name,
            profile=resolve_stored_profile(profile_row, _impact_level(content)),
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


def _workspace_confirmation_context(
    content: RevisionContent,
) -> SspConfirmationContext:
    """Project persisted confirmation flags, evidence, and canonical sections."""

    from ato_service.ssp_workspace.categorization import (
        CATEGORIZATION_STATUS_KEY,
        IMPACT_FACT_KEYS,
        RATIONALE_FACT_KEYS,
    )
    from ato_service.ssp_workspace.information_types import (
        INFORMATION_TYPES_SECTION_KEY,
        INFORMATION_TYPES_STATUS_KEY,
        active_information_types_status,
    )
    from ato_service.ssp_workspace.system_definition import (
        STRUCTURED_SECTION_KEYS,
        SYSTEM_DEFINITION_STATUS_KEY,
        active_system_definition_status,
    )

    facts_by_key = {
        item.key: item for item in content.facts if item.state is FactState.ACTIVE
    }
    categorization_status = active_categorization_status(facts_by_key) or "unconfirmed"
    system_definition_status = (
        active_system_definition_status(facts_by_key) or "unconfirmed"
    )
    information_types_status = (
        active_information_types_status(facts_by_key) or "unconfirmed"
    )

    def bound_fact_ids(canonical_keys: set[str] | frozenset[str]) -> tuple[str, ...]:
        """Return only active, evidence-linked facts owned by one dimension."""

        return tuple(
            sorted(
                fact.key
                for fact in content.facts
                if fact.state is FactState.ACTIVE
                and fact.key in canonical_keys
                and fact.evidence
            )
        )

    categorization_fact_ids = bound_fact_ids(
        frozenset(
            (
                CATEGORIZATION_STATUS_KEY,
                *IMPACT_FACT_KEYS,
                *RATIONALE_FACT_KEYS,
            )
        )
    )
    system_definition_fact_ids = bound_fact_ids(
        frozenset(STRUCTURED_SECTION_KEYS | {SYSTEM_DEFINITION_STATUS_KEY})
    )
    information_types_fact_ids = bound_fact_ids(
        frozenset({INFORMATION_TYPES_SECTION_KEY, INFORMATION_TYPES_STATUS_KEY})
    )

    def confirmed_sections(
        canonical_keys: set[str] | frozenset[str],
        status: str,
    ) -> tuple[tuple[str, str], ...]:
        """Return non-empty canonical section values only after confirmation."""

        if status != "confirmed":
            return ()
        return tuple(
            (section.key, section.content)
            for section in sorted(content.sections, key=lambda item: item.key)
            if section.key in canonical_keys and section.content.strip()
        )

    system_definition_sections = confirmed_sections(
        STRUCTURED_SECTION_KEYS,
        system_definition_status,
    )
    information_types_sections = confirmed_sections(
        frozenset({INFORMATION_TYPES_SECTION_KEY}),
        information_types_status,
    )
    return SspConfirmationContext(
        categorization_status=categorization_status,
        system_definition_status=system_definition_status,
        information_types_status=information_types_status,
        categorization_fact_ids=(
            categorization_fact_ids if categorization_status == "confirmed" else ()
        ),
        system_definition_fact_ids=(
            system_definition_fact_ids
            if system_definition_status == "confirmed"
            else ()
        ),
        information_types_fact_ids=(
            information_types_fact_ids
            if information_types_status == "confirmed"
            else ()
        ),
        confirmed_system_definition_sections=system_definition_sections,
        confirmed_information_types_sections=information_types_sections,
    )


def _control_response_envelope(
    control_response: ControlResponsePolicy,
) -> dict[str, Any]:
    return {
        "implementation_statuses": sorted(control_response.implementation_statuses),
        "responsibilities": sorted(control_response.responsibilities),
        "question_owner_types": sorted(control_response.question_owner_types),
        "evidence_required_for_agent_statement": (
            control_response.evidence_required_for_agent_statement
        ),
    }


def _impact_level(content: RevisionContent) -> str:
    confirmed = _confirmed_impact_level(content)
    if confirmed is not None:
        return confirmed
    for fact in content.facts:
        if fact.state is not FactState.ACTIVE:
            continue
        if fact.key == "system.provisional_impact_level" and fact.value in {
            "low",
            "moderate",
            "high",
        }:
            return str(fact.value)
        if fact.key in {"system.impact_level", "impact_level"} and fact.value in {
            "low",
            "moderate",
            "high",
        }:
            return str(fact.value)
    return "moderate"


def _confirmed_impact_level(content: RevisionContent) -> str | None:
    facts = {
        item.key: item
        for item in content.facts
        if item.state is FactState.ACTIVE
    }
    if active_categorization_status(facts) != "confirmed":
        return None
    for fact in content.facts:
        if fact.key in {"system.impact_level", "impact_level"} and fact.value in {
            "low",
            "moderate",
            "high",
        }:
            return str(fact.value)
    return None


def _impact_profile_diff(old: Any, new: Any) -> ProfileDiff:
    old_controls = {control.control_id for control in old.controls}
    new_controls = {control.control_id for control in new.controls}
    return ProfileDiff(
        old_profile_version=old.profile_version,
        new_profile_version=new.profile_version,
        impact_level=new.impact_level,
        added_control_ids=tuple(sorted(new_controls - old_controls)),
        removed_control_ids=tuple(sorted(old_controls - new_controls)),
        changed_control_ids=(),
        added_ssp_item_ids=(),
        removed_ssp_item_ids=(),
        changed_ssp_item_ids=(),
        source_version_changes=(),
    )


def _metric_requirements(profile_row: Any) -> tuple[ProfileRequirement, ...]:
    bundle = profile_row.bundle
    raw_items = bundle.get("ssp_required_items", []) if isinstance(bundle, dict) else []
    requirements: list[ProfileRequirement] = []
    for item in raw_items:
        structured_kind = item.get("structured_kind")
        if structured_kind == "authorization_boundary":
            value_type = "object"
        elif structured_kind in {
            "component_inventory",
            "interconnection_register",
            "information_type_register",
        } or item["value_type"] == "string_list":
            value_type = "array"
        else:
            value_type = "string"
        requirements.append(
            ProfileRequirement(
                key=item["item_id"],
                value_type=value_type,
                required=item.get("required", True),
                enum_values=tuple(item.get("allowed_values", ())),
                min_length=item.get("min_length") or 1,
                evidence_required_for_agent_value=item.get(
                    "evidence_required_for_agent", True
                ),
                structured_kind=structured_kind,
            )
        )
    return tuple(requirements)


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
        if requirement.structured_kind:
            if requirement.structured_kind == "information_type_register":
                from ato_service.ssp_workspace.information_types import (
                    structured_section_metric_value as information_types_metric_value,
                )

                value = information_types_metric_value(section.content)
            else:
                from ato_service.ssp_workspace.system_definition import (
                    structured_section_metric_value,
                )

                value = structured_section_metric_value(
                    section.key,
                    section.content,
                    structured_kind=requirement.structured_kind,
                )
            if value is None:
                continue
        elif requirement.value_type == "array":
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
                SspApprovalSnapshot.revision_id == SspWorkspaceRevision.revision_id,
            )
            .join(System, System.system_id == SspWorkspace.system_id)
            .join(
                SspProfileVersion,
                SspProfileVersion.profile_version_id == SspWorkspace.profile_version_id,
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

    impact_level = _impact_level(content)
    resolved_profile = resolve_stored_profile(profile, impact_level)
    stored_bundle = deserialize_profile_bundle(profile.bundle)
    active_fact_objects = {
        item.key: item
        for item in content.facts
        if item.state is FactState.ACTIVE
    }
    active_facts = {
        key: item.value for key, item in active_fact_objects.items()
    }
    fact_evidence = {
        key: item.evidence
        for key, item in active_fact_objects.items()
        if item.evidence
    }
    document_title = active_facts.get("system.name")
    if not isinstance(document_title, str) or not document_title.strip():
        document_title = system.display_name
    else:
        document_title = document_title.strip()

    from ato_service.db.models import SspEvidenceArtifact

    evidence_rows = (
        await session.execute(
            select(SspEvidenceArtifact.evidence_artifact_id, SspEvidenceArtifact.display_filename).where(
                SspEvidenceArtifact.workspace_id == workspace_id,
                SspEvidenceArtifact.removed_at.is_(None),
            )
        )
    ).all()
    evidence_catalog = {
        str(row.evidence_artifact_id): row.display_filename for row in evidence_rows
    }
    section_content = {
        item.key: item.content for item in content.sections if item.content.strip()
    }
    from ato_service.ssp_workspace.information_types import (
        build_information_types_export_block,
    )
    from ato_service.ssp_workspace.system_definition import (
        build_system_definition_export_block,
    )

    return {
        "workspace_id": str(workspace.workspace_id),
        "revision_id": str(revision.revision_id),
        "content_sha256": revision.content_sha256,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at.isoformat(),
        "document_title": document_title,
        "system": {
            "display_name": system.display_name,
            "external_system_id": system.external_system_id,
        },
        "profile": {
            "profile_id": profile.profile_key,
            "version": profile.version,
            "impact_level": impact_level,
        },
        "facts": active_facts,
        "categorization": build_categorization_export_block(
            facts=active_facts,
            fact_evidence=fact_evidence,
            evidence_catalog=evidence_catalog,
            overall_impact=impact_level,
        ),
        "standard_coverage": [
            {
                "source_id": entry.source_id,
                "requirement_id": entry.requirement_id,
                "title": entry.title,
                "coverage_kind": entry.coverage_kind,
                "item_ids": list(entry.item_ids),
                "required": entry.required,
            }
            for entry in stored_bundle.standard_coverage
        ],
        "ssp_items": [
            {
                "item_id": item.item_id,
                "title": item.title,
                "value_type": item.value_type,
                "standard_refs": list(item.standard_refs),
            }
            for item in stored_bundle.ssp_required_items
        ],
        "control_order": [control.control_id for control in resolved_profile.controls],
        "evidence_catalog": evidence_catalog,
        "system_definition": build_system_definition_export_block(
            sections=section_content,
            evidence_catalog=evidence_catalog,
        ),
        "information_types": build_information_types_export_block(
            sections=section_content,
            evidence_catalog=evidence_catalog,
        ),
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


async def list_agency_docx_renders(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> list[Any]:
    from ato_service.db.models import SspAgencyDocxRender

    result = await session.execute(
        select(SspAgencyDocxRender)
        .where(SspAgencyDocxRender.workspace_id == workspace_id)
        .order_by(SspAgencyDocxRender.created_at.desc())
    )
    return list(result.scalars())


async def get_agency_docx_render(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    render_id: uuid.UUID,
) -> Any:
    from ato_service.db.models import SspAgencyDocxRender

    row = (
        await session.execute(
            select(SspAgencyDocxRender).where(
                SspAgencyDocxRender.workspace_id == workspace_id,
                SspAgencyDocxRender.render_id == render_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AgencyDocxRenderNotFoundError("agency docx render not found")
    return row


async def create_agency_docx_render(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source_revision_id: uuid.UUID,
    template_filename: str,
    template_bytes: bytes,
    actor_id: str,
    now: datetime,
    blob_store: Any,
    config: Any,
    model: Any,
    audit_hmac_key: bytes,
) -> Any:
    from ato_service.blobs import BlobStore
    from ato_service.db.models import SspAgencyDocxRender, SspWorkspace
    from ato_service.extraction.limits import resolve_extraction_limits_from_config
    from ato_service.ssp_workspace.agency_docx import (
        AgencyDocxError,
        extract_template_outline,
        generate_mapping_plan,
        render_template,
        review_render,
    )
    from ato_service.ssp_workspace.agency_docx_contracts import parse_mapping_plan

    normalized_filename = template_filename.strip()
    if not normalized_filename:
        raise AgencyDocxUploadError("template filename cannot be empty")
    if len(normalized_filename) > 255:
        raise AgencyDocxUploadError("template filename exceeds 255 characters")
    if not normalized_filename.lower().endswith(".docx"):
        raise AgencyDocxUploadError("agency template must be a .docx file")
    if not template_bytes:
        raise AgencyDocxUploadError("template file cannot be empty")
    if len(template_bytes) > config.limits.max_single_file_bytes:
        raise AgencyDocxUploadError("template file exceeds configured limit")

    await _scan_agency_docx_before_processing(
        config=config,
        template_bytes=template_bytes,
    )
    require_ssp_model_allowed(config)

    if not isinstance(blob_store, BlobStore):
        raise TypeError("blob_store must be a BlobStore")

    extraction_limits = resolve_extraction_limits_from_config(config)
    try:
        outline = await asyncio.to_thread(
            extract_template_outline, template_bytes, extraction_limits
        )
    except AgencyDocxError as exc:
        raise _wrap_agency_docx_error(exc) from exc

    template_sha256 = hashlib.sha256(template_bytes).hexdigest()
    cached_reference: AgencyDocxRenderReference | None = None
    async with _read_snapshot_transaction(session):
        snapshot = await _approved_export_snapshot(
            session,
            workspace_id=workspace_id,
            revision_id=source_revision_id,
        )
        workspace = (
            await session.execute(
                select(SspWorkspace).where(SspWorkspace.workspace_id == workspace_id)
            )
        ).scalar_one()
        profile_version_id = workspace.profile_version_id
        cached = (
            await session.execute(
                select(SspAgencyDocxRender).where(
                    SspAgencyDocxRender.workspace_id == workspace_id,
                    SspAgencyDocxRender.profile_version_id == profile_version_id,
                    SspAgencyDocxRender.source_revision_id == source_revision_id,
                    SspAgencyDocxRender.template_sha256 == template_sha256,
                )
            )
        ).scalar_one_or_none()
        if cached is not None:
            cached_reference = _agency_docx_render_reference(cached)
        reusable_mapping_document = None
        if cached is None:
            reusable = (
                await session.execute(
                    select(SspAgencyDocxRender)
                    .where(
                        SspAgencyDocxRender.workspace_id == workspace_id,
                        SspAgencyDocxRender.template_sha256 == template_sha256,
                        SspAgencyDocxRender.profile_version_id == profile_version_id,
                        SspAgencyDocxRender.status == "approved",
                    )
                    .order_by(SspAgencyDocxRender.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if reusable is not None:
                reusable_mapping_document = dict(reusable.mapping_plan)

    source_revision_sha256 = snapshot["content_sha256"]
    if cached_reference is not None:
        return cached_reference

    stored_template = await asyncio.to_thread(
        blob_store.store_stream,
        BytesIO(template_bytes),
        max_bytes=config.limits.max_single_file_bytes,
    )
    if stored_template.sha256 != template_sha256:
        raise AgencyDocxUploadError("template digest mismatch after storage")

    section_ids = frozenset(item["section_id"] for item in snapshot["sections"])
    reused_mapping = False
    mapping_plan_document: dict[str, Any]
    from ato_service.ssp_workspace.agency_docx_contracts import AgencyDocxContractError

    try:
        if reusable_mapping_document is not None:
            mapping_plan_document = reusable_mapping_document
            reused_mapping = True
            mapping_plan = parse_mapping_plan(
                json.dumps(mapping_plan_document),
                outline=outline,
                allowed_section_ids=section_ids,
            )
        else:
            execution = await generate_mapping_plan(outline, snapshot, model)
            mapping_plan = execution.plan
            mapping_plan_document = _mapping_plan_document(mapping_plan)
    except AgencyDocxError as exc:
        raise _wrap_agency_docx_error(exc) from exc
    except AgencyDocxContractError as exc:
        raise _wrap_agency_docx_error(exc) from exc

    try:
        rendered_bytes = await asyncio.to_thread(
            render_template,
            template_bytes,
            mapping_plan,
            snapshot,
            extraction_limits=extraction_limits,
        )
    except AgencyDocxError as exc:
        raise _wrap_agency_docx_error(exc) from exc

    try:
        review = await review_render(
            outline,
            mapping_plan,
            snapshot,
            rendered_bytes,
            model,
        )
    except AgencyDocxError as exc:
        raise _wrap_agency_docx_error(exc) from exc
    except AgencyDocxContractError as exc:
        raise _wrap_agency_docx_error(exc) from exc
    review_document = _review_result_document(review)
    status = _agency_docx_render_status(
        mapping_plan=mapping_plan,
        mapping_plan_document=mapping_plan_document,
        review_document=review_document,
    )

    stored_output = await asyncio.to_thread(
        blob_store.store_stream,
        BytesIO(rendered_bytes),
        max_bytes=config.limits.max_single_file_bytes,
    )

    _require_clean_root_transaction(session)
    current_workspace = (
        await session.execute(
            select(SspWorkspace)
            .where(SspWorkspace.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if current_workspace is None:
        raise StaleWorkspaceRevisionError("workspace changed")
    if current_workspace.profile_version_id != profile_version_id:
        raise StaleWorkspaceRevisionError("workspace profile changed")
    current_snapshot = await _approved_export_snapshot(
        session,
        workspace_id=workspace_id,
        revision_id=source_revision_id,
    )
    if current_snapshot["content_sha256"] != source_revision_sha256:
        raise StaleWorkspaceRevisionError("approved export inputs changed")
    existing = (
        await session.execute(
            select(SspAgencyDocxRender)
            .where(
                SspAgencyDocxRender.workspace_id == workspace_id,
                SspAgencyDocxRender.profile_version_id == profile_version_id,
                SspAgencyDocxRender.source_revision_id == source_revision_id,
                SspAgencyDocxRender.template_sha256 == stored_template.sha256,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _agency_docx_render_reference(existing)

    render = SspAgencyDocxRender(
        render_id=uuid.uuid4(),
        workspace_id=workspace_id,
        profile_version_id=profile_version_id,
        source_revision_id=source_revision_id,
        source_revision_sha256=source_revision_sha256,
        template_storage_key=stored_template.storage_key,
        template_sha256=stored_template.sha256,
        template_filename=normalized_filename,
        mapping_plan=mapping_plan_document,
        review_result=review_document,
        output_storage_key=stored_output.storage_key,
        output_sha256=stored_output.sha256,
        status=status,
        created_by=actor_id,
        created_at=now,
    )
    session.add(render)
    await session.flush()

    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action="ssp_agency_docx_render_created",
        object_type="ssp_agency_docx_render",
        object_id=str(render.render_id),
        metadata={
            "workspace_id": str(workspace_id),
            "source_revision_id": str(source_revision_id),
            "source_revision_sha256": source_revision_sha256,
            "template_sha256": stored_template.sha256,
            "output_sha256": stored_output.sha256,
            "reused_mapping": reused_mapping,
            "placement_count": len(mapping_plan.text_placements),
            "plan_exception_count": len(mapping_plan.exceptions),
            "review_issue_count": len(review.issues),
            "review_blocker_count": sum(
                1 for item in review.issues if item.severity == "blocker"
            ),
            "status": status,
        },
        now=now,
    )
    return _agency_docx_render_reference(render)


async def approve_agency_docx_render(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    render_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    render = await _load_agency_docx_render_for_update(
        session, workspace_id=workspace_id, render_id=render_id
    )
    if render.status != "awaiting_approval":
        raise AgencyDocxRenderStateError("render is not awaiting approval")
    if _stored_render_has_blocker(render.mapping_plan, render.review_result):
        raise AgencyDocxRenderStateError("render has unresolved blockers")
    render.status = "approved"
    render.resolved_by = actor_id
    render.resolved_at = now
    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action="ssp_agency_docx_render_approved",
        object_type="ssp_agency_docx_render",
        object_id=str(render.render_id),
        metadata={
            "workspace_id": str(workspace_id),
            "output_sha256": render.output_sha256,
        },
        now=now,
    )
    return render


async def reject_agency_docx_render(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    render_id: uuid.UUID,
    actor_id: str,
    now: datetime,
    audit_hmac_key: bytes,
) -> Any:
    render = await _load_agency_docx_render_for_update(
        session, workspace_id=workspace_id, render_id=render_id
    )
    if render.status not in {"awaiting_approval", "review_failed"}:
        raise AgencyDocxRenderStateError("render cannot be rejected")
    render.status = "rejected"
    render.resolved_by = actor_id
    render.resolved_at = now
    await _audit(
        session,
        hmac_key=audit_hmac_key,
        actor_id=actor_id,
        action="ssp_agency_docx_render_rejected",
        object_type="ssp_agency_docx_render",
        object_id=str(render.render_id),
        metadata={
            "workspace_id": str(workspace_id),
            "output_sha256": render.output_sha256,
        },
        now=now,
    )
    return render


async def read_agency_docx_preview_bytes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    render_id: uuid.UUID,
    blob_store: Any,
) -> bytes:
    from ato_service.blobs import BlobStore

    if not isinstance(blob_store, BlobStore):
        raise TypeError("blob_store must be a BlobStore")
    render = await get_agency_docx_render(
        session, workspace_id=workspace_id, render_id=render_id
    )
    if render.status not in {"awaiting_approval", "review_failed", "approved"}:
        raise AgencyDocxRenderStateError("render preview is unavailable")
    return _read_blob_bytes(blob_store, render.output_storage_key, render.output_sha256)


async def read_agency_docx_download_bytes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    render_id: uuid.UUID,
    blob_store: Any,
) -> bytes:
    from ato_service.blobs import BlobStore

    if not isinstance(blob_store, BlobStore):
        raise TypeError("blob_store must be a BlobStore")
    render = await get_agency_docx_render(
        session, workspace_id=workspace_id, render_id=render_id
    )
    if render.status != "approved":
        raise AgencyDocxRenderStateError("render download requires approval")
    return _read_blob_bytes(blob_store, render.output_storage_key, render.output_sha256)


def agency_docx_output_filename(render_id: uuid.UUID) -> str:
    return f"agency-shaped-draft-{render_id}.docx"


def _agency_docx_render_metadata(render: Any) -> dict[str, Any]:
    from ato_service.ssp_workspace.agency_docx_contracts import (
        MAX_CODE_LENGTH,
        MAX_EXCEPTIONS,
        MAX_ISSUES,
        MAX_MESSAGE_LENGTH,
        MAX_SUMMARY_LENGTH,
    )

    mapping_plan = render.mapping_plan if isinstance(render.mapping_plan, dict) else {}
    review_result = (
        render.review_result if isinstance(render.review_result, dict) else {}
    )
    status = render.status
    return {
        "render_id": str(render.render_id),
        "profile_version_id": str(render.profile_version_id),
        "source_revision_id": str(render.source_revision_id),
        "source_revision_sha256": render.source_revision_sha256,
        "template_sha256": render.template_sha256,
        "template_filename": render.template_filename,
        "output_sha256": render.output_sha256,
        "status": status,
        "created_by": render.created_by,
        "created_at": render.created_at.isoformat(),
        "resolved_by": render.resolved_by,
        "resolved_at": (
            render.resolved_at.isoformat() if render.resolved_at is not None else None
        ),
        "mapping_summary": _bounded_agency_docx_envelope_text(
            mapping_plan.get("summary"),
            maximum=MAX_SUMMARY_LENGTH,
        ),
        "mapping_exceptions": _safe_mapping_exceptions(
            mapping_plan,
            maximum=MAX_EXCEPTIONS,
            code_max=MAX_CODE_LENGTH,
            message_max=MAX_MESSAGE_LENGTH,
        ),
        "review_summary": _bounded_agency_docx_envelope_text(
            review_result.get("summary"),
            maximum=MAX_SUMMARY_LENGTH,
        ),
        "review_issues": _safe_review_issues(
            review_result,
            maximum=MAX_ISSUES,
            code_max=MAX_CODE_LENGTH,
            message_max=MAX_MESSAGE_LENGTH,
        ),
        "can_approve": status == "awaiting_approval"
        and not _stored_render_has_blocker(mapping_plan, review_result),
        "can_preview": status in {"awaiting_approval", "review_failed", "approved"},
        "can_download": status == "approved",
    }


async def _load_agency_docx_render_for_update(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    render_id: uuid.UUID,
) -> Any:
    from ato_service.db.models import SspAgencyDocxRender

    row = (
        await session.execute(
            select(SspAgencyDocxRender)
            .where(
                SspAgencyDocxRender.workspace_id == workspace_id,
                SspAgencyDocxRender.render_id == render_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise AgencyDocxRenderNotFoundError("agency docx render not found")
    return row


def _read_blob_bytes(blob_store: Any, storage_key: str, expected_sha256: str) -> bytes:
    from ato_service.storage_reconciliation import require_storage_regular_file

    prefix, digest = storage_key.split("/", maxsplit=1)
    if digest != expected_sha256:
        raise ValueError("storage key does not match content digest")
    path = require_storage_regular_file(
        blob_store.storage_root,
        "blobs",
        prefix,
        digest,
    )
    payload = path.read_bytes()
    if len(payload) < 1:
        raise ValueError("stored blob is empty")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise ValueError("stored blob digest does not match expected sha256")
    return payload


def _mapping_plan_document(plan: Any) -> dict[str, Any]:
    from ato_service.ssp_workspace.agency_docx_contracts import (
        SCHEMA_VERSION,
        MappingPlan,
    )

    if not isinstance(plan, MappingPlan):
        raise TypeError("plan must be a MappingPlan")
    control_table: dict[str, Any] = {
        "table_index": plan.control_table.table_index,
        "column_map": dict(plan.control_table.column_map),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "text_placements": [
            {
                "target_locator": item.target_locator,
                "source_ref": item.source_ref,
                "mode": item.mode,
            }
            for item in plan.text_placements
        ],
        "control_table": control_table,
        "exceptions": [
            {
                "severity": item.severity,
                "code": item.code,
                "message": item.message,
            }
            for item in plan.exceptions
        ],
        "summary": plan.summary,
    }


def _review_result_document(result: Any) -> dict[str, Any]:
    from ato_service.ssp_workspace.agency_docx_contracts import (
        SCHEMA_VERSION,
        ReviewResult,
    )

    if not isinstance(result, ReviewResult):
        raise TypeError("result must be a ReviewResult")
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": result.summary,
        "issues": [
            {
                "severity": item.severity,
                "code": item.code,
                "message": item.message,
                "locator": item.locator,
            }
            for item in result.issues
        ],
        "facts": {
            "section_count": result.facts.section_count,
            "control_count": result.facts.control_count,
            "plan_exception_count": result.facts.plan_exception_count,
            "rendered_paragraph_count": result.facts.rendered_paragraph_count,
            "rendered_cell_count": result.facts.rendered_cell_count,
            "rendered_table_count": result.facts.rendered_table_count,
        },
    }


async def _scan_agency_docx_before_processing(
    *,
    config: Any,
    template_bytes: bytes,
) -> None:
    """Apply the shared fail-closed malware gate before DOCX processing."""
    from ato_service.ssp_workspace.evidence import (
        EvidenceUploadError,
        _scan_before_processing,
    )

    try:
        await _scan_before_processing(config=config, content=template_bytes)
    except EvidenceUploadError as exc:
        raise AgencyDocxUploadError(
            "agency template rejected by malware scan",
            failure_kind="malware_detected",
        ) from exc


def _stored_render_has_blocker(
    mapping_plan: dict[str, Any],
    review_result: dict[str, Any],
) -> bool:
    mapping_document = mapping_plan if isinstance(mapping_plan, dict) else {}
    review_document = review_result if isinstance(review_result, dict) else {}
    if _mapping_plan_has_blocker(_EMPTY_MAPPING_PLAN, mapping_document):
        return True
    return _review_has_blocker(review_document)


class _EmptyMappingPlan:
    exceptions: tuple[Any, ...] = ()


_EMPTY_MAPPING_PLAN = _EmptyMappingPlan()


def _agency_docx_render_status(
    *,
    mapping_plan: Any,
    mapping_plan_document: dict[str, Any],
    review_document: dict[str, Any],
) -> str:
    if _mapping_plan_has_blocker(mapping_plan, mapping_plan_document):
        return "review_failed"
    if _review_has_blocker(review_document):
        return "review_failed"
    return "awaiting_approval"


def _mapping_plan_has_blocker(plan: Any, document: dict[str, Any]) -> bool:
    for item in getattr(plan, "exceptions", ()):
        if getattr(item, "severity", None) == "blocker":
            return True
    exceptions = document.get("exceptions")
    if not isinstance(exceptions, list):
        return False
    return any(
        isinstance(item, dict) and item.get("severity") == "blocker"
        for item in exceptions
    )


def _review_has_blocker(review_document: dict[str, Any]) -> bool:
    issues = review_document.get("issues")
    if not isinstance(issues, list):
        return False
    return any(
        isinstance(item, dict) and item.get("severity") == "blocker" for item in issues
    )


def _bounded_agency_docx_envelope_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized
    return normalized[:maximum]


def _safe_mapping_exceptions(
    mapping_plan: dict[str, Any],
    *,
    maximum: int,
    code_max: int,
    message_max: int,
) -> list[dict[str, str]]:
    raw = mapping_plan.get("exceptions")
    if not isinstance(raw, list):
        return []
    safe: list[dict[str, str]] = []
    for item in raw[:maximum]:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        code = item.get("code")
        message = item.get("message")
        if not isinstance(severity, str) or severity not in {"blocker", "warning"}:
            continue
        if not isinstance(code, str) or not isinstance(message, str):
            continue
        safe.append(
            {
                "severity": severity,
                "code": _bounded_agency_docx_envelope_text(code, maximum=code_max),
                "message": _bounded_agency_docx_envelope_text(
                    message, maximum=message_max
                ),
            }
        )
    return safe


def _safe_review_issues(
    review_result: dict[str, Any],
    *,
    maximum: int,
    code_max: int,
    message_max: int,
) -> list[dict[str, str | None]]:
    raw = review_result.get("issues")
    if not isinstance(raw, list):
        return []
    safe: list[dict[str, str | None]] = []
    for item in raw[:maximum]:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        code = item.get("code")
        message = item.get("message")
        locator = item.get("locator")
        if not isinstance(severity, str) or severity not in {"blocker", "warning"}:
            continue
        if not isinstance(code, str) or not isinstance(message, str):
            continue
        if locator is not None and not isinstance(locator, str):
            locator = None
        safe.append(
            {
                "severity": severity,
                "code": _bounded_agency_docx_envelope_text(code, maximum=code_max),
                "message": _bounded_agency_docx_envelope_text(
                    message, maximum=message_max
                ),
                "locator": (
                    _bounded_agency_docx_envelope_text(locator, maximum=128)
                    if isinstance(locator, str)
                    else None
                ),
            }
        )
    return safe
