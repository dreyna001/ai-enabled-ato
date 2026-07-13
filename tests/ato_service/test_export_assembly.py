"""Export ZIP assembly and manifest binding tests."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile

from ato_service.export_assembly import AI_DISCLOSURE, assemble_export_bundle, manifest_sha256
from ato_service.profile_artifacts import generate_profile_artifacts

APPROVAL_ID = "77777777-7777-4777-8777-777777777777"
REVIEW_ID = "66666666-6666-4666-8666-666666666666"
RUN_ID = "33333333-3333-4333-8333-333333333333"
REVISION_ID = "11111111-1111-4111-8111-111111111111"
SYSTEM_ID = "55555555-5555-4555-8555-555555555555"


def _sealed_document() -> dict:
    return {
        "package": {
            "profile_id": "fisma_agency_security",
            "title": "Demo",
            "prepared_for": "Agency",
            "reporting_period": None,
        },
        "system": {
            "display_name": "Demo",
            "authorization_boundary": "vpc",
            "mission_summary": "demo",
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
            "AC-2": {
                "implementation_status": "implemented",
                "implementation_statement": "policy",
                "responsible_parties": [],
                "evidence_links": [],
            }
        },
        "evidence": {},
        "findings": {},
        "poam_candidates": {},
        "assessor_inputs": {"sar": {"owner": "assessor"}},
        "privacy": {
            "artifacts_present": True,
            "scope_notice": "External privacy review required.",
        },
        "fedramp_20x": None,
        "fedramp_rev5_transition": None,
        "fisma_agency_security": {"sections": {}},
        "extensions": {},
    }


def test_assemble_export_bundle_reproduces_approved_manifest_hash() -> None:
    dispositions = [
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
    matrix_rows = [
        {
            "matrix_row_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "assessment_item_id": "AC-2",
            "model_proposed_status": "insufficient_evidence",
            "system_status": "insufficient_evidence",
            "finding_summary": "No evidence",
            "citations": [],
        }
    ]
    artifacts = generate_profile_artifacts(
        profile_id="fisma_agency_security",
        sealed_document=_sealed_document(),
        review_revision_id=uuid.UUID(REVIEW_ID),
        run_id=uuid.UUID(RUN_ID),
        dispositions=dispositions,
        matrix_rows=matrix_rows,
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
        sealed_document=_sealed_document(),
        dispositions=dispositions,
        matrix_rows=matrix_rows,
        expected_payload_manifest_sha256=expected_hash,
    )

    assert bundle.manifest["ai_disclosure"] == AI_DISCLOSURE
    assert bundle.manifest["approval_id"] == APPROVAL_ID

    with zipfile.ZipFile(io.BytesIO(bundle.zip_bytes)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "README.txt" in names
        assert "machine/assessment-matrix.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["export_id"] == APPROVAL_ID
        for entry in manifest["files"]:
            payload = archive.read(entry["path"])
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
