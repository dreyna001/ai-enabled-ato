"""Deterministic assertions against golden fixture and expected.json."""

from __future__ import annotations

from ato_analysis.models.package_schema import PackageModel
from ato_analysis.validate.package_validate import validate_package
from ato_analysis.validate.preflight import compute_preflight


def test_expected_json_matches_golden_package(
    golden_package: dict,
    expected_golden: dict,
    test_settings,
) -> None:
    assert golden_package["package_id"] == expected_golden["package_id"]

    validation = validate_package(
        golden_package,
        expected_package_id=expected_golden["package_id"],
    )
    assert validation.valid is True
    assert validation.package is not None

    assert set(validation.stale_evidence_ids) == set(expected_golden["stale_evidence_ids"])

    package = validation.package
    control_ids = {control.control_id for control in package.controls}
    assert control_ids == set(expected_golden["controls_requiring_citations"])

    preflight = compute_preflight(package, validation.warnings, test_settings)
    assert preflight.score >= expected_golden["preflight_min_score"]


def test_golden_has_required_control_count(golden_model: PackageModel) -> None:
    assert len(golden_model.controls) >= 5
    assert len(golden_model.evidence_items) >= 8


def test_golden_orphan_evidence_present(golden_model: PackageModel) -> None:
    linked: set[str] = set()
    for control in golden_model.controls:
        linked.update(control.linked_evidence_ids)
    orphan_ids = [
        item.evidence_id
        for item in golden_model.evidence_items
        if item.evidence_id not in linked
    ]
    assert "EV-ORPHAN-DOC" in orphan_ids


def test_integration_expectations_structure(expected_golden: dict) -> None:
    expectations = expected_golden["integration_expectations"]
    required_controls = ["AC-2", "AU-6", "CM-6", "IR-4", "RA-5"]
    for control_id in required_controls:
        assert control_id in expectations
        assert "sufficiency_status_in" in expectations[control_id]
        assert len(expectations[control_id]["sufficiency_status_in"]) >= 1
