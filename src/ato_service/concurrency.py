"""PackageRevision optimistic concurrency helpers."""

from __future__ import annotations

import re

_PACKAGE_REVISION_ETAG_PATTERN = re.compile(r'^"v([1-9][0-9]*)"$')


class IfMatchRequiredError(Exception):
    """Raised when a required If-Match header is missing."""

    error_code = "if_match_required"


class EtagMismatchError(Exception):
    """Raised when If-Match does not match the current revision version."""

    error_code = "etag_mismatch"


def format_package_revision_etag(revision_version: int) -> str:
    """Return the quoted strong ETag for a PackageRevision."""
    if revision_version < 1:
        raise ValueError("revision_version must be a positive integer")
    return f'"v{revision_version}"'


def parse_package_revision_etag(if_match: str) -> int:
    """Parse a strong PackageRevision ETag into revision_version."""
    if if_match.startswith("W/") or if_match == "*":
        raise ValueError("weak and wildcard If-Match values are rejected")
    match = _PACKAGE_REVISION_ETAG_PATTERN.fullmatch(if_match)
    if match is None:
        raise ValueError("malformed PackageRevision ETag")
    return int(match.group(1))


def assert_if_match(if_match_header: str | None, current_revision_version: int) -> None:
    """Validate If-Match against the current PackageRevision revision_version."""
    if if_match_header is None:
        raise IfMatchRequiredError()
    try:
        provided_version = parse_package_revision_etag(if_match_header)
    except ValueError:
        raise EtagMismatchError() from None
    if provided_version != current_revision_version:
        raise EtagMismatchError()
