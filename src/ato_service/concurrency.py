"""PackageRevision optimistic concurrency helpers."""

from __future__ import annotations

import re

# OpenAPI If-Match / ETag inner token: [A-Za-z0-9._:-]{1,128}
_MAX_PACKAGE_REVISION_ETAG_INNER_LENGTH = 128
_MAX_PACKAGE_REVISION_VERSION_DIGITS = _MAX_PACKAGE_REVISION_ETAG_INNER_LENGTH - 1
_PACKAGE_REVISION_ETAG_PATTERN = re.compile(
    rf'^"v([1-9][0-9]{{0,{_MAX_PACKAGE_REVISION_VERSION_DIGITS - 1}}})"$'
)


def _reject_non_positive_int_revision_version(revision_version: object) -> int:
    if isinstance(revision_version, bool) or not isinstance(revision_version, int):
        raise ValueError("revision_version must be a positive integer")
    if revision_version < 1:
        raise ValueError("revision_version must be a positive integer")
    return revision_version


class IfMatchRequiredError(Exception):
    """Raised when a required If-Match header is missing."""

    error_code = "if_match_required"


class EtagMismatchError(Exception):
    """Raised when If-Match does not match the current revision version."""

    error_code = "etag_mismatch"


def format_package_revision_etag(revision_version: int) -> str:
    """Return the quoted strong ETag for a PackageRevision."""
    validated = _reject_non_positive_int_revision_version(revision_version)
    inner_token = f"v{validated}"
    if len(inner_token) > _MAX_PACKAGE_REVISION_ETAG_INNER_LENGTH:
        raise ValueError("revision_version exceeds the supported ETag token length")
    return f'"{inner_token}"'


def parse_package_revision_etag(if_match: str) -> int:
    """Parse a strong PackageRevision ETag into revision_version."""
    if not isinstance(if_match, str) or any(character.isspace() for character in if_match):
        raise ValueError("malformed PackageRevision ETag")
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
