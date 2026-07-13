"""Focused extraction library tests with in-memory hostile fixtures."""

from __future__ import annotations

import json
import re
import stat
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from ato_service.extraction import (
    ExtractionContext,
    ExtractionError,
    ExtractionLimits,
    VisionPolicy,
    extract_content,
    resolve_extraction_limits,
)
from ato_service.extraction.pdf import render_page_png
from ato_service.extraction.safety_zip import open_safe_zip
from ato_service.extraction.serialize import outcome_to_contract

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = ROOT / "docs" / "contracts"
FIXTURES_DIR = Path(__file__).resolve().parent / "extraction"

_FORBIDDEN_PARSER_LEAK_TERMS = (
    "traceback",
    "lxml",
    "openpyxl",
    "pillow",
    "pil.",
    "pil ",
    "defusedxml",
    "packagenotfounderror",
    "badzipfile",
    "keyerror",
    "elementtree",
    "etree.",
    "invalid literal",
)
_BOUNDED_ERROR_MESSAGE = re.compile(r"^[A-Za-z][A-Za-z0-9 ,._/-]{0,119}$")


@pytest.fixture
def office_limits() -> ExtractionLimits:
    return ExtractionLimits(
        max_pdf_pages_per_file=3,
        max_extracted_text_characters_per_file=100,
        max_zip_members_per_archive=500,
        max_zip_uncompressed_bytes_per_archive=1_048_576,
        max_zip_decompression_ratio=100,
        max_xml_depth=32,
        max_xml_elements=200,
        max_xml_attributes_per_element=8,
        max_xml_text_node_characters=500,
    )


@pytest.fixture
def limits() -> ExtractionLimits:
    return ExtractionLimits(
        max_pdf_pages_per_file=3,
        max_extracted_text_characters_per_file=100,
        max_zip_members_per_archive=5,
        max_zip_uncompressed_bytes_per_archive=10_000,
        max_zip_decompression_ratio=10,
        max_xml_depth=4,
        max_xml_elements=20,
        max_xml_attributes_per_element=4,
        max_xml_text_node_characters=50,
    )


@pytest.fixture
def context() -> ExtractionContext:
    return ExtractionContext(
        declared_media_type=None,
        detected_media_type=None,
        declared_format=None,
        artifact_kind=None,
        filename=None,
    )


def _extract(
    content: bytes,
    *,
    limits: ExtractionLimits,
    context: ExtractionContext,
    vision_allowed: bool = False,
):
    return extract_content(
        content_bytes=content,
        sha256="0" * 64,
        context=context,
        limits=limits,
        vision_policy=VisionPolicy(vision_allowed=vision_allowed),
    )


def _build_zip(members: dict[str, bytes], *, compression=zipfile.ZIP_STORED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for path, data in members.items():
            archive.writestr(path, data)
    return buffer.getvalue()


def _assert_bounded_extraction_error(
    exc_info: pytest.ExceptionInfo[ExtractionError],
    *,
    error_code: str,
) -> None:
    error = exc_info.value
    assert error.error_code == error_code
    message = str(error)
    assert _BOUNDED_ERROR_MESSAGE.match(message), message
    lowered = message.lower()
    assert not any(term in lowered for term in _FORBIDDEN_PARSER_LEAK_TERMS), message


def _docx_context(**overrides: object) -> ExtractionContext:
    return ExtractionContext(
        declared_media_type=overrides.get(
            "declared_media_type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        detected_media_type=overrides.get("detected_media_type"),
        declared_format="docx",
        artifact_kind=overrides.get("artifact_kind", "evidence_document"),
        filename=overrides.get("filename", "sample.docx"),
    )


def _xlsx_context(**overrides: object) -> ExtractionContext:
    return ExtractionContext(
        declared_media_type=overrides.get("declared_media_type"),
        detected_media_type=overrides.get("detected_media_type"),
        declared_format="xlsx",
        artifact_kind=overrides.get("artifact_kind"),
        filename=overrides.get("filename", "book.xlsx"),
    )


def _build_docx(*paragraphs: str) -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _build_docx_with_table() -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Control"
    table.cell(0, 1).text = "Status"
    table.cell(1, 0).text = "AC-1"
    table.cell(1, 1).text = "Implemented"
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _build_xlsx(cells: dict[str, str]) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    for ref, value in cells.items():
        worksheet[ref] = value
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _build_xlsx_formula_cached() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet["B1"] = "safe"
    base = BytesIO()
    workbook.save(base)
    sheet_xml = (FIXTURES_DIR / "xlsx_formula_cached_sheet.xml").read_bytes()
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(base.getvalue()), "r") as source:
        with zipfile.ZipFile(output, "w") as archive:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "xl/worksheets/sheet1.xml":
                    data = sheet_xml
                archive.writestr(info, data)
    return output.getvalue()


def _build_xlsx_formula_without_cache() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet["A1"] = "=NOW()"
    worksheet["B1"] = "safe"
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _build_png(width: int, height: int, *, color: str = "white") -> bytes:
    pillow = pytest.importorskip("PIL")
    image = pillow.Image.new("RGB", (width, height), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _build_text_pdf(text: str) -> bytes:
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(200, 200)
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 120 Td ({escaped}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = stream
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _build_scanned_pdf(page_count: int = 1) -> bytes:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_resolve_extraction_limits_uses_runtime_defaults() -> None:
    limits = resolve_extraction_limits({})
    assert limits.max_pdf_pages_per_file == 200
    assert limits.max_zip_members_per_archive == 500
    assert limits.max_zip_uncompressed_bytes_per_archive == 104_857_600


def test_json_extraction_produces_json_pointer_locators(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    payload = {"system": {"name": "demo-system"}, "controls": ["AC-1", "AC-2"]}
    outcome = _extract(json.dumps(payload).encode("utf-8"), limits=limits, context=context)
    assert outcome.status == "succeeded"
    pointers = {segment.locator["json_pointer"] for segment in outcome.segments}
    assert "/controls/0" in pointers
    assert "/controls/1" in pointers
    assert outcome.segments == tuple(sorted(outcome.segments, key=lambda s: s.locator["json_pointer"]))


def test_json_duplicate_keys_fail_without_partial_segments(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(b'{"a":1,"a":2}', limits=limits, context=context)
    assert exc_info.value.error_code == "source_parse_failed"


def test_json_depth_limit_fails_explicitly(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    payload: dict[str, object] = {"value": "leaf"}
    for _ in range(65):
        payload = {"nested": payload}
    with pytest.raises(ExtractionError) as exc_info:
        _extract(json.dumps(payload).encode("utf-8"), limits=limits, context=context)
    assert exc_info.value.error_code == "package_limit_exceeded"


def test_text_rejects_control_characters(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(b"hello\x01world", limits=limits, context=context)
    assert exc_info.value.error_code == "source_parse_failed"


def test_text_exact_character_limit_passes(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    text = "a" * limits.max_extracted_text_characters_per_file
    outcome = _extract(text.encode("utf-8"), limits=limits, context=context)
    assert outcome.total_text_characters == len(text)


def test_text_over_character_limit_fails(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    text = "a" * (limits.max_extracted_text_characters_per_file + 1)
    with pytest.raises(ExtractionError) as exc_info:
        _extract(text.encode("utf-8"), limits=limits, context=context)
    assert exc_info.value.error_code == "package_limit_exceeded"


def test_zip_traversal_member_rejected(limits: ExtractionLimits) -> None:
    archive = _build_zip({"../evil.txt": b"bad"})
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(archive, limits=limits)
    assert exc_info.value.error_code == "unsafe_archive"


def test_zip_duplicate_normalized_paths_rejected(limits: ExtractionLimits) -> None:
    archive = _build_zip({"./a.txt": b"1", "a.txt": b"2"})
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(archive, limits=limits)
    assert exc_info.value.error_code == "unsafe_archive"


def test_zip_nested_archive_member_rejected(limits: ExtractionLimits) -> None:
    archive = _build_zip({"inner.zip": b"PK\x03\x04"})
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(archive, limits=limits)
    assert exc_info.value.error_code == "unsafe_archive"


def test_zip_member_count_limit_rejected(limits: ExtractionLimits) -> None:
    members = {f"file{i}.txt": b"x" for i in range(limits.max_zip_members_per_archive + 1)}
    archive = _build_zip(members)
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(archive, limits=limits)
    assert exc_info.value.error_code == "package_limit_exceeded"


def test_zip_decompression_ratio_limit_rejected(limits: ExtractionLimits) -> None:
    payload = b"x" * 200
    archive = _build_zip({"big.txt": payload}, compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(archive, limits=limits)
    assert exc_info.value.error_code == "package_limit_exceeded"


def test_zip_linux_regular_member_is_accepted(limits: ExtractionLimits) -> None:
    buffer = BytesIO()
    info = zipfile.ZipInfo("evidence.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, b"evidence")

    members = open_safe_zip(buffer.getvalue(), limits=limits)

    assert members["evidence.txt"].data == b"evidence"


def test_zip_linux_symlink_member_is_rejected(limits: ExtractionLimits) -> None:
    buffer = BytesIO()
    info = zipfile.ZipInfo("evidence-link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, b"evidence.txt")

    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(buffer.getvalue(), limits=limits)

    assert exc_info.value.error_code == "unsafe_archive"


@pytest.mark.parametrize(
    "member_name",
    [
        "folder\u2215evidence.txt",
        "folder./evidence.txt",
        "folder /evidence.txt",
    ],
)
def test_zip_unicode_and_windows_ambiguous_paths_are_rejected(
    limits: ExtractionLimits,
    member_name: str,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(_build_zip({member_name: b"bad"}), limits=limits)

    assert exc_info.value.error_code == "unsafe_archive"


def test_zip_directory_name_is_validated_before_skipping(
    limits: ExtractionLimits,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(_build_zip({"unsafe.//": b""}), limits=limits)

    assert exc_info.value.error_code == "unsafe_archive"


def test_zip_zero_compressed_size_for_nonempty_member_is_rejected(
    limits: ExtractionLimits,
) -> None:
    archive_bytes = bytearray(_build_zip({"evidence.txt": b"evidence"}))
    central_offset = archive_bytes.index(b"PK\x01\x02")
    archive_bytes[central_offset + 20 : central_offset + 24] = b"\x00\x00\x00\x00"

    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(bytes(archive_bytes), limits=limits)

    assert exc_info.value.error_code == "package_limit_exceeded"


def test_zip_nested_archive_magic_is_rejected_without_archive_extension(
    limits: ExtractionLimits,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        open_safe_zip(_build_zip({"payload.bin": b"PK\x03\x04nested"}), limits=limits)

    assert exc_info.value.error_code == "unsafe_archive"


def test_xml_xxe_preflight_rejected(limits: ExtractionLimits, context: ExtractionContext) -> None:
    xml = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe "bad">]><root>&xxe;</root>'
    with pytest.raises(ExtractionError) as exc_info:
        _extract(xml, limits=limits, context=context)
    assert exc_info.value.error_code == "unsafe_archive"


def test_xml_doctype_after_initial_scan_window_is_rejected(
    office_limits: ExtractionLimits,
) -> None:
    xml = b" " * 9000 + b"<!DOCTYPE root><root/>"
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            xml,
            limits=office_limits,
            context=ExtractionContext(
                declared_media_type=None,
                detected_media_type=None,
                declared_format="xml",
                artifact_kind=None,
                filename="late-doctype.xml",
            ),
        )
    assert exc_info.value.error_code == "unsafe_archive"


def test_docx_extracts_paragraphs(office_limits: ExtractionLimits) -> None:
    outcome = _extract(
        _build_docx("Alpha", "Beta"),
        limits=office_limits,
        context=_docx_context(),
    )
    texts = [segment.text for segment in outcome.segments]
    assert texts == ["Alpha", "Beta"]


def test_docx_extracts_table_rows(office_limits: ExtractionLimits) -> None:
    outcome = _extract(
        _build_docx_with_table(),
        limits=office_limits,
        context=_docx_context(),
    )
    assert [segment.text for segment in outcome.segments] == [
        "Control | Status\nAC-1 | Implemented"
    ]
    assert outcome.segments[0].locator == {
        "kind": "section",
        "section": "table-1",
    }


def test_docx_empty_document_rejected(office_limits: ExtractionLimits) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(_build_docx(), limits=office_limits, context=_docx_context())
    _assert_bounded_extraction_error(exc_info, error_code="source_parse_failed")
    assert str(exc_info.value) == "docx contains no extractable text"


def test_docx_truncated_archive_maps_to_bounded_parse_error(
    office_limits: ExtractionLimits,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            b"PK\x03\x04truncated-docx-bytes",
            limits=office_limits,
            context=_docx_context(),
        )
    _assert_bounded_extraction_error(exc_info, error_code="source_parse_failed")


def test_docx_broken_document_xml_maps_to_bounded_parse_error(
    office_limits: ExtractionLimits,
) -> None:
    archive = _build_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<not-well-formed",
        }
    )
    with pytest.raises(ExtractionError) as exc_info:
        _extract(archive, limits=office_limits, context=_docx_context())
    _assert_bounded_extraction_error(exc_info, error_code="source_parse_failed")


def test_docx_macro_member_rejected(limits: ExtractionLimits, context: ExtractionContext) -> None:
    archive = _build_zip(
        {
            "word/document.xml": b"<w:document/>",
            "word/vbaProject.bin": b"macro",
        }
    )
    with pytest.raises(ExtractionError) as exc_info:
        _extract(archive, limits=limits, context=ExtractionContext(
            declared_media_type=None,
            detected_media_type=None,
            declared_format="docx",
            artifact_kind=None,
            filename="sample.docx",
        ))
    assert exc_info.value.error_code == "unsafe_archive"


def test_docx_external_relationship_rejected(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    archive = _build_zip(
        {
            "word/document.xml": (
                b'<?xml version="1.0"?>'
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p><w:r><w:t>ok</w:t></w:r></w:p></w:body></w:document>"
            ),
            "word/_rels/document.xml.rels": (
                b'<?xml version="1.0"?>'
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                b'<Relationship Target="https://evil.example/file" Type="external"/>'
                b"</Relationships>"
            ),
        }
    )
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            archive,
            limits=limits,
            context=ExtractionContext(
                declared_media_type=None,
                detected_media_type=None,
                declared_format="docx",
                artifact_kind=None,
                filename="sample.docx",
            ),
        )
    assert exc_info.value.error_code == "unsafe_archive"


def test_docx_target_mode_external_relationship_rejected(
    office_limits: ExtractionLimits,
) -> None:
    archive = _build_zip(
        {
            "word/document.xml": (
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                b'wordprocessingml/2006/main"><w:body><w:p><w:r>'
                b"<w:t>ok</w:t></w:r></w:p></w:body></w:document>"
            ),
            "word/_rels/document.xml.rels": (
                b"<Relationships><Relationship Target='relative.xml' "
                b"TargetMode='External'/></Relationships>"
            ),
        }
    )
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            archive,
            limits=office_limits,
            context=ExtractionContext(
                declared_media_type=None,
                detected_media_type=None,
                declared_format="docx",
                artifact_kind=None,
                filename="sample.docx",
            ),
        )
    assert exc_info.value.error_code == "unsafe_archive"


def test_xlsx_extracts_cached_cell_values(office_limits: ExtractionLimits) -> None:
    outcome = _extract(
        _build_xlsx({"A1": "10", "B2": "20"}),
        limits=office_limits,
        context=_xlsx_context(),
    )
    locators = {(s.locator["sheet"], s.locator["cell"]): s.text for s in outcome.segments}
    assert locators[("sheet1", "A1")] == "10"
    assert locators[("sheet1", "B2")] == "20"


def test_xlsx_formula_uses_cached_value_without_evaluation(
    office_limits: ExtractionLimits,
) -> None:
    outcome = _extract(
        _build_xlsx_formula_cached(),
        limits=office_limits,
        context=_xlsx_context(),
    )
    by_cell = {segment.locator["cell"]: segment for segment in outcome.segments}
    assert by_cell["A1"].text == "3"
    assert by_cell["A1"].metadata == {"formula_ignored": True}
    assert by_cell["B1"].text == "safe"


def test_xlsx_formula_without_cached_value_is_skipped(
    office_limits: ExtractionLimits,
) -> None:
    outcome = _extract(
        _build_xlsx_formula_without_cache(),
        limits=office_limits,
        context=_xlsx_context(),
    )
    assert [(segment.locator["cell"], segment.text) for segment in outcome.segments] == [
        ("B1", "safe")
    ]


def test_xlsx_truncated_archive_maps_to_bounded_parse_error(
    office_limits: ExtractionLimits,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            b"PK\x03\x04truncated-xlsx-bytes",
            limits=office_limits,
            context=_xlsx_context(),
        )
    _assert_bounded_extraction_error(exc_info, error_code="source_parse_failed")


def test_xlsx_broken_workbook_xml_maps_to_bounded_parse_error(
    office_limits: ExtractionLimits,
) -> None:
    archive = _build_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<not-well-formed",
            "xl/worksheets/sheet1.xml": b"<worksheet/>",
        }
    )
    with pytest.raises(ExtractionError) as exc_info:
        _extract(archive, limits=office_limits, context=_xlsx_context())
    _assert_bounded_extraction_error(exc_info, error_code="source_parse_failed")


def test_xlsx_external_links_rejected(limits: ExtractionLimits) -> None:
    archive = _build_zip(
        {
            "xl/workbook.xml": b"<workbook/>",
            "xl/worksheets/sheet1.xml": b"<worksheet/>",
            "xl/externalLinks/externalLink1.xml": b"<externalLink/>",
        }
    )
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            archive,
            limits=limits,
            context=ExtractionContext(
                declared_media_type=None,
                detected_media_type=None,
                declared_format="xlsx",
                artifact_kind=None,
                filename="book.xlsx",
            ),
        )
    assert exc_info.value.error_code == "unsafe_archive"


def test_svg_sanitization_strips_unsafe_script(
    limits: ExtractionLimits,
) -> None:
    svg = (
        b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" '
        b'onload="alert(1)"><title>Safe title</title>'
        b'<script><title>Unsafe title</title>alert(1)</script>'
        b'<a href="javascript:alert(1)"><desc>Safe description</desc></a></svg>'
    )
    outcome = _extract(
        svg,
        limits=limits,
        context=ExtractionContext(
            declared_media_type=None,
            detected_media_type=None,
            declared_format="svg",
            artifact_kind=None,
            filename="diagram.svg",
        ),
    )
    assert outcome.segments[0].text == "Safe title\nSafe description"
    assert "alert" not in outcome.segments[0].text
    assert "Unsafe title" not in outcome.segments[0].text


def test_svg_safe_metadata_extracted(limits: ExtractionLimits) -> None:
    svg = (
        b'<?xml version="1.0"?>'
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b"<title>Boundary</title><desc>System boundary diagram</desc></svg>"
    )
    outcome = _extract(
        svg,
        limits=limits,
        context=ExtractionContext(
            declared_media_type=None,
            detected_media_type=None,
            declared_format="svg",
            artifact_kind=None,
            filename="diagram.svg",
        ),
    )
    assert outcome.status == "succeeded"
    assert "Boundary" in outcome.segments[0].text


def test_png_header_and_dimensions_validated(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    outcome = _extract(_build_png(8, 8), limits=limits, context=context, vision_allowed=False)
    assert outcome.status == "evidence_only"
    assert outcome.segments[0].locator["kind"] == "image_region"
    assert outcome.segments[0].extraction_method == "deterministic"


def test_deferred_image_segment_is_deterministic_metadata(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    outcome = _extract(
        _build_png(8, 8),
        limits=limits,
        context=context,
        vision_allowed=True,
    )
    assert outcome.status == "vision_deferred"
    assert outcome.segments[0].extraction_method == "deterministic"


def test_png_oversized_dimensions_rejected(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(_build_png(20_000, 4), limits=limits, context=context)
    assert exc_info.value.error_code == "package_limit_exceeded"


def test_png_truncated_bytes_map_to_bounded_image_error(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(_build_png(8, 8)[:20], limits=limits, context=context)
    assert exc_info.value.error_code in {"source_parse_failed", "source_type_mismatch"}
    _assert_bounded_extraction_error(exc_info, error_code=exc_info.value.error_code)


def test_png_header_only_map_to_bounded_image_error(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(b"\x89PNG\r\n\x1a\n", limits=limits, context=context)
    _assert_bounded_extraction_error(exc_info, error_code="source_type_mismatch")
    assert str(exc_info.value) == "png header is invalid"


def test_pdf_text_extraction_succeeds(limits: ExtractionLimits, context: ExtractionContext) -> None:
    outcome = _extract(_build_text_pdf("Deterministic PDF text"), limits=limits, context=context)
    assert outcome.status == "succeeded"
    assert outcome.page_count == 1
    assert "Deterministic PDF text" in outcome.segments[0].text


def test_pdf_encrypted_rejected(limits: ExtractionLimits, context: ExtractionContext) -> None:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(ExtractionError) as exc_info:
        _extract(buffer.getvalue(), limits=limits, context=context)
    assert exc_info.value.error_code == "source_parse_failed"


def test_pdf_corrupt_rejected(limits: ExtractionLimits, context: ExtractionContext) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(b"%PDF-1.4\nnot-a-real-pdf", limits=limits, context=context)
    assert exc_info.value.error_code == "source_parse_failed"


def test_scanned_pdf_returns_vision_deferred_when_allowed(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    outcome = _extract(_build_scanned_pdf(), limits=limits, context=context, vision_allowed=True)
    assert outcome.status == "vision_deferred"
    assert outcome.vision_status == "deferred"
    assert outcome.segments == ()


def test_scanned_pdf_returns_evidence_only_when_vision_blocked(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    outcome = _extract(_build_scanned_pdf(), limits=limits, context=context, vision_allowed=False)
    assert outcome.status == "evidence_only"
    assert outcome.total_text_characters == 0


def test_pdf_page_limit_exact_passes(limits: ExtractionLimits, context: ExtractionContext) -> None:
    outcome = _extract(
        _build_scanned_pdf(page_count=limits.max_pdf_pages_per_file),
        limits=limits,
        context=context,
        vision_allowed=False,
    )
    assert outcome.page_count == limits.max_pdf_pages_per_file


def test_pdf_page_limit_over_fails(limits: ExtractionLimits, context: ExtractionContext) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            _build_scanned_pdf(page_count=limits.max_pdf_pages_per_file + 1),
            limits=limits,
            context=context,
        )
    assert exc_info.value.error_code == "package_limit_exceeded"


def test_pdf_render_helper_is_bounded_without_pillow(
    limits: ExtractionLimits,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdfium = pytest.importorskip("pypdfium2")
    monkeypatch.setattr(
        pdfium.PdfBitmap,
        "to_pil",
        lambda self: (_ for _ in ()).throw(AssertionError("Pillow path used")),
    )
    pdf_bytes = _build_text_pdf("render me")
    png = render_page_png(pdf_bytes, page_number=1, limits=limits, scale=1.0)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_declared_media_type_mismatch_rejected(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        extract_content(
            content_bytes=json.dumps({"a": 1}).encode("utf-8"),
            sha256="0" * 64,
            context=ExtractionContext(
                declared_media_type="application/pdf",
                detected_media_type=None,
                declared_format=None,
                artifact_kind=None,
                filename=None,
            ),
            limits=limits,
            vision_policy=VisionPolicy(vision_allowed=False),
        )
    assert exc_info.value.error_code == "source_type_mismatch"


def test_declared_format_cannot_bypass_content_detection(
    limits: ExtractionLimits,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            b'{"name":"not a pdf"}',
            limits=limits,
            context=ExtractionContext(
                declared_media_type=None,
                detected_media_type=None,
                declared_format="pdf",
                artifact_kind=None,
                filename="claimed.pdf",
            ),
        )
    assert exc_info.value.error_code == "source_type_mismatch"


@pytest.mark.parametrize(
    ("declared_format", "content"),
    [
        ("sarif_json", b'{"version":"2.1.0","not_runs":[]}'),
        ("oscal_json", b'{"unrelated":{}}'),
        ("stig_json", b'{"unrelated":{}}'),
        ("nessus_xml", b"<unrelated/>"),
        ("oscal_xml", b"<unrelated/>"),
        ("stig_xml", b"<unrelated/>"),
    ],
)
def test_declared_structured_format_requires_expected_signature(
    office_limits: ExtractionLimits,
    declared_format: str,
    content: bytes,
) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        _extract(
            content,
            limits=office_limits,
            context=ExtractionContext(
                declared_media_type=None,
                detected_media_type=None,
                declared_format=declared_format,
                artifact_kind=None,
                filename=None,
            ),
        )
    assert exc_info.value.error_code == "source_type_mismatch"


def test_outcome_serializes_to_contract_schema(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    outcome = _extract(json.dumps({"name": "demo"}).encode("utf-8"), limits=limits, context=context)
    schema = json.loads((CONTRACTS_DIR / "extracted-segment.schema.json").read_text(encoding="utf-8"))
    domain_schema = json.loads((CONTRACTS_DIR / "domain.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resources(
        [
            (schema["$id"], Resource.from_contents(schema)),
            (domain_schema["$id"], Resource.from_contents(domain_schema)),
        ]
    )
    validator = Draft202012Validator(schema, registry=registry)
    validator.validate(outcome_to_contract(outcome))


def test_nessus_xml_produces_xml_path_locators(
    limits: ExtractionLimits,
    context: ExtractionContext,
) -> None:
    xml = (
        b'<?xml version="1.0"?>'
        b"<NessusClientData><Report><ReportHost name=\"host\">"
        b"<ReportItem pluginName=\"ssh\">Open port</ReportItem>"
        b"</ReportHost></Report></NessusClientData>"
    )
    outcome = _extract(
        xml,
        limits=limits,
        context=ExtractionContext(
            declared_media_type=None,
            detected_media_type=None,
            declared_format=None,
            artifact_kind=None,
            filename="scan.nessus",
        ),
    )
    assert outcome.segments
    assert outcome.segments[0].locator["kind"] == "xml_path"


def test_json_pointer_preserves_escaping(limits: ExtractionLimits, context: ExtractionContext) -> None:
    payload = {"a/b": 1, "c~d": 2}
    outcome = _extract(json.dumps(payload).encode("utf-8"), limits=limits, context=context)
    pointers = {segment.locator["json_pointer"] for segment in outcome.segments}
    assert "/a~1b" in pointers
    assert "/c~0d" in pointers
