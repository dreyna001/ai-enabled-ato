"""Typed extraction failures with stable lifecycle error codes."""

from __future__ import annotations

_EXTRACTION_ERROR_CODES = frozenset(
    {
        "package_limit_exceeded",
        "source_parse_failed",
        "source_type_mismatch",
        "unsafe_archive",
    }
)


class ExtractionError(Exception):
    """Raised on hard extraction failure; never returns partial segments."""

    def __init__(self, message: str, *, error_code: str) -> None:
        if error_code not in _EXTRACTION_ERROR_CODES:
            raise ValueError(f"unsupported extraction error code: {error_code}")
        super().__init__(message)
        self.error_code = error_code
