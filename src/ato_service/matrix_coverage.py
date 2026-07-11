"""Deterministic exact-completeness validation for matrix row identifiers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
import re

_MATRIX_COVERAGE_ERROR_CODE = "matrix_coverage_invalid"

# Matches domain.schema.json assessment_item_id / control_id contract.
_ASSESSMENT_ITEM_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9()._-]{1,127}$"
)


@dataclass(frozen=True, slots=True)
class MatrixCoverageError(Exception):
    """Raised when actual matrix rows do not exactly cover expected assessment items."""

    error_code: str
    missing_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    extra_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.error_code != _MATRIX_COVERAGE_ERROR_CODE:
            raise ValueError(
                f"error_code must be {_MATRIX_COVERAGE_ERROR_CODE!r}"
            )

    def __str__(self) -> str:
        return (
            "matrix coverage invalid: "
            f"missing={list(self.missing_ids)!r}, "
            f"duplicate={list(self.duplicate_ids)!r}, "
            f"extra={list(self.extra_ids)!r}"
        )


def require_exact_matrix_coverage(
    expected_ids: Iterable[str],
    actual_ids: Iterable[str],
) -> None:
    """Require exactly one actual row identifier per expected assessment item."""
    expected = _materialize_and_validate_ids(expected_ids)
    actual = _materialize_and_validate_ids(actual_ids)

    expected_duplicates = _sorted_duplicate_ids(expected)
    if expected_duplicates:
        raise MatrixCoverageError(
            error_code=_MATRIX_COVERAGE_ERROR_CODE,
            missing_ids=(),
            duplicate_ids=expected_duplicates,
            extra_ids=(),
        )

    actual_duplicates = _sorted_duplicate_ids(actual)
    expected_set = frozenset(expected)
    actual_counts = Counter(actual)

    missing_ids = tuple(
        sorted(
            assessment_item_id
            for assessment_item_id in expected_set
            if actual_counts[assessment_item_id] == 0
        )
    )
    extra_ids = tuple(
        sorted(
            assessment_item_id
            for assessment_item_id in actual_counts
            if assessment_item_id not in expected_set
        )
    )

    if missing_ids or actual_duplicates or extra_ids:
        raise MatrixCoverageError(
            error_code=_MATRIX_COVERAGE_ERROR_CODE,
            missing_ids=missing_ids,
            duplicate_ids=actual_duplicates,
            extra_ids=extra_ids,
        )


def _materialize_and_validate_ids(ids: Iterable[str]) -> list[str]:
    materialized = list(ids)
    return [_validate_assessment_item_id(identifier) for identifier in materialized]


def _validate_assessment_item_id(identifier: object) -> str:
    if not isinstance(identifier, str):
        raise MatrixCoverageError(
            error_code=_MATRIX_COVERAGE_ERROR_CODE,
            missing_ids=(),
            duplicate_ids=(),
            extra_ids=(),
        )
    if not identifier or identifier.strip() != identifier:
        raise MatrixCoverageError(
            error_code=_MATRIX_COVERAGE_ERROR_CODE,
            missing_ids=(),
            duplicate_ids=(),
            extra_ids=(),
        )
    if not _ASSESSMENT_ITEM_ID_PATTERN.fullmatch(identifier):
        raise MatrixCoverageError(
            error_code=_MATRIX_COVERAGE_ERROR_CODE,
            missing_ids=(),
            duplicate_ids=(),
            extra_ids=(),
        )
    return identifier


def _sorted_duplicate_ids(ids: list[str]) -> tuple[str, ...]:
    counts = Counter(ids)
    return tuple(sorted(assessment_item_id for assessment_item_id, count in counts.items() if count > 1))
