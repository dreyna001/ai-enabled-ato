"""Smoke check: installed API code renders SP 800-18 Table 1 DOCX marker."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docx import Document

from ato_service.ssp_workspace.export import EXPORT_SCHEMA_VERSION, build_workspace_docx_export

MARKER = "NIST SP 800-18 Revision 2 (Table 1)"


def _minimal_snapshot() -> dict[str, object]:
    return {
        "workspace_id": "11111111-1111-4111-8111-111111111111",
        "revision_id": "22222222-2222-4222-8222-222222222222",
        "content_sha256": "a" * 64,
        "approved_by": "isso@example.gov",
        "approved_at": "2026-07-27T12:00:00Z",
        "document_title": "Smoke Test System",
        "system": {"display_name": "Smoke Test System", "external_system_id": "SMK-1"},
        "profile": {
            "profile_id": "agency-fisma-80053r5-moderate",
            "version": "1.2.0",
            "impact_level": "moderate",
        },
        "facts": {"system.name": "Smoke Test System", "system.purpose": "Smoke export check."},
        "sections": [
            {
                "section_id": "system.purpose",
                "title": "System Purpose",
                "order": 0,
                "state": "reviewed",
                "content": "Smoke export check.",
            }
        ],
        "controls": [
            {
                "control_id": "AC-2",
                "title": "Account Management",
                "state": "reviewed",
                "implementation_status": "implemented",
                "responsibility": "system_specific",
                "implementation_statement": "Accounts are managed.",
                "evidence_links": [],
            }
        ],
        "questions": [],
        "standard_coverage": [
            {
                "source_id": "nist-sp-800-18r2",
                "requirement_id": "table1.system-overview",
                "title": "System Overview",
                "coverage_kind": "ssp_item",
                "item_ids": ["system.purpose"],
                "required": True,
            },
            {
                "source_id": "nist-sp-800-18r2",
                "requirement_id": "table1.control-implementation-details",
                "title": "Control Implementation Details",
                "coverage_kind": "controls",
                "item_ids": [],
                "required": True,
            },
        ],
        "ssp_items": [
            {
                "item_id": "system.purpose",
                "title": "System Purpose",
                "value_type": "string",
                "standard_refs": ["table1.system-overview"],
            }
        ],
        "control_order": ["AC-2"],
    }


def main() -> int:
    doc_bytes = build_workspace_docx_export(_minimal_snapshot(), include_open_questions=False)
    text = "\n".join(paragraph.text for paragraph in Document(BytesIO(doc_bytes)).paragraphs)
    has_marker = MARKER in text
    schema_ok = EXPORT_SCHEMA_VERSION == "1.1.0"
    print(f"export_schema_version={EXPORT_SCHEMA_VERSION}")
    print(f"docx_table1_marker={has_marker}")
    print("RESULT=PASS" if has_marker and schema_ok else "RESULT=FAIL")
    return 0 if has_marker and schema_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
