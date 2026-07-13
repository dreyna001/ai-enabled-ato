"""Image validation and metadata via Pillow with bounded decode."""

from __future__ import annotations

import warnings
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.limits import MAX_IMAGE_DIMENSION, MAX_IMAGE_PIXELS
from ato_service.extraction.types import (
    ExtractedSegment,
    ExtractionLimits,
    ExtractionOutcome,
    ExtractionStatus,
    VisionPolicy,
    VisionStatus,
)

_PIL_FORMATS = {
    "png": "PNG",
    "jpeg": "JPEG",
    "webp": "WEBP",
}


def extract_image(
    content: bytes,
    *,
    limits: ExtractionLimits,
    vision_policy: VisionPolicy,
    detected_format: str,
) -> ExtractionOutcome:
    """Validate image headers and return evidence or vision-deferred outcome."""
    width, height = _validate_image_headers(content, detected_format=detected_format)
    pixels = width * height
    if pixels > MAX_IMAGE_PIXELS:
        raise ExtractionError(
            "image pixel count exceeds configured limit",
            error_code="package_limit_exceeded",
        )
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ExtractionError(
            "image dimensions exceed configured limit",
            error_code="package_limit_exceeded",
        )

    if vision_policy.vision_allowed:
        status: ExtractionStatus = "vision_deferred"
        vision_status: VisionStatus = "deferred"
    else:
        status = "evidence_only"
        vision_status = "evidence_only"

    segment = ExtractedSegment(
        segment_index=1,
        text=f"{detected_format} image evidence ({width}x{height})",
        locator={
            "kind": "image_region",
            "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        },
        extraction_method="deterministic",
        metadata={
            "format": detected_format,
            "width": width,
            "height": height,
            "bytes": len(content),
        },
    )
    return ExtractionOutcome(
        status=status,
        detected_format=detected_format,
        detected_media_type=_media_type(detected_format),
        page_count=None,
        total_text_characters=len(segment.text),
        vision_status=vision_status,
        segments=(segment,),
    )


def _media_type(detected_format: str) -> str:
    return {
        "png": "image/png",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }[detected_format]


def _validate_image_headers(content: bytes, *, detected_format: str) -> tuple[int, int]:
    expected_format = _PIL_FORMATS.get(detected_format)
    if expected_format is None:
        raise ExtractionError("unsupported image format", error_code="source_type_mismatch")
    if not content:
        raise ExtractionError(f"{detected_format} header is invalid", error_code="source_type_mismatch")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            return _open_and_validate_image(
                content,
                detected_format=detected_format,
                expected_format=expected_format,
            )
    except ExtractionError:
        raise
    except Image.DecompressionBombError as exc:
        raise ExtractionError(
            "image pixel count exceeds configured limit",
            error_code="package_limit_exceeded",
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise _map_image_exception(exc, detected_format=detected_format) from exc


def _open_and_validate_image(
    content: bytes,
    *,
    detected_format: str,
    expected_format: str,
) -> tuple[int, int]:
    with Image.open(BytesIO(content)) as image:
        if image.format != expected_format:
            raise ExtractionError(f"{detected_format} header is invalid", error_code="source_type_mismatch")
        width, height = image.size
        if width < 1 or height < 1:
            raise ExtractionError(f"{detected_format} dimensions are invalid", error_code="source_parse_failed")
        if width * height > MAX_IMAGE_PIXELS:
            raise ExtractionError(
                "image pixel count exceeds configured limit",
                error_code="package_limit_exceeded",
            )
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            raise ExtractionError(
                "image dimensions exceed configured limit",
                error_code="package_limit_exceeded",
            )
        image.verify()
    return width, height


def _map_image_exception(exc: Exception, *, detected_format: str) -> ExtractionError:
    if isinstance(exc, UnidentifiedImageError):
        return ExtractionError(f"{detected_format} header is invalid", error_code="source_type_mismatch")
    message = str(exc).lower()
    if detected_format == "jpeg" and "dimension" in message:
        return ExtractionError("jpeg dimensions were not found", error_code="source_parse_failed")
    if detected_format == "webp" and "chunk" in message:
        return ExtractionError("unsupported webp chunk type", error_code="source_parse_failed")
    return ExtractionError(f"{detected_format} header is invalid", error_code="source_parse_failed")
