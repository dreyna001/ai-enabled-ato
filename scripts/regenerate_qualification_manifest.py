#!/usr/bin/env python3
"""Regenerate data/qualification/manifest.json digests from fixture metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = ROOT / "data" / "qualification"

FIXTURES = [
    {
        "fixture_id": "fisma.sealed-draft",
        "relative_path": "profiles/fisma-agency-security/sealed-draft.json",
        "profile_id": "fisma_agency_security",
        "split": "qualification",
        "purpose": "Minimal sealed draft for agency FISMA deterministic E2E.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["deterministic_e2e_fixture_present"],
        "blocked_claims": ["agency_field_parity", "customer_ready_fisma_export"],
    },
    {
        "fixture_id": "fisma.analysis-profile",
        "relative_path": "profiles/fisma-agency-security/analysis-profile.minimal.json",
        "profile_id": "fisma_agency_security",
        "split": "qualification",
        "purpose": "Draft analysis profile for agency FISMA deterministic E2E.",
        "expected_behavior": "deterministic_only",
        "allowed_claims": ["analysis_profile_schema_valid"],
        "blocked_claims": ["official_qualification_claim"],
    },
    {
        "fixture_id": "fisma.security-plan-md",
        "relative_path": "profiles/fisma-agency-security/mixed-format/security-plan-excerpt.md",
        "profile_id": "fisma_agency_security",
        "split": "qualification",
        "purpose": "Markdown excerpt for mixed-format agency FISMA intake.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["mixed_format_intake_fixture"],
        "blocked_claims": ["customer_ready_fisma_export"],
    },
    {
        "fixture_id": "fisma.control-matrix-csv",
        "relative_path": "profiles/fisma-agency-security/mixed-format/control-matrix.csv",
        "profile_id": "fisma_agency_security",
        "split": "qualification",
        "purpose": "CSV control matrix for mixed-format agency FISMA intake.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["mixed_format_intake_fixture"],
        "blocked_claims": ["customer_ready_fisma_export"],
    },
    {
        "fixture_id": "fisma.policy-txt",
        "relative_path": "profiles/fisma-agency-security/mixed-format/policy-excerpt.txt",
        "profile_id": "fisma_agency_security",
        "split": "qualification",
        "purpose": "Plain-text policy excerpt for mixed-format agency FISMA intake.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["mixed_format_intake_fixture"],
        "blocked_claims": ["customer_ready_fisma_export"],
    },
    {
        "fixture_id": "fedramp20x.sealed-draft",
        "relative_path": "profiles/fedramp-20x-class-c/sealed-draft.json",
        "profile_id": "fedramp_20x_program",
        "split": "qualification",
        "purpose": "Minimal sealed draft for FedRAMP 20x Class C deterministic E2E.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["deterministic_e2e_fixture_present"],
        "blocked_claims": ["official_schema_qualification_claim"],
    },
    {
        "fixture_id": "fedramp20x.analysis-profile",
        "relative_path": "profiles/fedramp-20x-class-c/analysis-profile.minimal.json",
        "profile_id": "fedramp_20x_program",
        "split": "qualification",
        "purpose": "Draft analysis profile for FedRAMP 20x Class C deterministic E2E.",
        "expected_behavior": "deterministic_only",
        "allowed_claims": ["analysis_profile_schema_valid"],
        "blocked_claims": ["official_qualification_claim"],
    },
    {
        "fixture_id": "fedramp20x.cpo-synthetic",
        "relative_path": "profiles/fedramp-20x-class-c/cpo-synthetic.json",
        "profile_id": "fedramp_20x_program",
        "split": "qualification",
        "purpose": "Synthetic CPO-shaped fixture for structural validation only.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["synthetic_official_schema_fixture"],
        "blocked_claims": ["official_schema_qualification_claim"],
    },
    {
        "fixture_id": "fedramp20x.sdr-synthetic",
        "relative_path": "profiles/fedramp-20x-class-c/sdr-synthetic.json",
        "profile_id": "fedramp_20x_program",
        "split": "qualification",
        "purpose": "Synthetic SDR-shaped fixture for structural validation only.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["synthetic_official_schema_fixture"],
        "blocked_claims": ["official_schema_qualification_claim"],
    },
    {
        "fixture_id": "fedramp20x.ocr-synthetic",
        "relative_path": "profiles/fedramp-20x-class-c/ocr-synthetic.json",
        "profile_id": "fedramp_20x_program",
        "split": "qualification",
        "purpose": "Synthetic OCR-shaped fixture for structural validation only.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["synthetic_official_schema_fixture"],
        "blocked_claims": ["official_schema_qualification_claim"],
    },
    {
        "fixture_id": "rev5.sealed-draft",
        "relative_path": "profiles/fedramp-rev5-transition/sealed-draft.json",
        "profile_id": "fedramp_rev5_transition",
        "split": "qualification",
        "purpose": "Minimal sealed draft for FedRAMP Rev.5 transition deterministic E2E.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["deterministic_e2e_fixture_present"],
        "blocked_claims": ["official_schema_qualification_claim"],
    },
    {
        "fixture_id": "rev5.analysis-profile",
        "relative_path": "profiles/fedramp-rev5-transition/analysis-profile.minimal.json",
        "profile_id": "fedramp_rev5_transition",
        "split": "qualification",
        "purpose": "Draft analysis profile for FedRAMP Rev.5 transition deterministic E2E.",
        "expected_behavior": "deterministic_only",
        "allowed_claims": ["analysis_profile_schema_valid"],
        "blocked_claims": ["official_qualification_claim"],
    },
    {
        "fixture_id": "rev5.ssp-import",
        "relative_path": "profiles/fedramp-rev5-transition/ssp-import-excerpt.json",
        "profile_id": "fedramp_rev5_transition",
        "split": "qualification",
        "purpose": "Imported SSP excerpt for transition gap analysis fixtures.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["transition_import_fixture"],
        "blocked_claims": ["official_schema_qualification_claim"],
    },
    {
        "fixture_id": "rev5.oscal-component",
        "relative_path": "profiles/fedramp-rev5-transition/oscal-component-minimal.json",
        "profile_id": "fedramp_rev5_transition",
        "split": "qualification",
        "purpose": "Minimal OSCAL-shaped component for transition structural validation.",
        "expected_behavior": "schema_valid",
        "allowed_claims": ["transition_import_fixture"],
        "blocked_claims": ["official_schema_qualification_claim"],
    },
    {
        "fixture_id": "assessor.sar-excerpt",
        "relative_path": "assessor-import/sar-excerpt.json",
        "profile_id": None,
        "split": "qualification",
        "purpose": "Synthetic assessor SAR excerpt for import-only tests.",
        "expected_behavior": "import_only_assessor",
        "allowed_claims": ["assessor_import_fixture"],
        "blocked_claims": ["assessor_conclusion_claim"],
    },
    {
        "fixture_id": "assessor.independent-assessment",
        "relative_path": "assessor-import/independent-assessment.json",
        "profile_id": "fedramp_20x_program",
        "split": "qualification",
        "purpose": "Synthetic independent assessment import for Class C readiness fixtures.",
        "expected_behavior": "import_only_assessor",
        "allowed_claims": ["assessor_import_fixture"],
        "blocked_claims": ["complete_class_c_readiness_claim"],
    },
    {
        "fixture_id": "hostile.nessus-xxe",
        "relative_path": "hostile-inputs/nessus-xxe.xml",
        "profile_id": None,
        "split": "hostile",
        "purpose": "XXE attempt in scanner export XML for parser regression.",
        "expected_behavior": "reject_xxe",
        "allowed_claims": ["hostile_parser_regression"],
        "blocked_claims": ["production_scanner_operational_claim"],
    },
    {
        "fixture_id": "hostile.prompt-injection",
        "relative_path": "hostile-inputs/prompt-injection-fixtures.json",
        "profile_id": None,
        "split": "hostile",
        "purpose": "Prompt injection cases for refusal regression.",
        "expected_behavior": "refuse_injection",
        "allowed_claims": ["refusal_regression_fixture"],
        "blocked_claims": ["ai_qualification_claim"],
    },
    {
        "fixture_id": "hostile.malformed-json",
        "relative_path": "hostile-inputs/malformed-json.txt",
        "profile_id": None,
        "split": "hostile",
        "purpose": "Malformed JSON text for intake rejection regression.",
        "expected_behavior": "parse_reject",
        "allowed_claims": ["hostile_parser_regression"],
        "blocked_claims": ["production_customer_extraction_claim"],
    },
    {
        "fixture_id": "scenario.duplicate-artifact",
        "relative_path": "scenarios/duplicate-artifact-descriptor.json",
        "profile_id": None,
        "split": "scenario",
        "purpose": "Duplicate artifact digest scenario descriptor.",
        "expected_behavior": "duplicate_detected",
        "allowed_claims": ["duplicate_detection_scenario"],
        "blocked_claims": ["release_complete_claim"],
    },
    {
        "fixture_id": "scenario.idempotency-replay",
        "relative_path": "scenarios/idempotency-replay-descriptor.json",
        "profile_id": None,
        "split": "scenario",
        "purpose": "Idempotency replay scenario descriptor.",
        "expected_behavior": "idempotent_replay",
        "allowed_claims": ["idempotency_replay_scenario"],
        "blocked_claims": ["release_complete_claim"],
    },
    {
        "fixture_id": "scenario.job-lease-recovery",
        "relative_path": "scenarios/job-lease-recovery-descriptor.json",
        "profile_id": None,
        "split": "scenario",
        "purpose": "Expired job lease recovery scenario descriptor.",
        "expected_behavior": "lease_recovery",
        "allowed_claims": ["lease_recovery_scenario"],
        "blocked_claims": ["release_complete_claim"],
    },
    {
        "fixture_id": "scenario.worker-crash",
        "relative_path": "scenarios/worker-crash-descriptor.json",
        "profile_id": None,
        "split": "scenario",
        "purpose": "Worker crash mid-step recovery scenario descriptor.",
        "expected_behavior": "crash_safe_resume",
        "allowed_claims": ["crash_recovery_scenario"],
        "blocked_claims": ["release_complete_claim"],
    },
]


def _digest(path: Path) -> tuple[str, int]:
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest(), len(content)


def main() -> None:
    entries = []
    for spec in FIXTURES:
        path = CORPUS_ROOT / spec["relative_path"]
        sha256, size_bytes = _digest(path)
        entries.append(
            {
                "fixture_id": spec["fixture_id"],
                "relative_path": spec["relative_path"],
                "sha256": sha256,
                "size_bytes": size_bytes,
                "profile_id": spec["profile_id"],
                "split": spec["split"],
                "purpose": spec["purpose"],
                "expected_behavior": spec["expected_behavior"],
                "claim_metadata": {
                    "closes_hard_stops": False,
                    "allowed_claims": spec["allowed_claims"],
                    "blocked_claims": spec["blocked_claims"],
                },
            }
        )

    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": "ato-qualification-corpus-2026-07-14",
        "status": "draft",
        "created_at": "2026-07-14T16:00:00Z",
        "corpus_root": "data/qualification",
        "hard_stop_notice": (
            "This manifest seals synthetic qualification fixtures only. "
            "It does not close HS-001 through HS-009 or authorize production, "
            "AI qualification, official schema, or customer-ready release claims."
        ),
        "hard_stops_governed": [
            "HS-001",
            "HS-002",
            "HS-003",
            "HS-004",
            "HS-005",
            "HS-006",
            "HS-007",
            "HS-008",
            "HS-009",
        ],
        "fixtures": entries,
    }
    output = CORPUS_ROOT / "manifest.json"
    output.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {output} ({len(entries)} fixtures)")


if __name__ == "__main__":
    main()
