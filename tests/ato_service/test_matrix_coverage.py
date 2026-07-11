"""Tests for deterministic matrix row exact-completeness validation."""

from __future__ import annotations

import re

import pytest

from ato_service.matrix_coverage import (
    MatrixCoverageError,
    require_exact_matrix_coverage,
)


SYNTHETIC_EXPECTED = ("AC-1", "AC-2", "IA-5")
DOMAIN_ASSESSMENT_ITEM_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9()._-]{1,127}$"
)


def test_exact_match_with_different_ordering() -> None:
    assert (
        require_exact_matrix_coverage(
            ["AC-2", "IA-5", "AC-1"],
            ["IA-5", "AC-1", "AC-2"],
        )
        is None
    )


def test_exact_match_accepts_generators() -> None:
    def expected() -> str:
        yield "AC-1"
        yield "AC-2"

    def actual() -> str:
        yield "AC-2"
        yield "AC-1"

    assert require_exact_matrix_coverage(expected(), actual()) is None


def test_missing_rows_reports_sorted_missing_ids() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(SYNTHETIC_EXPECTED, ["AC-1", "AC-2"])

    error = exc_info.value
    assert error.error_code == "matrix_coverage_invalid"
    assert error.missing_ids == ("IA-5",)
    assert error.duplicate_ids == ()
    assert error.extra_ids == ()


def test_duplicate_actual_rows_reports_sorted_duplicate_ids() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(
            SYNTHETIC_EXPECTED,
            ["AC-1", "AC-2", "IA-5", "AC-1"],
        )

    error = exc_info.value
    assert error.error_code == "matrix_coverage_invalid"
    assert error.missing_ids == ()
    assert error.duplicate_ids == ("AC-1",)
    assert error.extra_ids == ()


def test_extra_rows_reports_sorted_extra_ids() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(
            SYNTHETIC_EXPECTED,
            ["AC-1", "AC-2", "IA-5", "SC-7"],
        )

    error = exc_info.value
    assert error.error_code == "matrix_coverage_invalid"
    assert error.missing_ids == ()
    assert error.duplicate_ids == ()
    assert error.extra_ids == ("SC-7",)


def test_simultaneous_missing_duplicate_and_extra_mismatch() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(
            ["AC-1", "AC-2", "IA-5"],
            ["AC-1", "AC-1", "SC-7", "SC-12"],
        )

    error = exc_info.value
    assert error.error_code == "matrix_coverage_invalid"
    assert error.missing_ids == ("AC-2", "IA-5")
    assert error.duplicate_ids == ("AC-1",)
    assert error.extra_ids == ("SC-12", "SC-7")


def test_duplicate_expected_inventory_is_rejected() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(
            ["AC-1", "AC-2", "AC-2"],
            ["AC-1", "AC-2"],
        )

    error = exc_info.value
    assert error.error_code == "matrix_coverage_invalid"
    assert error.missing_ids == ()
    assert error.duplicate_ids == ("AC-2",)
    assert error.extra_ids == ()


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        " ",
        " AC-1",
        "AC-1 ",
        "A",
        ".AC-1",
        "AC 1",
        "AC-1\n",
        42,
        None,
    ],
)
def test_malformed_or_empty_identifiers_raise(identifier: object) -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(["AC-1"], [identifier])  # type: ignore[list-item]

    error = exc_info.value
    assert error.error_code == "matrix_coverage_invalid"
    assert error.missing_ids == ()
    assert error.duplicate_ids == ()
    assert error.extra_ids == ()


def test_malformed_expected_identifier_raises_before_actual_validation() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage([" AC-1"], ["AC-1"])

    assert exc_info.value.error_code == "matrix_coverage_invalid"


def test_diagnostics_remain_deterministically_sorted() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(
            ["ZZ-9", "AA-1", "MM-3"],
            ["BB-2", "AA-1", "BB-2", "CC-4"],
        )

    error = exc_info.value
    assert error.missing_ids == ("MM-3", "ZZ-9")
    assert error.duplicate_ids == ("BB-2",)
    assert error.extra_ids == ("BB-2", "CC-4")


def test_empty_expected_and_empty_actual_succeed() -> None:
    """Published invariant: zero expected items require zero matrix rows."""
    assert require_exact_matrix_coverage([], []) is None
    assert require_exact_matrix_coverage((), ()) is None


def test_empty_expected_with_actual_rows_reports_extra_only() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage([], ["AC-1"])

    error = exc_info.value
    assert error.missing_ids == ()
    assert error.duplicate_ids == ()
    assert error.extra_ids == ("AC-1",)


def test_empty_actual_with_expected_items_reports_missing_only() -> None:
    with pytest.raises(MatrixCoverageError) as exc_info:
        require_exact_matrix_coverage(["AC-1", "AC-2"], [])

    error = exc_info.value
    assert error.missing_ids == ("AC-1", "AC-2")
    assert error.duplicate_ids == ()
    assert error.extra_ids == ()


def test_synthetic_fixture_ids_match_domain_contract_pattern() -> None:
    for identifier in (*SYNTHETIC_EXPECTED, "SC-7", "SC-12", "ZZ-9"):
        assert DOMAIN_ASSESSMENT_ITEM_ID_PATTERN.fullmatch(identifier)
