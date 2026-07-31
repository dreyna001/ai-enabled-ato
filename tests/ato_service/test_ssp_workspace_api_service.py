from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.main import create_app
from ato_service.ssp_workspace.api import (
    CreateWorkspaceRequest,
    build_ssp_workspace_router,
    get_db_session,
    get_read_principal,
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_workspace_router_exposes_complete_bounded_workflow() -> None:
    paths = {route.path for route in build_ssp_workspace_router().routes}

    assert "/ssp-systems" in paths
    assert "/ssp-profiles" in paths
    assert "/ssp-profiles/import" in paths
    assert "/ssp-workspaces" in paths
    assert "/ssp-workspaces/{workspace_id}/evidence" in paths
    assert "/ssp-workspaces/{workspace_id}/evidence/{evidence_artifact_id}" in paths
    assert "/ssp-workspaces/{workspace_id}/generate" in paths
    assert "/ssp-workspaces/{workspace_id}/categorization" in paths
    assert "/ssp-workspaces/{workspace_id}/agent/patches/{patch_id}/apply" in paths
    assert "/ssp-workspaces/{workspace_id}/approve" in paths
    assert "/ssp-workspaces/{workspace_id}/revisions/{revision_id}/restore" in paths
    assert "/ssp-workspaces/{workspace_id}/migrate-profile" in paths
    assert "/ssp-workspaces/{workspace_id}/exports/{export_format}" in paths
    assert "/ssp-workspaces/{workspace_id}/agency-docx-renders" in paths
    assert (
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/preview"
        in paths
    )
    assert (
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/download"
        in paths
    )
    assert (
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/approve"
        in paths
    )
    assert (
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/reject" in paths
    )


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
        PROJECT_ROOT / "reference" / "ssp_profiles" / "synthetic-fisma-rev5-1.0.0"
    )

    diff = _impact_profile_diff(
        resolve_profile(bundle, "low"),
        resolve_profile(bundle, "moderate"),
    )

    assert diff.impact_level == "moderate"
    assert diff.added_control_ids
    assert diff.removed_control_ids == ()


def test_stored_builtin_profiles_round_trip_without_semantic_loss() -> None:
    for version in ("1.1.0", "1.2.0"):
        source = load_profile_bundle(
            PROJECT_ROOT
            / "reference"
            / "ssp_profiles"
            / f"agency-fisma-nist-sp800-53-rev5-{version}"
        )

        restored = deserialize_profile_bundle(serialize_profile_bundle(source))

        assert restored == source
    assert source.implementation_statement_policy.agent_instructions.statement_content
    assert source.implementation_statement_policy.agent_instructions.semantic_review


def test_deserialize_legacy_stored_manifest_without_catalog_release_field() -> None:
    source = load_profile_bundle(
        PROJECT_ROOT
        / "reference"
        / "ssp_profiles"
        / "agency-fisma-nist-sp800-53-rev5-1.1.0"
    )
    document = serialize_profile_bundle(source)
    del document["manifest"]["nist_control_catalog_release"]

    restored = deserialize_profile_bundle(document)

    assert restored.manifest.nist_control_catalog_release == "5.2.0"


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

    assert _effective_metric_facts(generated_without_evidence, (requirement,)) == ()

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


def test_export_openapi_declares_explicit_export_format_enum() -> None:
    app = create_app(readiness_probe=AsyncMock(return_value={}))
    export_format = app.openapi()["paths"][
        "/ssp-workspaces/{workspace_id}/exports/{export_format}"
    ]["get"]["parameters"][1]["schema"]

    assert export_format == {
        "enum": ["json", "docx", "oscal-json"],
        "title": "Export Format",
        "type": "string",
    }


def test_get_export_oscal_json_sets_media_type_and_filename() -> None:
    workspace_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    revision_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    principal = AuthenticatedPrincipal(
        actor_id="isso@example.gov",
        groups=("system-owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example.gov",),
    )
    app = FastAPI()
    app.include_router(build_ssp_workspace_router())
    app.dependency_overrides[get_read_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    with (
        patch(
            "ato_service.ssp_workspace.api._authorize_workspace",
            new=AsyncMock(),
        ),
        patch(
            "ato_service.ssp_workspace.api.render_approved_export",
            new=AsyncMock(return_value=b'{"system-security-plan":{}}\n'),
        ) as render_export,
    ):
        with TestClient(app) as client:
            response = client.get(
                f"/ssp-workspaces/{workspace_id}/exports/oscal-json",
                params={
                    "revision_id": str(revision_id),
                    "include_open_questions": "true",
                },
            )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="ssp-{revision_id}.oscal.json"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    render_export.assert_awaited_once()
    assert render_export.await_args.kwargs == {
        "workspace_id": workspace_id,
        "revision_id": revision_id,
        "export_format": "oscal-json",
        "include_open_questions": True,
    }


def test_get_export_rejects_unsupported_export_format_path() -> None:
    app = FastAPI()
    app.include_router(build_ssp_workspace_router())
    app.dependency_overrides[get_read_principal] = lambda: AuthenticatedPrincipal(
        actor_id="isso@example.gov",
        groups=("system-owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example.gov",),
    )
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    with TestClient(app) as client:
        response = client.get(
            "/ssp-workspaces/11111111-1111-4111-8111-111111111111/exports/yaml",
            params={
                "revision_id": "22222222-2222-4222-8222-222222222222",
                "include_open_questions": "true",
            },
        )

    assert response.status_code == 422


def test_get_export_oscal_validation_failure_uses_validation_error_shape() -> None:
    from ato_service.ssp_workspace.oscal_export import OscalSspExportError

    workspace_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    revision_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    principal = AuthenticatedPrincipal(
        actor_id="isso@example.gov",
        groups=("system-owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example.gov",),
    )
    app = FastAPI()
    app.include_router(build_ssp_workspace_router())
    app.dependency_overrides[get_read_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    with (
        patch(
            "ato_service.ssp_workspace.api._authorize_workspace",
            new=AsyncMock(),
        ),
        patch(
            "ato_service.ssp_workspace.api.render_approved_export",
            new=AsyncMock(
                side_effect=OscalSspExportError(
                    "C:\\secret\\paths\\oscal-ssp.schema.json validation failed"
                )
            ),
        ),
    ):
        with TestClient(app) as client:
            response = client.get(
                f"/ssp-workspaces/{workspace_id}/exports/oscal-json",
                params={
                    "revision_id": str(revision_id),
                    "include_open_questions": "true",
                },
            )

    assert response.status_code == 422
    assert response.json() == {
        "error": "validation_failed",
        "error_code": "validation_failed",
    }
