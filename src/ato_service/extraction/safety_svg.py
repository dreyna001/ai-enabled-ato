"""SVG sanitization without inline rendering."""

from __future__ import annotations

import re
import defusedxml.ElementTree as ET

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.safety_xml import local_name, parse_xml_bounded
from ato_service.extraction.types import ExtractionLimits

_FORBIDDEN_TAGS = frozenset(
    {
        "script",
        "foreignobject",
        "iframe",
        "object",
        "embed",
        "use",
        "animate",
        "set",
    }
)
_EVENT_HANDLER_PATTERN = re.compile(r"^on[a-zA-Z]+")
_UNSAFE_URL_PATTERN = re.compile(r"^\s*javascript:", re.IGNORECASE)
_EXTERNAL_HREF_PATTERN = re.compile(r"^\s*(?:https?|file|data):", re.IGNORECASE)


def sanitize_svg(content: bytes, *, limits: ExtractionLimits) -> tuple[str, dict[str, str]]:
    """Parse SVG, strip unsafe constructs, and return metadata text only."""
    root = parse_xml_bounded(content, limits=limits)
    if local_name(root.tag).lower() != "svg":
        raise ExtractionError("root element is not svg", error_code="source_type_mismatch")

    _sanitize_element(root)
    title = _find_text(root, "title")
    description = _find_text(root, "desc")
    metadata = {
        "title": title,
        "description": description,
        "viewBox": root.attrib.get("viewBox", ""),
        "width": root.attrib.get("width", ""),
        "height": root.attrib.get("height", ""),
    }
    summary_parts = [part for part in (title, description) if part]
    summary = "\n".join(summary_parts) if summary_parts else "sanitized svg metadata"
    return summary, metadata


def _find_text(root: ET.Element, tag_name: str) -> str:
    for element in root.iter():
        if local_name(element.tag).lower() == tag_name and element.text:
            return element.text.strip()
    return ""


def _sanitize_element(element: ET.Element) -> None:
    tag = local_name(element.tag).lower()
    if tag in _FORBIDDEN_TAGS:
        raise ExtractionError("svg root cannot be sanitized", error_code="unsafe_archive")

    for attr_name, attr_value in list(element.attrib.items()):
        local_attr = attr_name.split("}", 1)[-1].lower()
        if _EVENT_HANDLER_PATTERN.match(local_attr):
            del element.attrib[attr_name]
            continue
        if local_attr == "href":
            if _UNSAFE_URL_PATTERN.match(attr_value) or _EXTERNAL_HREF_PATTERN.match(attr_value):
                del element.attrib[attr_name]

    for child in list(element):
        if local_name(child.tag).lower() in _FORBIDDEN_TAGS:
            element.remove(child)
            continue
        _sanitize_element(child)
