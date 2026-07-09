"""Tests for deterministic package validation."""

from __future__ import annotations

import pytest

from ato_analysis.models.package_schema import PackageModel
from ato_analysis.validate.package_validate import validate_package


def test_golden_fixture_validates(golden_package: dict) -> None:
    result = validate_package(
        golden_package,
        expected_package_id="golden_fisma_minimal",
    )
    assert result.valid is True
    assert result.errors == []
    assert result.package is not None


def test_malformed_missing_control_id_fails(load_fixture) -> None:
    data = load_fixture("malformed_missing_control_id")
    result = validate_package(
        data,
        expected_package_id="malformed_missing_control_id",
    )
    assert result.valid is False
    assert result.package is None
    assert any("control_id" in error.lower() for error in result.errors)


def test_malformed_broken_evidence_link_fails(load_fixture) -> None:
    data = load_fixture("malformed_broken_evidence_link")
    result = validate_package(
        data,
        expected_package_id="malformed_broken_evidence_link",
    )
    assert result.valid is False
    assert result.package is None
    assert any("EV-DOES-NOT-EXIST" in error for error in result.errors)


def test_stale_evidence_detected(golden_package: dict, expected_golden: dict) -> None:
    result = validate_package(
        golden_package,
        expected_package_id="golden_fisma_minimal",
    )
    assert result.valid is True
    assert set(result.stale_evidence_ids) == set(expected_golden["stale_evidence_ids"])
    assert any("Stale evidence" in warning for warning in result.warnings)


def test_orphan_evidence_warning(golden_package: dict) -> None:
    result = validate_package(
        golden_package,
        expected_package_id="golden_fisma_minimal",
    )
    assert result.valid is True
    assert any("EV-ORPHAN-DOC" in warning for warning in result.warnings)
    assert any("Orphan evidence" in warning for warning in result.warnings)


def test_package_id_mismatch_fails(golden_package: dict) -> None:
    result = validate_package(
        golden_package,
        expected_package_id="wrong_package_id",
    )
    assert result.valid is False
    assert any("package_id" in error for error in result.errors)
