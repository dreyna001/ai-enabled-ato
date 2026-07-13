"""Structured scanner and OSCAL format extractors."""

from __future__ import annotations

import defusedxml.ElementTree as ET

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.json_text import extract_json, extract_plain_text
from ato_service.extraction.safety_json import parse_json_strict
from ato_service.extraction.safety_text import normalize_text
from ato_service.extraction.safety_xml import collect_element_text, element_path, local_name, parse_xml_bounded
from ato_service.extraction.types import ExtractedSegment, ExtractionLimits

_OSCAL_JSON_ROOTS = frozenset(
    {
        "assessment-plan",
        "assessment-results",
        "catalog",
        "component-definition",
        "plan-of-action-and-milestones",
        "profile",
        "system-security-plan",
    }
)
_OSCAL_XML_ROOTS = frozenset(
    {
        "assessment-plan",
        "assessment-results",
        "catalog",
        "component-definition",
        "plan-of-action-and-milestones",
        "profile",
        "system-security-plan",
    }
)


def extract_structured_json(
    content: bytes,
    *,
    limits: ExtractionLimits,
    detected_format: str,
) -> list[ExtractedSegment]:
    """Extract known JSON structured exports."""
    return extract_json(content, limits=limits)


def extract_sarif(content: bytes, *, limits: ExtractionLimits) -> list[ExtractedSegment]:
    """Extract SARIF runs and results as JSON-pointer segments."""
    text = normalize_text(content)
    payload = parse_json_strict(text)
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("runs"), list)
        or not isinstance(payload.get("version"), str)
    ):
        raise ExtractionError(
            "sarif payload has an invalid top-level signature",
            error_code="source_type_mismatch",
        )
    segments = extract_json(content, limits=limits)
    return [
        ExtractedSegment(
            segment_index=segment.segment_index,
            text=segment.text,
            locator=segment.locator,
            extraction_method=segment.extraction_method,
            metadata={"format": "sarif_json"},
        )
        for segment in segments
    ]


def extract_oscal_json(content: bytes, *, limits: ExtractionLimits) -> list[ExtractedSegment]:
    """Extract OSCAL JSON leaves."""
    payload = parse_json_strict(normalize_text(content))
    if not isinstance(payload, dict) or not (_OSCAL_JSON_ROOTS & payload.keys()):
        raise ExtractionError(
            "oscal json payload has an invalid top-level signature",
            error_code="source_type_mismatch",
        )
    segments = extract_json(content, limits=limits)
    return [
        ExtractedSegment(
            segment_index=segment.segment_index,
            text=segment.text,
            locator=segment.locator,
            extraction_method=segment.extraction_method,
            metadata={"format": "oscal_json"},
        )
        for segment in segments
    ]


def extract_stig_json(content: bytes, *, limits: ExtractionLimits) -> list[ExtractedSegment]:
    """Extract STIG JSON leaves."""
    payload = parse_json_strict(normalize_text(content))
    if (
        not isinstance(payload, dict)
        or not {"stig", "benchmark", "Benchmark"} & payload.keys()
    ):
        raise ExtractionError(
            "stig json payload has an invalid top-level signature",
            error_code="source_type_mismatch",
        )
    segments = extract_json(content, limits=limits)
    return [
        ExtractedSegment(
            segment_index=segment.segment_index,
            text=segment.text,
            locator=segment.locator,
            extraction_method=segment.extraction_method,
            metadata={"format": "stig_json"},
        )
        for segment in segments
    ]


def extract_xml_structured(
    content: bytes,
    *,
    limits: ExtractionLimits,
    detected_format: str,
) -> list[ExtractedSegment]:
    """Extract logical XML elements for OSCAL, Nessus, STIG, or generic XML."""
    root = parse_xml_bounded(content, limits=limits)
    _validate_xml_signature(root, detected_format=detected_format)
    interesting_tags = _interesting_tags_for_format(detected_format)
    segments: list[ExtractedSegment] = []
    index = 0
    for element in root.iter():
        tag = local_name(element.tag)
        if interesting_tags and tag not in interesting_tags:
            continue
        text = collect_element_text(element).strip()
        if not text:
            continue
        index += 1
        segments.append(
            ExtractedSegment(
                segment_index=index,
                text=text,
                locator={"kind": "xml_path", "xml_path": element_path(element, root=root)},
                extraction_method="deterministic",
                metadata={"format": detected_format, "element": tag},
            )
        )
    if not segments:
        if detected_format != "xml":
            raise ExtractionError(
                f"{detected_format} contains no extractable structured elements",
                error_code="source_parse_failed",
            )
        text = normalize_text(content)
        return extract_plain_text(content, limits=limits, detected_format=detected_format)
    return segments


def _validate_xml_signature(
    root: ET.Element,
    *,
    detected_format: str,
) -> None:
    root_name = local_name(root.tag)
    if detected_format == "nessus_xml" and root_name not in {
        "NessusClientData",
        "NessusClientData_v2",
    }:
        raise ExtractionError(
            "nessus xml has an invalid root element",
            error_code="source_type_mismatch",
        )
    if detected_format == "stig_xml" and root_name != "Benchmark":
        raise ExtractionError(
            "stig xml has an invalid root element",
            error_code="source_type_mismatch",
        )
    if detected_format == "oscal_xml" and root_name not in _OSCAL_XML_ROOTS:
        raise ExtractionError(
            "oscal xml has an invalid root element",
            error_code="source_type_mismatch",
        )


def _interesting_tags_for_format(detected_format: str) -> frozenset[str] | None:
    if detected_format == "nessus_xml":
        return frozenset({"ReportItem", "ReportHost", "Preference"})
    if detected_format == "stig_xml":
        return frozenset({"Group", "Rule", "title", "description"})
    if detected_format == "oscal_xml":
        return frozenset(
            {
                "control",
                "implemented-requirement",
                "prop",
                "description",
                "title",
            }
        )
    return None
