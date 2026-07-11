"""Tests for PackageRevision optimistic concurrency helpers."""

from __future__ import annotations

import pytest

from ato_service.concurrency import (
    EtagMismatchError,
    IfMatchRequiredError,
    assert_if_match,
    format_package_revision_etag,
    parse_package_revision_etag,
)


def test_format_and_parse_package_revision_etag() -> None:
    assert format_package_revision_etag(1) == '"v1"'
    assert format_package_revision_etag(42) == '"v42"'
    assert parse_package_revision_etag('"v42"') == 42


@pytest.mark.parametrize("revision_version", [0, -1, True, 1.5, "1"])
def test_format_package_revision_etag_rejects_non_positive(
    revision_version: object,
) -> None:
    with pytest.raises(ValueError):
        format_package_revision_etag(revision_version)  # type: ignore[arg-type]


def test_format_package_revision_etag_rejects_excessive_token_length() -> None:
    excessive_version = int("9" * 128)
    with pytest.raises(ValueError, match="ETag token length"):
        format_package_revision_etag(excessive_version)


@pytest.mark.parametrize(
    "if_match",
    [
        'W/"v1"',
        '"v1", "v2"',
        "*",
        '"v0"',
        '"v01"',
        '"version-1"',
        "v1",
        '"v1',
        'v1"',
        "",
        '"v"',
        '"v-1"',
        '"v1 "',
        ' "v1"',
        '"v' + "9" * 128 + '"',
    ],
)
def test_parse_package_revision_etag_rejects_weak_or_malformed(if_match: str) -> None:
    with pytest.raises(ValueError):
        parse_package_revision_etag(if_match)


def test_assert_if_match_requires_header() -> None:
    with pytest.raises(IfMatchRequiredError) as exc_info:
        assert_if_match(None, 3)

    assert exc_info.value.error_code == "if_match_required"


@pytest.mark.parametrize(
    "if_match",
    ['W/"v3"', "*", '"v0"', '"v99"'],
)
def test_assert_if_match_reports_mismatch_or_rejects_invalid(if_match: str) -> None:
    with pytest.raises(EtagMismatchError) as exc_info:
        assert_if_match(if_match, 3)

    assert exc_info.value.error_code == "etag_mismatch"


def test_assert_if_match_accepts_current_version() -> None:
    assert_if_match('"v3"', 3)
