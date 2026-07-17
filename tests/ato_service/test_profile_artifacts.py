"""Profile artifact generator and export reproducibility tests."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from ato_service.export_assembly import assemble_export_bundle, manifest_sha256
from ato_service.export_readiness import evaluate_export_readiness
from ato_service.fedramp_schema import validate_fedramp_official_payload
from ato_service.profile_artifacts import generate_profile_artifacts

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "profile_artifacts"

REVIEW_ID = "66666666-6666-4666-8666-666666666666"
RUN_ID = "33333333-3333-4333-8333-333333333333"
REVISION_ID = "11111111-1111-4111-8111-111111111111"
SYSTEM_ID = "55555555-5555-4555-8555-555555555555"
APPROVAL_ID = "77777777-7777-4777-8777-777777777777"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _matrix_rows() -> list[dict]:
    return [
        {
            "matrix_row_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "assessment_item_id": "FR-1",
            "model_proposed_status": "insufficient_evidence",
            "system_status": "insufficient_evidence",
            "finding_summary": "Fixture row",
            "citations": [],
        }
    ]


def _dispositions() -> list[dict]:
    return [
        {
            "matrix_row_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "decision": "accepted",
            "edited_summary": None,
            "notes": None,
            "version": 2,
            "decided_by": "reviewer@example.test",
            "decided_at": "2026-07-15T12:00:00Z",
        }
    ]


def test_fedramp_fixture_cpo_validates_against_vendored_schema() -> None:
    document = _load_fixture("fedramp-20x-class-c-sealed.json")
    result = validate_fedramp_official_payload(
        artifact_key="cpo",
        payload=document["fedramp_20x"]["cpo"],
        project_root=ROOT,
    )
    assert result.structurally_valid is True
    assert result.authority_id == "fedramp-schema-cpo-2026-06-24"


def test_fedramp_20x_generates_spec_paths_and_schema_ids() -> None:
    document = _load_fixture("fedramp-20x-class-c-sealed.json")
    artifacts = generate_profile_artifacts(
        profile_id="fedramp_20x_program",
        sealed_document=document,
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=_dispositions(),
        matrix_rows=_matrix_rows(),
        project_root=ROOT,
    )
    paths = {entry["path"] for entry in artifacts.files}
    assert "machine/cpo.json" in paths
    assert "human/cpo.md" in paths
    assert "machine/sdr.json" in paths
    assert "machine/ocr.json" in paths
    assert "human/scg-readiness.md" in paths
    assert "machine/ksi-summary.json" in paths
    assert "validation/export-readiness.json" in paths
    assert "validation/schema-purity.json" in paths
    assert "provenance/assessor-imports.json" in paths

    cpo_descriptor = next(item for item in artifacts.files if item["path"] == "machine/cpo.json")
    assert cpo_descriptor["official_schema_id"] == "fedramp-schema-cpo-2026-06-24"
    assert cpo_descriptor["sha256"] == hashlib.sha256(artifacts.contents["machine/cpo.json"]).hexdigest()

    readiness = json.loads(artifacts.contents["validation/export-readiness.json"])
    assert readiness["draft_only"] is True
    assert "hs_001_authority_review_pending" in readiness["warnings"]


def test_fedramp_rev5_preserves_imported_sections_and_provenance() -> None:
    document = _load_fixture("fedramp-rev5-transition-sealed.json")
    artifacts = generate_profile_artifacts(
        profile_id="fedramp_rev5_transition",
        sealed_document=document,
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=_dispositions(),
        matrix_rows=_matrix_rows(),
        project_root=ROOT,
    )
    paths = {entry["path"] for entry in artifacts.files}
    assert "machine/ssp.json" in paths
    assert "machine/sar.json" in paths
    assert "machine/oscal.json" in paths
    assert "human/rev5-transition-readiness.md" in paths
    assert "validation/export-readiness.json" in paths
    assert "validation/schema-purity.json" not in paths

    sar_payload = json.loads(artifacts.contents["machine/sar.json"])
    assert sar_payload["owner"] == "assessor"
    assert sar_payload["import_only"] is True

    provenance = json.loads(artifacts.contents["provenance/assessor-imports.json"])
    assert provenance["import_only"] is True
    assert "sar-import" in provenance["assessor_inputs"]


def test_generate_profile_artifacts_is_deterministic() -> None:
    document = _load_fixture("fedramp-20x-class-c-sealed.json")
    kwargs = {
        "profile_id": "fedramp_20x_program",
        "sealed_document": document,
        "review_revision_id": uuid.UUID(REVIEW_ID),
        "run_id": uuid.UUID(RUN_ID),
        "dispositions": _dispositions(),
        "matrix_rows": _matrix_rows(),
        "project_root": ROOT,
    }
    first = generate_profile_artifacts(**kwargs)
    second = generate_profile_artifacts(**kwargs)
    assert first.files == second.files
    assert first.contents == second.contents


def test_assemble_export_bundle_reproduces_fedramp_manifest_hash() -> None:
    document = _load_fixture("fedramp-20x-class-c-sealed.json")
    artifacts = generate_profile_artifacts(
        profile_id="fedramp_20x_program",
        sealed_document=document,
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=_dispositions(),
        matrix_rows=_matrix_rows(),
        project_root=ROOT,
    )
    draft_manifest = {
        "schema_version": "1.0.0",
        "profile_id": "fedramp_20x_program",
        "package_revision_id": REVISION_ID,
        "run_id": RUN_ID,
        "review_revision_id": REVIEW_ID,
        "authority_manifest_id": "authority.v2",
        "files": artifacts.files,
    }
    expected_hash = manifest_sha256(draft_manifest)

    bundle = assemble_export_bundle(
        export_id=APPROVAL_ID,
        profile_id="fedramp_20x_program",
        system_id=SYSTEM_ID,
        package_revision_id=REVISION_ID,
        run_id=RUN_ID,
        review_revision_id=REVIEW_ID,
        approval_id=APPROVAL_ID,
        authority_manifest_id="authority.v2",
        created_at="2026-07-15T12:00:00Z",
        sealed_document=document,
        dispositions=_dispositions(),
        matrix_rows=_matrix_rows(),
        expected_payload_manifest_sha256=expected_hash,
        project_root=ROOT,
    )

    with zipfile.ZipFile(io.BytesIO(bundle.zip_bytes)) as archive:
        for entry in bundle.manifest["files"]:
            payload = archive.read(entry["path"])
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


def test_export_readiness_reports_hs009_without_independent_assessment() -> None:
    document = _load_fixture("fedramp-20x-class-c-sealed.json")
    document["fedramp_20x"]["independent_assessment"] = {}
    result = evaluate_export_readiness(
        profile_id="fedramp_20x_program",
        sealed_document=document,
        project_root=ROOT,
    )
    assert "hs_009_missing_independent_assessment" in result.blockers


def test_fisma_profile_prunes_duplicate_common_artifacts() -> None:
    document = {
        "package": {"profile_id": "fisma_agency_security", "title": "Demo"},
        "system": {
            "display_name": "Demo",
            "authorization_boundary": "vpc",
            "mission_summary": "demo",
            "impact_level": "moderate",
        },
        "security_controls": {
            "AC-2": {
                "implementation_status": "implemented",
                "implementation_statement": "policy",
            }
        },
        "assessor_inputs": {"sar": {"owner": "assessor"}},
        "privacy": {"scope_notice": "External privacy review required."},
        "fisma_agency_security": {"sections": {}},
    }
    artifacts = generate_profile_artifacts(
        profile_id="fisma_agency_security",
        sealed_document=document,
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=_dispositions(),
        matrix_rows=_matrix_rows(),
        project_root=ROOT,
    )
    paths = {entry["path"] for entry in artifacts.files}

    assert "validation/schema-purity.json" not in paths
    assert "human/readiness-summary.md" not in paths
    assert "validation/export-readiness.json" not in paths
    assert "human/security-readiness.md" in paths
    assert "machine/security-readiness.json" in paths
    assert "validation/fisma-export-readiness.json" in paths
    assert "machine/assessment-matrix.json" in paths
    assert "human/assessment-matrix.md" in paths
    assert "machine/package-document.json" in paths

    matrix_md = artifacts.contents["human/assessment-matrix.md"].decode("utf-8")
    assert "# Assessment Matrix" in matrix_md
    assert "# Assessment matrix (draft)" not in matrix_md

    fisma_readiness = json.loads(artifacts.contents["validation/fisma-export-readiness.json"])
    assert "hs002_template_pack_unavailable" in fisma_readiness["readiness_blockers"]
    assert "hs002_template_pack_unavailable" in fisma_readiness["readiness_warnings"]


def test_export_readiness_rev5_requires_imported_sections() -> None:
    document = _load_fixture("fedramp-rev5-transition-sealed.json")
    document["fedramp_rev5_transition"]["ssp"] = {}
    result = evaluate_export_readiness(
        profile_id="fedramp_rev5_transition",
        sealed_document=document,
        project_root=ROOT,
    )
    assert "missing_rev5_ssp" in result.blockers
