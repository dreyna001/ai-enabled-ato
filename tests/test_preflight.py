"""Tests for pre-flight readiness scoring."""

from __future__ import annotations

from ato_analysis.models.package_schema import PackageModel
from ato_analysis.validate.package_validate import validate_package
from ato_analysis.validate.preflight import compute_preflight


def test_golden_preflight_score_meets_minimum(
    golden_package: dict,
    expected_golden: dict,
    test_settings,
) -> None:
    validation = validate_package(
        golden_package,
        expected_package_id="golden_fisma_minimal",
    )
    assert validation.valid is True
    assert validation.package is not None

    outcome = compute_preflight(
        validation.package,
        validation.warnings,
        test_settings,
    )
    assert outcome.score >= expected_golden["preflight_min_score"]
    assert outcome.blocked is False
    assert outcome.metadata_complete is True
    assert outcome.controls_non_empty is True
    assert outcome.all_controls_have_evidence is True
    assert outcome.no_broken_links is True


def test_preflight_blocked_below_threshold(golden_package: dict, test_settings) -> None:
    package = PackageModel.model_validate(golden_package)
    blocked_package = package.model_copy(update={"controls": []})

    outcome = compute_preflight(blocked_package, [], test_settings)
    assert outcome.score < test_settings.preflight_block_threshold
    assert outcome.blocked is True
    assert outcome.controls_non_empty is False


def test_preflight_passes_at_threshold_boundary(test_settings) -> None:
    """Score 0.75 (three of four criteria) is above default threshold 0.6."""
    package = PackageModel.model_validate(
        {
            "package_id": "boundary_test",
            "authorization_path": "fisma_agency",
            "baseline": "NIST-SP-800-53-R5",
            "impact_level": "Moderate",
            "data_classification": "Unclassified",
            "system_name": "",
            "authorization_boundary": "Boundary text",
            "assessment_date": "2026-06-15",
            "controls": [
                {
                    "control_id": "AC-2",
                    "control_title": "Account Management",
                    "control_requirement": "Manage accounts.",
                    "implementation_statement": "Implemented.",
                    "linked_evidence_ids": ["EV-1"],
                }
            ],
            "evidence_items": [
                {
                    "evidence_id": "EV-1",
                    "title": "Policy",
                    "source_type": "policy",
                    "source_owner": "Owner",
                    "collected_at": "2026-01-01",
                    "text": "Policy text for account management.",
                }
            ],
        }
    )
    outcome = compute_preflight(package, [], test_settings)
    assert outcome.score == 0.75
    assert outcome.blocked is False
    assert outcome.metadata_complete is False
