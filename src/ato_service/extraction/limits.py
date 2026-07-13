"""Resolve extraction limits from validated runtime configuration."""

from __future__ import annotations

from typing import Any

from ato_service.extraction.types import ExtractionLimits
from ato_service.runtime_config import RuntimeConfig, _positive_limit_from_document

_DEFAULT_MAX_PDF_PAGES_PER_FILE = 200
_DEFAULT_MAX_EXTRACTED_TEXT_CHARACTERS_PER_FILE = 2_000_000
_DEFAULT_MAX_ZIP_MEMBERS_PER_ARCHIVE = 500
_DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES_PER_ARCHIVE = 104_857_600
_DEFAULT_MAX_ZIP_DECOMPRESSION_RATIO = 100
_DEFAULT_MAX_XML_DEPTH = 64
_DEFAULT_MAX_XML_ELEMENTS = 100_000
_DEFAULT_MAX_XML_ATTRIBUTES_PER_ELEMENT = 128
_DEFAULT_MAX_XML_TEXT_NODE_CHARACTERS = 1_048_576

# Internal constant; not runtime-configurable.
MAX_JSON_DEPTH = 64
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_DIMENSION = 16_384


def resolve_extraction_limits(document: dict[str, Any]) -> ExtractionLimits:
    """Return extraction limits using schema defaults for absent keys."""
    return ExtractionLimits(
        max_pdf_pages_per_file=_positive_limit_from_document(
            document,
            "MAX_PDF_PAGES_PER_FILE",
            default=_DEFAULT_MAX_PDF_PAGES_PER_FILE,
        ),
        max_extracted_text_characters_per_file=_positive_limit_from_document(
            document,
            "MAX_EXTRACTED_TEXT_CHARACTERS_PER_FILE",
            default=_DEFAULT_MAX_EXTRACTED_TEXT_CHARACTERS_PER_FILE,
        ),
        max_zip_members_per_archive=_positive_limit_from_document(
            document,
            "MAX_ZIP_MEMBERS_PER_ARCHIVE",
            default=_DEFAULT_MAX_ZIP_MEMBERS_PER_ARCHIVE,
        ),
        max_zip_uncompressed_bytes_per_archive=_positive_limit_from_document(
            document,
            "MAX_ZIP_UNCOMPRESSED_BYTES_PER_ARCHIVE",
            default=_DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES_PER_ARCHIVE,
        ),
        max_zip_decompression_ratio=_positive_limit_from_document(
            document,
            "MAX_ZIP_DECOMPRESSION_RATIO",
            default=_DEFAULT_MAX_ZIP_DECOMPRESSION_RATIO,
        ),
        max_xml_depth=_positive_limit_from_document(
            document,
            "MAX_XML_DEPTH",
            default=_DEFAULT_MAX_XML_DEPTH,
        ),
        max_xml_elements=_positive_limit_from_document(
            document,
            "MAX_XML_ELEMENTS",
            default=_DEFAULT_MAX_XML_ELEMENTS,
        ),
        max_xml_attributes_per_element=_positive_limit_from_document(
            document,
            "MAX_XML_ATTRIBUTES_PER_ELEMENT",
            default=_DEFAULT_MAX_XML_ATTRIBUTES_PER_ELEMENT,
        ),
        max_xml_text_node_characters=_positive_limit_from_document(
            document,
            "MAX_XML_TEXT_NODE_CHARACTERS",
            default=_DEFAULT_MAX_XML_TEXT_NODE_CHARACTERS,
        ),
    )


def resolve_extraction_limits_from_config(config: RuntimeConfig) -> ExtractionLimits:
    """Return extraction limits from a loaded ``RuntimeConfig``."""
    return resolve_extraction_limits(config.document)
