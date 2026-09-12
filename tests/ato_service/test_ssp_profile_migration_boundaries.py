"""PostgreSQL behavior tests for bounded SSP profile migrations."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.db.models import SspWorkspace, SspWorkspaceRevision
from ato_service.ssp_workspace.contracts import (
    ControlState,
    FactContent,
    Provenance,
    QuestionContent,
    QuestionState,
    RevisionContent,
    SectionState,
)
from ato_service.ssp_workspace.persistence import (
    StaleWorkspaceRevisionError,
    approve_current_revision,
    save_revision,
)
from ato_service.ssp_workspace.profile_bundles import load_profile_bundle
from ato_service.ssp_workspace.profiles import activate_profile, import_profile
from ato_service.ssp_workspace.service import (
    WorkspaceProfileValidationError,
    create_initialized_workspace,
    migrate_workspace_profile,
)
from ato_service.systems import create_system
from tests.integration_support.postgres import postgres_integration_harness


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    PROJECT_ROOT / "reference" / "ssp_profiles" / "agency-fisma-nist-sp800-53-rev5-1.0.0"
)
ACTOR_ID = "migration-isso@example.gov"
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=ACTOR_ID,
        groups=("system-owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


async def _create_workspace(harness, *, impact_level: str = "low"):
    bundle = load_profile_bundle(PROFILE_PATH)
    profile_row = await import_profile(
        harness.session,
        bundle=bundle,
        imported_by=ACTOR_ID,
        now=NOW,
    )
    await activate_profile(
        harness.session,
        profile_version_id=profile_row.profile_version_id,
        now=NOW,
    )
    system_result = await create_system(
        harness.session,
        principal=_principal(),
        audit_hmac_key=harness.hmac_key,
        idempotency_key=f"profile-migration-system-{uuid.uuid4()}",
        display_name="Profile migration boundary system",
        external_system_id=None,
        owner_group="system-owners",
        viewer_groups=["system-viewers"],
        customer_enterprise_id="dev-local-enterprise",
        now=NOW,
    )
    workspace = await create_initialized_workspace(
        harness.session,
        system_id=uuid.UUID(system_result.payload["system_id"]),
        profile_version_id=profile_row.profile_version_id,
        impact_level=impact_level,
        actor_id=ACTOR_ID,
        now=NOW,
        audit_hmac_key=harness.hmac_key,
    )
    return bundle, profile_row, workspace


async def _seed_reviewed_revision(
    harness,
    workspace,
    *,
    question_target_section_id: str | None = None,
):
    current = await harness.session.get(
        SspWorkspaceRevision,
        workspace.current_revision_id,
    )
    assert current is not None
    content = RevisionContent.model_validate(current.content)
    reviewed_section_values = {
        "system.name": "Atlas system",
        "system.components": "Existing components",
        "system.hosting_model": "on_premises",
    }
    sections = tuple(
        section.model_copy(
            update={
                "content": reviewed_section_values[section.key],
                "state": SectionState.REVIEWED,
            }
        )
        if section.key in reviewed_section_values
        else section
        for section in content.sections
    )
    controls = tuple(
        control.model_copy(
            update={
                "implementation_status": "implemented",
                "responsibility": "system_specific",
                "implementation_statement": "The agency operates this control.",
                "state": ControlState.REVIEWED,
            }
        )
        if control.control_id in {"AC-1", "AC-2"}
        else control
        for control in content.controls
    )
    facts = {
        item.key: item
        for item in content.facts
    }
    facts["system.categorization_status"] = FactContent(
        key="system.categorization_status",
        value="confirmed",
        provenance=Provenance.ISSO_ENTERED,
    )
    facts["system.system_definition_status"] = FactContent(
        key="system.system_definition_status",
        value="confirmed",
        provenance=Provenance.ISSO_ENTERED,
    )
    facts["system.information_types_status"] = FactContent(
        key="system.information_types_status",
        value="confirmed",
        provenance=Provenance.ISSO_ENTERED,
    )
    reviewed_content = content.model_copy(
        update={
            "facts": tuple(facts[key] for key in sorted(facts)),
            "sections": sections,
            "controls": controls,
            "questions": (
                content.questions
                + (
                    QuestionContent(
                        question_id=uuid.uuid4(),
                        question="Provide the removed section details.",
                        target_type="ssp_section",
                        target_key=question_target_section_id,
                        owner_type="isso",
                        state=QuestionState.OPEN,
                    ),
                )
                if question_target_section_id is not None
                else ()
            ),
        }
    )
    reviewed = await save_revision(
        harness.session,
        workspace_id=workspace.workspace_id,
        content=reviewed_content,
        created_by=ACTOR_ID,
        now=NOW,
        expected_revision_id=current.revision_id,
    )
    approval = await approve_current_revision(
        harness.session,
        workspace_id=workspace.workspace_id,
        revision_id=reviewed.revision_id,
        approved_by=ACTOR_ID,
        now=NOW,
    )
    await harness.session.commit()
    return reviewed, approval, reviewed_content


async def _activate_variant(harness, bundle):
    row = await import_profile(
        harness.session,
        bundle=bundle,
        imported_by=ACTOR_ID,
        now=NOW,
    )
    await activate_profile(
        harness.session,
        profile_version_id=row.profile_version_id,
        now=NOW,
    )
    await harness.session.commit()
    return row


def _migration_bundle(
    bundle,
    *,
    version: str,
    enum_policy=None,
    remove_section_id: str | None = None,
):
    extra_control_id = next(
        control_id
        for control_id in bundle.high_control_ids
        if control_id not in bundle.low_control_ids
    )
    changed_items = tuple(
        replace(item, title="Updated system name requirement")
        if item.item_id in {
            "system.name",
            "system.components",
            "system.data_types",
            "system.hosting_model",
        }
        else item
        for item in bundle.ssp_required_items
    )
    changed_items = tuple(
        replace(item, allowed_values=("agency_cloud", "agency_hybrid"))
        if item.item_id == "system.hosting_model"
        else item
        for item in changed_items
    )
    changed_controls = tuple(
        replace(control, title="Updated access control requirement")
        if control.control_id == "AC-2"
        else control
        for control in bundle.catalog_controls
    )
    low_control_ids = tuple(
        sorted(
            (set(bundle.low_control_ids) - {"AC-1"})
            | {extra_control_id}
        )
    )
    return replace(
        bundle,
        manifest=replace(bundle.manifest, profile_version=version),
        catalog_controls=changed_controls,
        low_control_ids=low_control_ids,
        ssp_required_items=tuple(
            item
            for item in changed_items
            if item.item_id != remove_section_id
        ),
        **({"control_response": enum_policy} if enum_policy is not None else {}),
    )


@pytest.mark.integration
def test_profile_migration_preserves_approved_history_and_marks_changed_content(
    tmp_path: Path,
) -> None:
    """Migration creates a working child without mutating approved history."""

    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path,
            now=NOW,
            ordinary_session=True,
        ) as harness:
            bundle, old_profile, workspace = await _create_workspace(harness)
            approved, approval, approved_content = await _seed_reviewed_revision(
                harness,
                workspace,
                question_target_section_id="system.interconnections",
            )
            target_profile = await _activate_variant(
                harness,
                _migration_bundle(
                    bundle,
                    version="2.0.0",
                    remove_section_id="system.interconnections",
                ),
            )

            migrated, profile_diff = await migrate_workspace_profile(
                harness.session,
                workspace_id=workspace.workspace_id,
                profile_version_id=target_profile.profile_version_id,
                impact_level="low",
                expected_revision_id=approved.revision_id,
                actor_id=ACTOR_ID,
                now=NOW,
                audit_hmac_key=harness.hmac_key,
            )
            await harness.session.commit()

            assert migrated.parent_revision_id == approved.revision_id
            assert migrated.status == "working"
            assert profile_diff.added_control_ids == (next(
                control_id
                for control_id in bundle.high_control_ids
                if control_id not in bundle.low_control_ids
            ),)
            assert profile_diff.removed_control_ids == ("AC-1",)
            assert profile_diff.changed_control_ids == ("AC-2",)
            assert "system.name" in profile_diff.changed_ssp_item_ids
            assert "system.components" in profile_diff.changed_ssp_item_ids
            assert "system.data_types" in profile_diff.changed_ssp_item_ids
            assert "system.hosting_model" in profile_diff.changed_ssp_item_ids

            historical = await harness.session.get(
                SspWorkspaceRevision,
                approved.revision_id,
            )
            assert historical is not None
            assert historical.status == "approved"
            assert historical.content_sha256 == approved.content_sha256
            assert historical.content == approved_content.model_dump(mode="json")
            assert approval.revision_sha256 == approved.content_sha256

            migrated_content = RevisionContent.model_validate(migrated.content)
            migrated_sections = {
                item.key: item for item in migrated_content.sections
            }
            migrated_controls = {
                item.control_id: item for item in migrated_content.controls
            }
            migrated_facts = {
                item.key: item for item in migrated_content.facts
            }
            assert migrated_sections["system.name"].content == "Atlas system"
            assert migrated_sections["system.name"].state is SectionState.EDITED
            assert migrated_sections["system.components"].content == (
                "Existing components"
            )
            assert migrated_sections["system.components"].state is SectionState.EDITED
            assert migrated_sections["system.hosting_model"].content == "on_premises"
            assert migrated_sections["system.hosting_model"].state is SectionState.EDITED
            assert migrated_controls["AC-2"].state is ControlState.PARTIAL
            assert migrated_controls["AC-2"].implementation_statement == (
                "The agency operates this control."
            )
            assert "Profile migration changed this control requirement" in (
                migrated_controls["AC-2"].unresolved_reason or ""
            )
            assert "AC-1" not in migrated_controls
            assert migrated_controls[next(
                control_id
                for control_id in bundle.high_control_ids
                if control_id not in bundle.low_control_ids
            )].state is ControlState.EMPTY
            migrated_questions = {
                item.target_key: item for item in migrated_content.questions
            }
            historical_content = RevisionContent.model_validate(historical.content)
            historical_questions = {
                item.target_key: item for item in historical_content.questions
            }
            assert migrated_questions["system.interconnections"].state is QuestionState.DISMISSED
            assert historical_questions["system.interconnections"].state is QuestionState.OPEN
            assert migrated_facts["system.categorization_status"].value == "stale"
            assert migrated_facts["system.system_definition_status"].value == "stale"
            assert migrated_facts["system.information_types_status"].value == "stale"
            assert migrated_facts["system.provisional_impact_level"].value == "low"
            assert workspace.profile_version_id == target_profile.profile_version_id
            assert old_profile.profile_version_id != target_profile.profile_version_id

    asyncio.run(exercise())


@pytest.mark.integration
def test_profile_and_impact_migration_diff_uses_actual_source_and_target_baselines(
    tmp_path: Path,
) -> None:
    """A combined migration reports baseline changes for both requested impacts."""

    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path,
            now=NOW,
            ordinary_session=True,
        ) as harness:
            bundle, _, workspace = await _create_workspace(harness)
            target_bundle = _migration_bundle(bundle, version="2.1.0")
            target_profile = await _activate_variant(harness, target_bundle)
            current_revision_id = workspace.current_revision_id

            _, profile_diff = await migrate_workspace_profile(
                harness.session,
                workspace_id=workspace.workspace_id,
                profile_version_id=target_profile.profile_version_id,
                impact_level="high",
                expected_revision_id=current_revision_id,
                actor_id=ACTOR_ID,
                now=NOW,
                audit_hmac_key=harness.hmac_key,
            )
            await harness.session.rollback()

            expected_old_controls = set(bundle.low_control_ids)
            expected_new_controls = set(bundle.high_control_ids)
            assert profile_diff.impact_level == "high"
            assert profile_diff.added_control_ids == tuple(
                sorted(expected_new_controls - expected_old_controls)
            )
            assert profile_diff.removed_control_ids == tuple(
                sorted(expected_old_controls - expected_new_controls)
            )
            assert "AC-2" in profile_diff.changed_control_ids
            assert set(profile_diff.changed_control_ids) <= (
                expected_old_controls & expected_new_controls
            )

    asyncio.run(exercise())


@pytest.mark.integration
def test_same_profile_version_and_impact_is_a_persisted_noop(tmp_path: Path) -> None:
    """An exact repeat does not create a revision or change the profile pin."""

    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path,
            now=NOW,
            ordinary_session=True,
        ) as harness:
            _, profile_row, workspace = await _create_workspace(harness)
            current_revision_id = workspace.current_revision_id
            result, profile_diff = await migrate_workspace_profile(
                harness.session,
                workspace_id=workspace.workspace_id,
                profile_version_id=profile_row.profile_version_id,
                impact_level="low",
                expected_revision_id=current_revision_id,
                actor_id=ACTOR_ID,
                now=NOW,
                audit_hmac_key=harness.hmac_key,
            )
            assert result.revision_id == current_revision_id
            assert workspace.current_revision_id == current_revision_id
            assert profile_diff.added_control_ids == ()
            assert profile_diff.removed_control_ids == ()
            assert profile_diff.changed_control_ids == ()
            assert profile_diff.added_ssp_item_ids == ()
            assert profile_diff.removed_ssp_item_ids == ()
            assert profile_diff.changed_ssp_item_ids == ()
            await harness.session.commit()

            count = await harness.session.scalar(
                select(func.count(SspWorkspaceRevision.revision_id))
            )
            assert count == 1

    asyncio.run(exercise())


@pytest.mark.integration
def test_incompatible_enum_value_fails_before_migration_write(tmp_path: Path) -> None:
    """An old control enum remains untouched when the target disallows it."""

    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path,
            now=NOW,
            ordinary_session=True,
        ) as harness:
            bundle, old_profile, workspace = await _create_workspace(harness)
            current, _, _ = await _seed_reviewed_revision(harness, workspace)
            workspace_id = workspace.workspace_id
            old_profile_id = old_profile.profile_version_id
            current_revision_id = current.revision_id
            enum_policy = replace(
                bundle.control_response,
                implementation_statuses=tuple(
                    value
                    for value in bundle.control_response.implementation_statuses
                    if value != "implemented"
                ),
            )
            target_profile = await _activate_variant(
                harness,
                _migration_bundle(
                    bundle,
                    version="3.0.0",
                    enum_policy=enum_policy,
                ),
            )

            with pytest.raises(
                WorkspaceProfileValidationError,
                match="incompatible control enum values",
            ):
                await migrate_workspace_profile(
                    harness.session,
                    workspace_id=workspace_id,
                    profile_version_id=target_profile.profile_version_id,
                    impact_level="low",
                    expected_revision_id=current_revision_id,
                    actor_id=ACTOR_ID,
                    now=NOW,
                    audit_hmac_key=harness.hmac_key,
                )
            await harness.session.rollback()

            workspace_row = await harness.session.get(
                SspWorkspace,
                workspace_id,
            )
            assert workspace_row is not None
            assert workspace_row.profile_version_id == old_profile_id
            assert workspace_row.current_revision_id == current_revision_id
            assert await harness.session.get(
                SspWorkspaceRevision,
                current_revision_id,
            ) is not None

    asyncio.run(exercise())


@pytest.mark.integration
def test_stale_expected_revision_is_rejected_by_profile_migration(tmp_path: Path) -> None:
    """A migration cannot overwrite a revision committed by another writer."""

    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path,
            now=NOW,
            ordinary_session=True,
        ) as harness:
            bundle, profile_row, workspace = await _create_workspace(harness)
            workspace_id = workspace.workspace_id
            old_profile_id = profile_row.profile_version_id
            current = await harness.session.get(
                SspWorkspaceRevision,
                workspace.current_revision_id,
            )
            assert current is not None
            current_revision_id = current.revision_id
            await harness.session.commit()
            target_profile = await _activate_variant(
                harness,
                _migration_bundle(bundle, version="4.0.0"),
            )

            replacement = RevisionContent.model_validate(current.content)
            replacement_revision = await save_revision(
                harness.session,
                workspace_id=workspace_id,
                content=replacement,
                created_by=ACTOR_ID,
                now=NOW,
                expected_revision_id=current_revision_id,
            )
            replacement_revision_id = replacement_revision.revision_id
            await harness.session.commit()

            with pytest.raises(StaleWorkspaceRevisionError, match="revision changed"):
                await migrate_workspace_profile(
                    harness.session,
                    workspace_id=workspace_id,
                    profile_version_id=target_profile.profile_version_id,
                    impact_level="low",
                    expected_revision_id=current_revision_id,
                    actor_id=ACTOR_ID,
                    now=NOW,
                    audit_hmac_key=harness.hmac_key,
                )
            await harness.session.rollback()
            workspace_row = await harness.session.get(SspWorkspace, workspace_id)
            assert workspace_row is not None
            assert replacement_revision_id == workspace_row.current_revision_id
            assert old_profile_id == workspace_row.profile_version_id

    asyncio.run(exercise())
