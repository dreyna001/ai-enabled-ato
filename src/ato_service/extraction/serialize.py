"""Map extraction outcomes to contract dictionaries."""

from __future__ import annotations

from typing import Any

from ato_service.extraction.types import ExtractionOutcome, ExtractedSegment

SCHEMA_VERSION = "1.0.0"


def segment_to_contract(segment: ExtractedSegment) -> dict[str, Any]:
    """Serialize one extracted segment for schema validation."""
    return {
        "segment_index": segment.segment_index,
        "text": segment.text,
        "locator": segment.locator,
        "extraction_method": segment.extraction_method,
        "metadata": segment.metadata,
    }


def outcome_to_contract(outcome: ExtractionOutcome) -> dict[str, Any]:
    """Serialize one extraction outcome for schema validation."""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": outcome.status,
        "detected_format": outcome.detected_format,
        "detected_media_type": outcome.detected_media_type,
        "page_count": outcome.page_count,
        "total_text_characters": outcome.total_text_characters,
        "vision_status": outcome.vision_status,
        "segments": [segment_to_contract(segment) for segment in outcome.segments],
    }
