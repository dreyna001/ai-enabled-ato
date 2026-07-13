"""Frozen extraction contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ExtractionStatus = Literal["succeeded", "evidence_only", "vision_deferred"]
VisionStatus = Literal[
    "not_needed",
    "deferred",
    "unavailable",
    "blocked",
    "evidence_only",
]
ExtractionMethod = Literal["deterministic", "text", "vision"]


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    """Runtime-configured extraction resource budgets."""

    max_pdf_pages_per_file: int
    max_extracted_text_characters_per_file: int
    max_zip_members_per_archive: int
    max_zip_uncompressed_bytes_per_archive: int
    max_zip_decompression_ratio: int
    max_xml_depth: int
    max_xml_elements: int
    max_xml_attributes_per_element: int
    max_xml_text_node_characters: int


@dataclass(frozen=True, slots=True)
class VisionPolicy:
    """Governed vision availability for image and scanned-PDF paths."""

    vision_allowed: bool


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """Declared and detected intake metadata supplied by the caller."""

    declared_media_type: str | None
    detected_media_type: str | None
    declared_format: str | None
    artifact_kind: str | None
    filename: str | None


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    """One bounded extracted fragment with a domain ``SourceLocator``."""

    segment_index: int
    text: str
    locator: dict[str, Any]
    extraction_method: ExtractionMethod
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """Successful or deferred extraction result; hard failures raise ``ExtractionError``."""

    status: ExtractionStatus
    detected_format: str
    detected_media_type: str
    page_count: int | None
    total_text_characters: int
    vision_status: VisionStatus
    segments: tuple[ExtractedSegment, ...]
