"""UTF-8 text normalization and control-character rejection."""

from __future__ import annotations

import re

from ato_service.extraction.errors import ExtractionError

_DISALLOWED_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def decode_strict_utf8(content: bytes) -> str:
    """Decode bytes as strict UTF-8."""
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError("content is not valid UTF-8", error_code="source_parse_failed") from exc


def reject_disallowed_control_characters(text: str) -> None:
    """Reject C0 control characters except tab, LF, and CR."""
    if _DISALLOWED_CONTROL.search(text):
        raise ExtractionError(
            "text contains disallowed control characters",
            error_code="source_parse_failed",
        )


def normalize_text(content: bytes) -> str:
    """Decode and validate UTF-8 text."""
    text = decode_strict_utf8(content)
    reject_disallowed_control_characters(text)
    return text
