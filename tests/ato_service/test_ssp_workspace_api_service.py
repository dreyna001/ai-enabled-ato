from __future__ import annotations

from pathlib import Path

from ato_service.ssp_workspace.api import (
    CreateWorkspaceRequest,
    build_ssp_workspace_router,
)
from ato_service.ssp_workspace.contracts import (
    EvidenceLink,
    ProfileRequirement,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.profiles import (
    deserialize_profile_bundle,
    serialize_profile_bundle,
)
from ato_service.ssp_workspace.profile_bundles import load_profile_bundle
from ato_service.ssp_workspace.profile_bundles import resolve_profile
from ato_service.ssp_workspace.service import (
    _effective_metric_facts,
    _impact_profile_diff,
)
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_workspace_router_exposes_complete_bounded_workflow() -> None:
    paths = {route.path for route in build_ssp_workspace_router().routes}

    assert "/ssp-systems" in paths
    assert "/ssp-profiles" in paths
    assert "/ssp-profiles/import" in paths
    assert "/ssp-workspaces" in paths
    assert "/ssp-workspaces/{workspace_id}/evidence" in paths
    assert (
        "/ssp-workspaces/{workspace_id}/evidence/{evidence_artifact_id}"
        in paths
    )
    assert "/ssp-workspaces/{workspace_id}/generate" in paths
    assert "/ssp-workspaces/{workspace_id}/categorization" in paths
    assert (
        "/ssp-workspaces/{workspace_id}/agent/patches/{patch_id}/apply"
        in paths
    )
    assert "/ssp-workspaces/{workspace_id}/approve" in paths
    assert (
        "/ssp-workspaces/{workspace_id}/revisions/{revision_id}/restore"
        in paths
    )
    assert "/ssp-workspaces/{workspace_id}/migrate-profile" in paths
    assert "/ssp-workspaces/{workspace_id}/exports/{export_format}" in paths


def test_workspace_creation_does_not_require_impact_level() -> None:
    request = CreateWorkspaceRequest.model_validate(
        {
            "system_id": str(uuid.uuid4()),
            "profile_version_id": str(uuid.uuid4()),
        }
    )

    assert request.model_dump().keys() == {"system_id", "profile_version_id"}


def test_categorization_change_diffs_control_baselines() -> None:
    bundle = load_profile_bundle(
        PROJECT_ROOT
        / "reference"
        / "ssp_profiles"
        / "synthetic-fisma-rev5-1.0.0"
    )

    diff = _impact_profile_diff(
        resolve_profile(bundle, "low"),
        resolve_profile(bundle, "moderate"),
    )

    assert diff.impact_level == "moderate"
    assert diff.added_control_ids
    assert diff.removed_control_ids == ()


def test_stored_builtin_profile_round_trips_without_semantic_loss() -> None:
    source = load_profile_bundle(
        PROJECT_ROOT
        / "reference"
        / "ssp_profiles"
        / "agency-fisma-nist-sp800-53-rev5-5.2.0-1"
    )

    restored = deserialize_profile_bundle(serialize_profile_bundle(source))

    assert restored == source


def test_metric_projection_requires_evidence_for_agent_generated_section() -> None:
    requirement = ProfileRequirement(
        key="system.components",
        value_type="array",
    )
    generated_without_evidence = RevisionContent(
        sections=(
            SectionContent(
                key="system.components",
                title="Components",
                content="- API\n- Database",
                state=SectionState.GENERATED,
            ),
        )
    )

    assert _effective_metric_facts(
        generated_without_evidence, (requirement,)
    ) == ()

    artifact_id = uuid.uuid4()
    grounded = generated_without_evidence.model_copy(
        update={
            "sections": (
                generated_without_evidence.sections[0].model_copy(
                    update={
                        "evidence": (
                            EvidenceLink(
                                artifact_id=artifact_id,
                                locator={"kind": "page", "page": 1},
                            ),
                        )
                    }
                ),
            )
        }
    )
    facts = _effective_metric_facts(grounded, (requirement,))

    assert facts[0].key == "system.components"
    assert facts[0].value == ["API", "Database"]
