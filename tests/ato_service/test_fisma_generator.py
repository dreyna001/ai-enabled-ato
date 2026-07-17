"""Agency FISMA security-only draft artifact generator tests."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from ato_service.export_assembly import assemble_export_bundle, manifest_sha256
from ato_service.export_readiness import evaluate_export_readiness
from ato_service.fisma_generator import (
    GENERIC_DRAFT_NOTICE,
    PRIVACY_SCOPE_NOTICE,
    generate_fisma_security_artifacts,
)
from ato_service.fisma_template_pack import FismaTemplatePackReference, load_verified_template_pack
from ato_service.profile_artifacts import generate_profile_artifacts

ROOT = Path(__file__).resolve().parents[2]
PACK_ZIP = ROOT / "tests/fixtures/internal/internal-fisma-template-pack.zip"
PACK_DIGEST = "1350f8557eff5c44061a25599be762998b5a45629d9c2440ad7f6ebda4c1ec1c"
REVIEW_ID = "66666666-6666-4666-8666-666666666666"
RUN_ID = "33333333-3333-4333-8333-333333333333"
REVISION_ID = "11111111-1111-4111-8111-111111111111"
SYSTEM_ID = "55555555-5555-4555-8555-555555555555"
APPROVAL_ID = "77777777-7777-4777-8777-777777777777"


def _sealed_document() -> dict:
    return {
        "package": {
            "profile_id": "fisma_agency_security",
            "title": "Synthetic FISMA Package",
            "prepared_for": "Fixture",
            "reporting_period": None,
        },
        "system": {
            "display_name": "Customer Records Portal",
            "authorization_boundary": "Single VPC",
            "mission_summary": "Internal web application for agency case management.",
            "impact_level": "moderate",
            "authorization_path": "agency",
        },
        "contacts": {
            "system_owner": [],
            "isso": [],
            "issm": [],
            "control_owners": [],
            "assessors": [],
            "approvers": [],
        },
        "control_set": {
            "source": {},
            "tailoring": [],
            "organization_defined_parameters": {},
            "inheritance": [],
        },
        "security_controls": {
            "AC-1": {
                "implementation_status": "implemented",
                "implementation_statement": "Access control policy reviewed annually.",
                "responsible_parties": [],
                "evidence_links": [],
            }
        },
        "evidence": {},
        "findings": {},
        "poam_candidates": {},
        "assessor_inputs": {"sar_excerpt": {"owner": "assessor", "import_only": True}},
        "privacy": {
            "artifacts_present": False,
            "scope_notice": "Privacy review is external to this product.",
        },
        "fedramp_20x": None,
        "fedramp_rev5_transition": None,
        "fisma_agency_security": {"security_plan_sections": {}},
        "extensions": {},
    }


def _dispositions() -> list[dict]:
    return [
        {
            "matrix_row_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "decision": "weakness_confirmed",
            "edited_summary": "Authenticator review incomplete",
            "notes": None,
            "version": 2,
            "decided_by": "reviewer@example.test",
            "decided_at": "2026-07-15T12:00:00Z",
        }
    ]


def _matrix_rows() -> list[dict]:
    return [
        {
            "matrix_row_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "assessment_item_id": "IA-5",
            "model_proposed_status": "partial",
            "system_status": "partial",
            "finding_summary": "Authenticator review incomplete",
            "citations": [],
        }
    ]


def test_generic_generator_marks_hs002_blockers_and_privacy_notice() -> None:
    result = generate_fisma_security_artifacts(
        sealed_document=_sealed_document(),
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=_dispositions(),
        matrix_rows=_matrix_rows(),
        template_pack=None,
    )
    assert result.rendering_mode == "generic_draft"
    assert "hs002_template_pack_unavailable" in result.readiness_blockers
    assert GENERIC_DRAFT_NOTICE in result.contents["human/ssp-security-draft.md"]
    assert PRIVACY_SCOPE_NOTICE in result.contents["human/security-readiness.md"]
    ssp_payload = json.loads(result.contents["machine/ssp-security-draft.json"])
    assert ssp_payload["agency_parity_claimed"] is False
    poam_payload = json.loads(result.contents["machine/poam-draft.json"])
    assert len(poam_payload["human_confirmed_weaknesses"]) == 1


def test_template_pack_generator_renders_mapped_fields_without_parity_claim() -> None:
    pack = load_verified_template_pack(
        FismaTemplatePackReference(path=PACK_ZIP, expected_sha256=PACK_DIGEST)
    )
    result = generate_fisma_security_artifacts(
        sealed_document=_sealed_document(),
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=_dispositions(),
        matrix_rows=_matrix_rows(),
        template_pack=pack,
    )
    assert result.rendering_mode == "template_pack"
    assert result.readiness_blockers == ()
    assert "Customer Records Portal" in result.contents["human/ssp-security-draft.md"]
    assert "agency_parity_claimed" not in result.contents["human/ssp-security-draft.md"]


def test_fisma_export_readiness_allows_generic_draft_without_privacy_execution() -> None:
    result = evaluate_export_readiness(
        profile_id="fisma_agency_security",
        sealed_document=_sealed_document(),
        project_root=ROOT,
        runtime_config_document=None,
    )
    assert result.structural_checks_passed is True
    assert result.blockers == ()
    assert "hs002_template_pack_unavailable" in result.warnings


def test_profile_artifacts_and_export_bundle_are_reproducible() -> None:
    runtime_config = {
        "FISMA_TEMPLATE_PACK_FILE_REFERENCE": {
            "path": str(PACK_ZIP),
            "expected_sha256": PACK_DIGEST,
        }
    }
    dispositions = _dispositions()
    matrix_rows = _matrix_rows()
    sealed = _sealed_document()
    artifacts = generate_profile_artifacts(
        profile_id="fisma_agency_security",
        sealed_document=sealed,
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=dispositions,
        matrix_rows=matrix_rows,
        runtime_config_document=runtime_config,
    )
    draft_manifest = {
        "schema_version": "1.0.0",
        "profile_id": "fisma_agency_security",
        "package_revision_id": REVISION_ID,
        "run_id": RUN_ID,
        "review_revision_id": REVIEW_ID,
        "authority_manifest_id": "authority.v2",
        "files": artifacts.files,
    }
    expected_hash = manifest_sha256(draft_manifest)

    bundle = assemble_export_bundle(
        export_id=APPROVAL_ID,
        profile_id="fisma_agency_security",
        system_id=SYSTEM_ID,
        package_revision_id=REVISION_ID,
        run_id=RUN_ID,
        review_revision_id=REVIEW_ID,
        approval_id=APPROVAL_ID,
        authority_manifest_id="authority.v2",
        created_at="2026-07-15T12:00:00Z",
        sealed_document=sealed,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
        expected_payload_manifest_sha256=expected_hash,
        runtime_config_document=runtime_config,
    )

    with zipfile.ZipFile(io.BytesIO(bundle.zip_bytes)) as archive:
        names = set(archive.namelist())
        assert "machine/ssp-security-draft.json" in names
        assert "human/poam-draft.md" in names
        assert "validation/fisma-export-readiness.json" in names
        assert "validation/schema-purity.json" not in names
        assert "human/readiness-summary.md" not in names
        assert "validation/export-readiness.json" not in names
        assert "machine/fisma-agency-security-draft.json" not in names
        for entry in bundle.manifest["files"]:
            payload = archive.read(entry["path"])
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]

    second = assemble_export_bundle(
        export_id=APPROVAL_ID,
        profile_id="fisma_agency_security",
        system_id=SYSTEM_ID,
        package_revision_id=REVISION_ID,
        run_id=RUN_ID,
        review_revision_id=REVIEW_ID,
        approval_id=APPROVAL_ID,
        authority_manifest_id="authority.v2",
        created_at="2026-07-15T12:00:00Z",
        sealed_document=sealed,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
        expected_payload_manifest_sha256=expected_hash,
        runtime_config_document=runtime_config,
    )
    assert second.zip_bytes == bundle.zip_bytes
