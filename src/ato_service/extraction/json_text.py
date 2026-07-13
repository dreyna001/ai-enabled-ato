"""Generic JSON and plain-text extraction."""

from __future__ import annotations

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.safety_json import iter_json_leaves, leaf_text, parse_json_strict
from ato_service.extraction.safety_text import normalize_text
from ato_service.extraction.types import ExtractedSegment, ExtractionLimits


def extract_json(content: bytes, *, limits: ExtractionLimits) -> list[ExtractedSegment]:
    """Extract JSON leaves with json_pointer locators."""
    text = normalize_text(content)
    _enforce_text_budget(text, limits=limits)
    payload = parse_json_strict(text)
    leaves = iter_json_leaves(payload)
    segments: list[ExtractedSegment] = []
    for index, (pointer, value) in enumerate(leaves, start=1):
        segment_text = leaf_text(value)
        if not segment_text:
            continue
        segments.append(
            ExtractedSegment(
                segment_index=index,
                text=segment_text,
                locator={"kind": "json_pointer", "json_pointer": pointer or "/"},
                extraction_method="deterministic",
            )
        )
    if not segments:
        raise ExtractionError("json contains no extractable leaves", error_code="source_parse_failed")
    return segments


def extract_plain_text(
    content: bytes,
    *,
    limits: ExtractionLimits,
    detected_format: str,
) -> list[ExtractedSegment]:
    """Extract one UTF-8 text or Markdown document."""
    text = normalize_text(content)
    _enforce_text_budget(text, limits=limits)
    if not text.strip():
        raise ExtractionError("text document is empty", error_code="source_parse_failed")
    return [
        ExtractedSegment(
            segment_index=1,
            text=text,
            locator={"kind": "text_offsets", "start_offset": 0, "end_offset": len(text)},
            extraction_method="text",
            metadata={"format": detected_format},
        )
    ]


def _enforce_text_budget(text: str, *, limits: ExtractionLimits) -> None:
    if len(text) > limits.max_extracted_text_characters_per_file:
        raise ExtractionError(
            "extracted text exceeds configured character limit",
            error_code="package_limit_exceeded",
        )
