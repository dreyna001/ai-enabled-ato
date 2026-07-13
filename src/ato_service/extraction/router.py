"""Public pure extraction router."""

from __future__ import annotations

from ato_service.extraction.detect import detect_format, media_type_for_format
from ato_service.extraction.docx import extract_docx
from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.images import extract_image
from ato_service.extraction.json_text import extract_json, extract_plain_text
from ato_service.extraction.pdf import extract_pdf
from ato_service.extraction.safety_svg import sanitize_svg
from ato_service.extraction.safety_zip import open_safe_zip
from ato_service.extraction.structured import (
    extract_oscal_json,
    extract_sarif,
    extract_stig_json,
    extract_xml_structured,
)
from ato_service.extraction.types import (
    ExtractionContext,
    ExtractionLimits,
    ExtractionOutcome,
    ExtractedSegment,
    VisionPolicy,
)
from ato_service.extraction.xlsx import extract_xlsx


def extract_content(
    *,
    content_bytes: bytes,
    sha256: str,
    context: ExtractionContext,
    limits: ExtractionLimits,
    vision_policy: VisionPolicy,
) -> ExtractionOutcome:
    """Extract bounded segments from verified bytes without side effects."""
    del sha256  # reserved for later provenance wiring in Diff 3
    if not content_bytes:
        raise ExtractionError("content is empty", error_code="source_parse_failed")

    try:
        detected_format = detect_format(
            content_bytes,
            declared_media_type=context.declared_media_type,
            declared_format=context.declared_format,
            filename=context.filename,
        )
    except ValueError as exc:
        raise ExtractionError("unsupported content format", error_code="source_type_mismatch") from exc

    if context.declared_format and not _formats_compatible(
        context.declared_format,
        detected_format,
    ):
        raise ExtractionError(
            "declared format does not match detected content",
            error_code="source_type_mismatch",
        )

    detected_media_type = media_type_for_format(detected_format)
    if context.declared_media_type and context.declared_media_type != detected_media_type:
        if not _media_types_compatible(context.declared_media_type, detected_media_type):
            raise ExtractionError(
                "declared media type does not match detected content",
                error_code="source_type_mismatch",
            )

    if detected_format in {"png", "jpeg", "webp"}:
        return extract_image(
            content_bytes,
            limits=limits,
            vision_policy=vision_policy,
            detected_format=detected_format,
        )

    if detected_format == "pdf":
        return extract_pdf(content_bytes, limits=limits, vision_policy=vision_policy)

    if detected_format == "svg":
        return _extract_svg(content_bytes, limits=limits, vision_policy=vision_policy)

    if detected_format == "docx":
        segments = extract_docx(content_bytes, limits=limits)
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    if detected_format == "xlsx":
        segments = extract_xlsx(content_bytes, limits=limits)
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    if detected_format == "zip":
        open_safe_zip(content_bytes, limits=limits, office_container=False)
        raise ExtractionError(
            "zip archives are handled at upload boundary only",
            error_code="source_type_mismatch",
        )

    if detected_format == "json":
        segments = extract_json(content_bytes, limits=limits)
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    if detected_format in {"text", "markdown"}:
        segments = extract_plain_text(
            content_bytes,
            limits=limits,
            detected_format=detected_format,
        )
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    if detected_format == "sarif_json":
        segments = extract_sarif(content_bytes, limits=limits)
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    if detected_format == "oscal_json":
        segments = extract_oscal_json(content_bytes, limits=limits)
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    if detected_format == "stig_json":
        segments = extract_stig_json(content_bytes, limits=limits)
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    if detected_format in {"xml", "oscal_xml", "nessus_xml", "stig_xml"}:
        segments = extract_xml_structured(
            content_bytes,
            limits=limits,
            detected_format=detected_format,
        )
        return _succeeded_outcome(
            detected_format=detected_format,
            detected_media_type=detected_media_type,
            segments=segments,
        )

    raise ExtractionError("unsupported content format", error_code="source_type_mismatch")


def _extract_svg(
    content: bytes,
    *,
    limits: ExtractionLimits,
    vision_policy: VisionPolicy,
) -> ExtractionOutcome:
    del vision_policy
    summary, metadata = sanitize_svg(content, limits=limits)
    segment = ExtractedSegment(
        segment_index=1,
        text=summary,
        locator={"kind": "text_offsets", "start_offset": 0, "end_offset": len(summary)},
        extraction_method="text",
        metadata=metadata,
    )
    return ExtractionOutcome(
        status="succeeded",
        detected_format="svg",
        detected_media_type="image/svg+xml",
        page_count=None,
        total_text_characters=len(summary),
        vision_status="not_needed",
        segments=(segment,),
    )


def _succeeded_outcome(
    *,
    detected_format: str,
    detected_media_type: str,
    segments: list[ExtractedSegment],
) -> ExtractionOutcome:
    ordered = tuple(sorted(segments, key=_segment_sort_key))
    total_chars = sum(len(segment.text) for segment in ordered)
    return ExtractionOutcome(
        status="succeeded",
        detected_format=detected_format,
        detected_media_type=detected_media_type,
        page_count=None,
        total_text_characters=total_chars,
        vision_status="not_needed",
        segments=ordered,
    )


def _segment_sort_key(segment: ExtractedSegment) -> tuple[str, str]:
    locator = segment.locator
    kind = str(locator.get("kind", ""))
    if kind == "json_pointer":
        return kind, str(locator.get("json_pointer", ""))
    if kind == "xml_path":
        return kind, str(locator.get("xml_path", ""))
    if kind == "sheet_cell":
        return kind, f"{locator.get('sheet', '')}:{locator.get('cell', '')}"
    if kind == "page":
        return kind, f"{locator.get('page', 0):09d}"
    if kind == "section":
        return kind, str(locator.get("section", ""))
    if kind == "text_offsets":
        return kind, f"{locator.get('start_offset', 0):09d}"
    if kind == "image_region":
        return kind, "image_region"
    return kind, str(segment.segment_index)


def _media_types_compatible(declared: str, detected: str) -> bool:
    if declared == detected:
        return True
    if declared == "text/plain" and detected == "text/markdown":
        return True
    if declared == "application/json" and detected in {
        "application/json",
        "application/sarif+json",
    }:
        return True
    if declared == "application/xml" and detected == "application/xml":
        return True
    if declared == "application/octet-stream":
        return True
    return False


def _formats_compatible(declared: str, detected: str) -> bool:
    if declared == detected:
        return True
    json_formats = {"json", "oscal_json", "sarif_json", "stig_json"}
    xml_formats = {"xml", "nessus_xml", "oscal_xml", "stig_xml"}
    if declared == "json" and detected in json_formats:
        return True
    if declared == "xml" and detected in xml_formats:
        return True
    if declared == "text" and detected == "markdown":
        return True
    return False
