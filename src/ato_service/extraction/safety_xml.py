"""Hardened XML parsing without network resolution or entity expansion."""

from __future__ import annotations

import re
from io import BytesIO

import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.types import ExtractionLimits

_DOCTYPE_PATTERN = re.compile(r"<!DOCTYPE\b", re.IGNORECASE)
_ENTITY_PATTERN = re.compile(r"<!ENTITY\b", re.IGNORECASE)
_EXTERNAL_TARGET_MODE_PATTERN = re.compile(
    r"""\bTargetMode\s*=\s*(['"])\s*External\s*\1""",
    re.IGNORECASE,
)
_EXTERNAL_TARGET_PATTERN = re.compile(
    r"""\bTarget\s*=\s*(['"])\s*(?:[a-z][a-z0-9+.-]*:|//|\\\\)[^'"]*\1""",
    re.IGNORECASE,
)


def reject_xml_prolog_threats(xml_bytes: bytes) -> None:
    """Reject DOCTYPE and ENTITY declarations before parsing."""
    try:
        text = xml_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError("XML is not valid UTF-8", error_code="source_parse_failed") from exc
    if _DOCTYPE_PATTERN.search(text):
        raise ExtractionError("XML DOCTYPE is not allowed", error_code="unsafe_archive")
    if _ENTITY_PATTERN.search(text):
        raise ExtractionError("XML ENTITY declarations are not allowed", error_code="unsafe_archive")


def reject_external_relationship_targets(xml_bytes: bytes) -> None:
    """Reject Office relationship parts that reference external targets."""
    try:
        text = xml_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError("relationship XML is not valid UTF-8", error_code="source_parse_failed") from exc
    if _EXTERNAL_TARGET_MODE_PATTERN.search(text) or _EXTERNAL_TARGET_PATTERN.search(text):
        raise ExtractionError(
            "external relationship target rejected",
            error_code="unsafe_archive",
        )


def parse_xml_bounded(
    xml_bytes: bytes,
    *,
    limits: ExtractionLimits,
) -> ET.Element:
    """Parse XML with depth, element, attribute, and text-node limits."""
    reject_xml_prolog_threats(xml_bytes)
    depth = 0
    element_count = 0
    root: ET.Element | None = None

    try:
        for event, element in ET.iterparse(
            BytesIO(xml_bytes),
            events=("start", "end"),
        ):
            if event == "start":
                depth += 1
                element_count += 1
                if root is None:
                    root = element
                if depth > limits.max_xml_depth:
                    raise ExtractionError(
                        "XML depth exceeds configured limit",
                        error_code="package_limit_exceeded",
                    )
                if element_count > limits.max_xml_elements:
                    raise ExtractionError(
                        "XML element count exceeds configured limit",
                        error_code="package_limit_exceeded",
                    )
                if len(element.attrib) > limits.max_xml_attributes_per_element:
                    raise ExtractionError(
                        "XML attribute count exceeds configured limit",
                        error_code="package_limit_exceeded",
                    )
            else:
                _validate_xml_text_nodes(element, limits=limits)
                depth -= 1
    except DefusedXmlException as exc:
        raise _map_defusedxml_exception(exc) from exc
    except ET.ParseError as exc:
        raise ExtractionError("XML parse failed", error_code="source_parse_failed") from exc
    if root is None or depth != 0:
        raise ExtractionError("XML parse failed", error_code="source_parse_failed")
    return root


def _map_defusedxml_exception(exc: DefusedXmlException) -> ExtractionError:
    message = str(exc).lower()
    if "entity" in message or "dtd" in message or "doctype" in message:
        return ExtractionError("XML ENTITY declarations are not allowed", error_code="unsafe_archive")
    return ExtractionError("XML parse failed", error_code="source_parse_failed")


def _validate_xml_text_nodes(
    element: ET.Element,
    *,
    limits: ExtractionLimits,
) -> None:
    if element.text and len(element.text) > limits.max_xml_text_node_characters:
        raise ExtractionError(
            "XML text node exceeds configured limit",
            error_code="package_limit_exceeded",
        )
    if element.tail and len(element.tail) > limits.max_xml_text_node_characters:
        raise ExtractionError(
            "XML tail text exceeds configured limit",
            error_code="package_limit_exceeded",
        )


def local_name(tag: str) -> str:
    """Return the XML local name without namespace prefix."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def element_path(element: ET.Element, *, root: ET.Element) -> str:
    """Build a stable ``/tag[index]/...`` path for one element."""
    parts: list[str] = []
    current: ET.Element | None = element
    while current is not None:
        parent = _find_parent(root, current)
        if parent is None:
            index = 1
        else:
            siblings = [child for child in list(parent) if local_name(child.tag) == local_name(current.tag)]
            index = siblings.index(current) + 1
        parts.append(f"{local_name(current.tag)}[{index}]")
        current = parent
    parts.reverse()
    return "/" + "/".join(parts)


def _find_parent(root: ET.Element, target: ET.Element) -> ET.Element | None:
    for parent in root.iter():
        for child in list(parent):
            if child is target:
                return parent
    return None


def collect_element_text(element: ET.Element) -> str:
    """Return normalized text content for one element subtree."""
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element.iter():
        if child is not element and child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return " ".join(part.strip() for part in parts if part and part.strip())
