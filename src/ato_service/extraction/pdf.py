"""PDF text extraction and bounded page rendering.

Uses ``pypdf`` for text-layer decode and ``pypdfium2`` only for bounded
page-to-PNG rendering. Diff 2 does not invoke vision models.
"""

from __future__ import annotations

from io import BytesIO
import math
import struct
import zlib

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.limits import MAX_IMAGE_DIMENSION, MAX_IMAGE_PIXELS
from ato_service.extraction.types import (
    ExtractedSegment,
    ExtractionLimits,
    ExtractionOutcome,
    VisionPolicy,
)

try:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
except ImportError:  # pragma: no cover - dependency installed in project env
    PdfReader = None  # type: ignore[assignment,misc]
    PdfReadError = Exception  # type: ignore[assignment,misc]

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover - dependency installed in project env
    pdfium = None  # type: ignore[assignment]

_MAX_RENDERED_PNG_BYTES = 104_857_600
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def extract_pdf(
    content: bytes,
    *,
    limits: ExtractionLimits,
    vision_policy: VisionPolicy,
) -> ExtractionOutcome:
    """Extract PDF text or return evidence/vision-deferred for scanned pages."""
    if PdfReader is None:
        raise ExtractionError("pypdf is not installed", error_code="source_parse_failed")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
    except PdfReadError as exc:
        raise ExtractionError("pdf is corrupt or unreadable", error_code="source_parse_failed") from exc

    if reader.is_encrypted:
        raise ExtractionError("encrypted pdf is not supported", error_code="source_parse_failed")

    page_count = len(reader.pages)
    if page_count > limits.max_pdf_pages_per_file:
        raise ExtractionError(
            "pdf page count exceeds configured limit",
            error_code="package_limit_exceeded",
        )
    if page_count == 0:
        raise ExtractionError("pdf contains no pages", error_code="source_parse_failed")

    segments: list[ExtractedSegment] = []
    total_chars = 0
    scanned_pages = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise ExtractionError(
                f"pdf text extraction failed on page {page_number}",
                error_code="source_parse_failed",
            ) from exc
        stripped = page_text.strip()
        if not stripped:
            scanned_pages += 1
            continue
        total_chars += len(page_text)
        if total_chars > limits.max_extracted_text_characters_per_file:
            raise ExtractionError(
                "extracted pdf text exceeds configured character limit",
                error_code="package_limit_exceeded",
            )
        segments.append(
            ExtractedSegment(
                segment_index=page_number,
                text=page_text,
                locator={"kind": "page", "page": page_number},
                extraction_method="deterministic",
            )
        )

    if scanned_pages == page_count:
        return _scanned_pdf_outcome(
            page_count=page_count,
            vision_policy=vision_policy,
        )

    if scanned_pages > 0 and vision_policy.vision_allowed:
        return ExtractionOutcome(
            status="vision_deferred",
            detected_format="pdf",
            detected_media_type="application/pdf",
            page_count=page_count,
            total_text_characters=total_chars,
            vision_status="deferred",
            segments=tuple(segments),
        )

    return ExtractionOutcome(
        status="succeeded",
        detected_format="pdf",
        detected_media_type="application/pdf",
        page_count=page_count,
        total_text_characters=total_chars,
        vision_status="not_needed",
        segments=tuple(segments),
    )


def _scanned_pdf_outcome(*, page_count: int, vision_policy: VisionPolicy) -> ExtractionOutcome:
    if vision_policy.vision_allowed:
        return ExtractionOutcome(
            status="vision_deferred",
            detected_format="pdf",
            detected_media_type="application/pdf",
            page_count=page_count,
            total_text_characters=0,
            vision_status="deferred",
            segments=(),
        )
    return ExtractionOutcome(
        status="evidence_only",
        detected_format="pdf",
        detected_media_type="application/pdf",
        page_count=page_count,
        total_text_characters=0,
        vision_status="evidence_only",
        segments=(),
    )


def render_page_png(
    content: bytes,
    *,
    page_number: int,
    limits: ExtractionLimits,
    scale: float = 1.0,
) -> bytes:
    """Render one PDF page to PNG for a later governed vision call."""
    if pdfium is None:
        raise ExtractionError("pypdfium2 is not installed", error_code="source_parse_failed")
    if page_number < 1:
        raise ValueError("page_number must be >= 1")
    if scale <= 0 or scale > 4:
        raise ValueError("scale must be > 0 and <= 4")

    document = None
    try:
        document = pdfium.PdfDocument(content)
        if len(document) > limits.max_pdf_pages_per_file:
            raise ExtractionError(
                "pdf page count exceeds configured limit",
                error_code="package_limit_exceeded",
            )
        if page_number > len(document):
            raise ExtractionError("pdf page number is out of range", error_code="source_parse_failed")
        page = document[page_number - 1]
        try:
            page_width, page_height = page.get_size()
            width = math.ceil(page_width * scale)
            height = math.ceil(page_height * scale)
            _validate_render_dimensions(width, height)
            bitmap = page.render(scale=scale)
            try:
                _validate_render_dimensions(bitmap.width, bitmap.height)
                png_bytes = _bitmap_to_png(bitmap)
            finally:
                bitmap.close()
        finally:
            page.close()
    except ExtractionError:
        raise
    except (pdfium.PdfiumError, RuntimeError, ValueError, OSError) as exc:
        raise ExtractionError(
            "pdf page render failed",
            error_code="source_parse_failed",
        ) from exc
    finally:
        if document is not None:
            document.close()

    if not png_bytes:
        raise ExtractionError("pdf page render produced no bytes", error_code="source_parse_failed")
    return png_bytes


def _validate_render_dimensions(width: int, height: int) -> None:
    if width < 1 or height < 1:
        raise ExtractionError(
            "pdf render dimensions are invalid",
            error_code="source_parse_failed",
        )
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ExtractionError(
            "pdf render dimensions exceed image limit",
            error_code="package_limit_exceeded",
        )
    if width * height > MAX_IMAGE_PIXELS:
        raise ExtractionError(
            "pdf render pixel count exceeds image limit",
            error_code="package_limit_exceeded",
        )


def _bitmap_to_png(bitmap: object) -> bytes:
    width = int(bitmap.width)
    height = int(bitmap.height)
    stride = int(bitmap.stride)
    mode = str(bitmap.mode).upper()
    raw = memoryview(bitmap.buffer).cast("B")

    if mode in {"BGR", "RGB"}:
        source_channels = 3
        png_channels = 3
        color_type = 2
    elif mode in {"BGRA", "RGBA", "BGRX", "RGBX"}:
        source_channels = 4
        png_channels = 4 if mode in {"BGRA", "RGBA"} else 3
        color_type = 6 if png_channels == 4 else 2
    elif mode in {"L", "GRAY"}:
        source_channels = 1
        png_channels = 1
        color_type = 0
    else:
        raise ExtractionError(
            f"unsupported PDF bitmap mode: {mode}",
            error_code="source_parse_failed",
        )

    row_bytes = width * source_channels
    if stride < row_bytes or len(raw) < stride * height:
        raise ExtractionError(
            "pdf bitmap buffer is truncated",
            error_code="source_parse_failed",
        )

    compressor = zlib.compressobj(level=6)
    compressed_parts: list[bytes] = []
    compressed_size = 0
    for row_index in range(height):
        source = bytes(raw[row_index * stride : row_index * stride + row_bytes])
        converted = _convert_bitmap_row(
            source,
            mode=mode,
            source_channels=source_channels,
            png_channels=png_channels,
        )
        part = compressor.compress(b"\x00" + converted)
        compressed_size += len(part)
        if compressed_size > _MAX_RENDERED_PNG_BYTES:
            raise ExtractionError(
                "rendered PNG exceeds output byte limit",
                error_code="package_limit_exceeded",
            )
        compressed_parts.append(part)
    final_part = compressor.flush()
    compressed_size += len(final_part)
    if compressed_size > _MAX_RENDERED_PNG_BYTES:
        raise ExtractionError(
            "rendered PNG exceeds output byte limit",
            error_code="package_limit_exceeded",
        )
    compressed_parts.append(final_part)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    png = (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", b"".join(compressed_parts))
        + _png_chunk(b"IEND", b"")
    )
    if len(png) > _MAX_RENDERED_PNG_BYTES:
        raise ExtractionError(
            "rendered PNG exceeds output byte limit",
            error_code="package_limit_exceeded",
        )
    return png


def _convert_bitmap_row(
    source: bytes,
    *,
    mode: str,
    source_channels: int,
    png_channels: int,
) -> bytes:
    if mode in {"RGB", "RGBA", "L", "GRAY"}:
        return source
    converted = bytearray((len(source) // source_channels) * png_channels)
    output_index = 0
    for input_index in range(0, len(source), source_channels):
        converted[output_index : output_index + 3] = (
            source[input_index + 2],
            source[input_index + 1],
            source[input_index],
        )
        if png_channels == 4:
            converted[output_index + 3] = source[input_index + 3]
        output_index += png_channels
    return bytes(converted)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)
