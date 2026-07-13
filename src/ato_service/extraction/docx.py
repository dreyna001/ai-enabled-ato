"""DOCX text and table extraction via ZIP preflight and python-docx."""

from __future__ import annotations

from docx.oxml import parse_xml
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.safety_xml import (
    element_path,
    local_name,
    parse_xml_bounded,
    reject_external_relationship_targets,
)
from ato_service.extraction.safety_zip import open_safe_zip, read_zip_member
from ato_service.extraction.types import ExtractedSegment, ExtractionLimits


def extract_docx(content: bytes, *, limits: ExtractionLimits) -> list[ExtractedSegment]:
    """Extract paragraph and table text from one DOCX container."""
    members = open_safe_zip(content, limits=limits, office_container=True)
    for path in members:
        if path.endswith(".rels"):
            reject_external_relationship_targets(members[path].data)

    document_xml = read_zip_member(members, "word/document.xml")
    parse_xml_bounded(document_xml, limits=limits)
    try:
        root = parse_xml(document_xml)
    except XMLSyntaxError as exc:
        raise ExtractionError("xml parse failed", error_code="source_parse_failed") from exc

    body = root.body
    if body is None:
        raise ExtractionError("docx body is missing", error_code="source_parse_failed")

    segments: list[ExtractedSegment] = []
    section_index = 0
    for child in body.iterchildren():
        tag = local_name(child.tag)
        if tag == "p":
            text = _paragraph_text(child)
            if not text:
                continue
            section_index += 1
            segments.append(
                ExtractedSegment(
                    segment_index=section_index,
                    text=text,
                    locator={"kind": "section", "section": f"paragraph-{section_index}"},
                    extraction_method="deterministic",
                )
            )
        elif tag == "tbl":
            table_text = _table_text(child)
            if not table_text:
                continue
            section_index += 1
            segments.append(
                ExtractedSegment(
                    segment_index=section_index,
                    text=table_text,
                    locator={"kind": "section", "section": f"table-{section_index}"},
                    extraction_method="deterministic",
                    metadata={"xml_path": element_path(child, root=root)},
                )
            )

    if not segments:
        raise ExtractionError("docx contains no extractable text", error_code="source_parse_failed")
    return segments


def _paragraph_text(paragraph_element) -> str:
    return Paragraph(paragraph_element, None).text.strip()


def _table_text(table_element) -> str:
    table = Table(table_element, None)
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            cell_text = cell.text.strip()
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows).strip()
