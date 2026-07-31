"""PostgreSQL end-to-end coverage for the internal SSP drafting workflow."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.ssp_workspace.evidence import (
    EvidenceRemovalError,
    ingest_workspace_evidence,
    remove_workspace_evidence,
)
from ato_service.ssp_workspace.profile_bundles import (
    load_profile_bundle,
    resolve_profile,
)
from ato_service.ssp_workspace.profiles import activate_profile, import_profile
from ato_service.ssp_workspace.service import (
    apply_proposed_patch,
    approve_workspace_revision,
    create_initialized_workspace,
    generate_workspace_draft,
    load_workspace_envelope,
    propose_agent_patch,
    render_approved_export,
    save_section_edit,
)
from ato_service.systems import create_system
from tests.integration_support.postgres import (
    CUSTOMER_ENTERPRISE_ID,
    ORIGIN,
    postgres_integration_harness,
    run_async,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    PROJECT_ROOT / "reference" / "ssp_profiles" / "synthetic-fisma-rev5-1.0.0"
)
ACTOR_ID = "isso@example.gov"
SCREENSHOT_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff"
    b"\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=ACTOR_ID,
        groups=("system-owners",),
        csrf_token="c" * 32,
        allowed_origins=(ORIGIN,),
    )


@pytest.mark.integration
def test_ssp_workspace_reaches_approved_json_and_docx_exports(tmp_path: Path) -> None:
    """Exercise intake, generation, agent editing, approval, and export."""

    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            bundle = load_profile_bundle(PROFILE_PATH)
            profile_row = await import_profile(
                harness.session,
                bundle=bundle,
                imported_by=ACTOR_ID,
                now=harness.now,
            )
            await activate_profile(
                harness.session,
                profile_version_id=profile_row.profile_version_id,
                now=harness.now,
            )
            profile = resolve_profile(bundle, "low")

            system_result = await create_system(
                harness.session,
                principal=_principal(),
                audit_hmac_key=harness.hmac_key,
                idempotency_key="ssp-e2e-system-create",
                display_name="Synthetic Grants System",
                external_system_id="SGS-001",
                owner_group="system-owners",
                viewer_groups=["system-viewers"],
                customer_enterprise_id=CUSTOMER_ENTERPRISE_ID,
                now=harness.now,
            )
            system_id = uuid.UUID(system_result.payload["system_id"])
            workspace = await create_initialized_workspace(
                harness.session,
                system_id=system_id,
                profile_version_id=profile_row.profile_version_id,
                impact_level="low",
                actor_id=ACTOR_ID,
                now=harness.now,
                audit_hmac_key=harness.hmac_key,
            )
            envelope = await load_workspace_envelope(
                harness.session,
                workspace_id=workspace.workspace_id,
            )
            revision_id = uuid.UUID(envelope["current_revision"]["revision_id"])

            evidence_text = (
                b"The agency operates the system on premises to process federal "
                b"grant records. The authorization boundary includes the web "
                b"application, application server, and PostgreSQL database."
            )
            await ingest_workspace_evidence(
                harness.session,
                workspace_id=workspace.workspace_id,
                expected_revision_id=revision_id,
                filename="system-overview.txt",
                media_type="text/plain",
                content=evidence_text,
                actor_id=ACTOR_ID,
                now=harness.now,
                blob_store=harness.blob_store,
                config=harness.config,
                audit_hmac_key=harness.hmac_key,
            )
            envelope = await load_workspace_envelope(
                harness.session,
                workspace_id=workspace.workspace_id,
            )
            revision_id = uuid.UUID(envelope["current_revision"]["revision_id"])
            screenshot = await ingest_workspace_evidence(
                harness.session,
                workspace_id=workspace.workspace_id,
                expected_revision_id=revision_id,
                filename="system-console.png",
                media_type="image/png",
                content=SCREENSHOT_BYTES,
                actor_id=ACTOR_ID,
                now=harness.now,
                blob_store=harness.blob_store,
                config=harness.config,
                audit_hmac_key=harness.hmac_key,
            )
            envelope = await load_workspace_envelope(
                harness.session,
                workspace_id=workspace.workspace_id,
            )
            revision_id = uuid.UUID(envelope["current_revision"]["revision_id"])
            await remove_workspace_evidence(
                harness.session,
                workspace_id=workspace.workspace_id,
                evidence_artifact_id=screenshot.evidence_artifact_id,
                expected_revision_id=revision_id,
                actor_id=ACTOR_ID,
                now=harness.now,
                audit_hmac_key=harness.hmac_key,
            )
            envelope = await load_workspace_envelope(
                harness.session,
                workspace_id=workspace.workspace_id,
            )
            assert [item["display_filename"] for item in envelope["evidence"]] == [
                "system-overview.txt"
            ]

            revision_id = uuid.UUID(envelope["current_revision"]["revision_id"])
            screenshot = await ingest_workspace_evidence(
                harness.session,
                workspace_id=workspace.workspace_id,
                expected_revision_id=revision_id,
                filename="system-console.png",
                media_type="image/png",
                content=SCREENSHOT_BYTES,
                actor_id=ACTOR_ID,
                now=harness.now,
                blob_store=harness.blob_store,
                config=harness.config,
                audit_hmac_key=harness.hmac_key,
            )

            async def generation_model(prompt) -> str:
                request = json.loads(prompt.user)
                fact_id = request["evidence_facts"][0]["fact_id"]
                section_content = {
                    "system.name": "Synthetic Grants System",
                    "system.purpose": (
                        "The system processes federal grant records for the agency."
                    ),
                    "system.hosting_model": "on_premises",
                    "system.authorization_boundary": (
                        "The boundary includes the web application, application "
                        "server, and PostgreSQL database."
                    ),
                    "system.data_types": "- Federal grant records\n- Account data",
                }
                return json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "sections": [
                            {
                                "section_id": item.item_id,
                                "content": section_content[item.item_id],
                                "supporting_fact_ids": [fact_id],
                            }
                            for item in profile.ssp_required_items
                        ],
                        "controls": [
                            {
                                "control_id": control.control_id,
                                "implementation_status": "implemented",
                                "responsibility": "system_specific",
                                "implementation_statement": (
                                    "The ISSO reviews the documented control "
                                    "procedure annually and after material changes."
                                ),
                                "supporting_fact_ids": [fact_id],
                            }
                            for control in profile.controls
                        ],
                        "questions": [],
                    }
                )

            envelope = await load_workspace_envelope(
                harness.session,
                workspace_id=workspace.workspace_id,
            )
            revision_id = uuid.UUID(envelope["current_revision"]["revision_id"])
            generated = await generate_workspace_draft(
                harness.session,
                workspace_id=workspace.workspace_id,
                expected_revision_id=revision_id,
                model=generation_model,
                actor_id=ACTOR_ID,
                now=harness.now,
                audit_hmac_key=harness.hmac_key,
            )
            with pytest.raises(EvidenceRemovalError):
                await remove_workspace_evidence(
                    harness.session,
                    workspace_id=workspace.workspace_id,
                    evidence_artifact_id=screenshot.evidence_artifact_id,
                    expected_revision_id=generated.revision_id,
                    actor_id=ACTOR_ID,
                    now=harness.now,
                    audit_hmac_key=harness.hmac_key,
                )
            edited = await save_section_edit(
                harness.session,
                workspace_id=workspace.workspace_id,
                expected_revision_id=generated.revision_id,
                section_key="system.purpose",
                content=(
                    "The system supports agency staff who process and review "
                    "federal grant records."
                ),
                actor_id=ACTOR_ID,
                now=harness.now,
                audit_hmac_key=harness.hmac_key,
            )

            async def patch_model(prompt) -> str:
                request = json.loads(prompt.user)
                fact_id = request["evidence_facts"][0]["fact_id"]
                return json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "patches": [
                            {
                                "target_type": "ssp_section",
                                "target_id": "system.authorization_boundary",
                                "expected_revision": edited.version,
                                "changes": {
                                    "content": (
                                        "The authorization boundary includes the "
                                        "agency web application, application server, "
                                        "and PostgreSQL database."
                                    )
                                },
                                "supporting_fact_ids": [fact_id],
                            }
                        ],
                        "questions_to_add": [],
                        "question_ids_to_resolve": [],
                        "change_summary": "Clarified the authorization boundary.",
                    }
                )

            proposed = await propose_agent_patch(
                harness.session,
                workspace_id=workspace.workspace_id,
                expected_revision_id=edited.revision_id,
                instruction="Clarify the authorization boundary.",
                model=patch_model,
                actor_id=ACTOR_ID,
                now=harness.now,
                audit_hmac_key=harness.hmac_key,
            )
            applied = await apply_proposed_patch(
                harness.session,
                workspace_id=workspace.workspace_id,
                patch_id=proposed.patch_id,
                expected_revision_id=edited.revision_id,
                actor_id=ACTOR_ID,
                now=harness.now,
                audit_hmac_key=harness.hmac_key,
            )
            approval = await approve_workspace_revision(
                harness.session,
                workspace_id=workspace.workspace_id,
                revision_id=applied.applied_revision_id,
                actor_id=ACTOR_ID,
                now=harness.now,
                audit_hmac_key=harness.hmac_key,
            )

            json_export = await render_approved_export(
                harness.session,
                workspace_id=workspace.workspace_id,
                revision_id=approval.revision_id,
                export_format="json",
                include_open_questions=True,
            )
            docx_export = await render_approved_export(
                harness.session,
                workspace_id=workspace.workspace_id,
                revision_id=approval.revision_id,
                export_format="docx",
                include_open_questions=True,
            )
            oscal_export = await render_approved_export(
                harness.session,
                workspace_id=workspace.workspace_id,
                revision_id=approval.revision_id,
                export_format="oscal-json",
                include_open_questions=True,
            )
            exported = json.loads(json_export)
            exported_sections = {
                item["section_id"]: item for item in exported["sections"]
            }
            final_envelope = await load_workspace_envelope(
                harness.session,
                workspace_id=workspace.workspace_id,
            )

            assert exported["system"]["display_name"] == "Synthetic Grants System"
            assert exported_sections["system.purpose"]["content"].startswith(
                "The system supports agency staff"
            )
            assert exported["controls"][0]["implementation_status"] == "implemented"
            assert docx_export.startswith(b"PK")
            assert json.loads(oscal_export)["system-security-plan"]["metadata"][
                "oscal-version"
            ]
            assert len(final_envelope["evidence"]) == 2
            assert final_envelope["metrics"]["screenshots"] == 1
            assert final_envelope["approvals"][0]["revision_sha256"] == (
                final_envelope["current_revision"]["content_sha256"]
            )

    run_async(exercise())
