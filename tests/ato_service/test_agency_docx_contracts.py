"""Contract tests for agency template DOCX mapping and review."""

from __future__ import annotations

import json

import pytest

from ato_service.ssp_workspace.agency_docx_contracts import (
    AgencyDocxContractError,
    OutlineCell,
    OutlineParagraph,
    TemplateOutline,
    canonical_append_column_map,
    control_table_column_names,
    parse_mapping_plan,
    parse_review_response,
)

OUTLINE = TemplateOutline(
    paragraphs=(
        OutlineParagraph(locator="paragraph:0", text="System Name:"),
        OutlineParagraph(locator="paragraph:1", text="Purpose placeholder"),
    ),
    cells=tuple(
        OutlineCell(
            locator=f"table:0:cell:0:{column_index}",
            text=control_table_column_names()[column_index],
        )
        for column_index in range(6)
    )
    + (OutlineCell(locator="table:0:cell:1:0", text=""),),
)
SECTIONS = frozenset({"purpose", "boundary"})


def _column_map(**overrides: int) -> dict[str, int]:
    mapping = canonical_append_column_map()
    mapping.update(overrides)
    return mapping


def _plan_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "text_placements": [
            {
                "target_locator": "paragraph:0",
                "source_ref": "system.display_name",
                "mode": "replace",
            }
        ],
        "control_table": {"table_index": 0, "column_map": _column_map()},
        "exceptions": [],
        "summary": "Mapped system name.",
    }
    payload.update(overrides)
    return payload


def test_mapping_plan_parses_normal_path() -> None:
    plan = parse_mapping_plan(
        json.dumps(_plan_payload()),
        outline=OUTLINE,
        allowed_section_ids=SECTIONS,
    )
    assert plan.text_placements[0].source_ref == "system.display_name"
    assert plan.control_table.table_index == 0


def test_mapping_plan_rejects_malformed_json() -> None:
    with pytest.raises(AgencyDocxContractError, match="strict JSON"):
        parse_mapping_plan("not-json", outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_mapping_plan_rejects_extra_top_level_keys() -> None:
    payload = _plan_payload()
    payload["unexpected"] = True
    with pytest.raises(AgencyDocxContractError, match="extra="):
        parse_mapping_plan(json.dumps(payload), outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_mapping_plan_rejects_unknown_source_ref() -> None:
    payload = _plan_payload(
        text_placements=[
            {
                "target_locator": "paragraph:0",
                "source_ref": "section:missing",
                "mode": "replace",
            }
        ]
    )
    with pytest.raises(AgencyDocxContractError, match="source_ref"):
        parse_mapping_plan(json.dumps(payload), outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_mapping_plan_rejects_unknown_locator() -> None:
    payload = _plan_payload(
        text_placements=[
            {
                "target_locator": "paragraph:99",
                "source_ref": "system.display_name",
                "mode": "replace",
            }
        ]
    )
    with pytest.raises(AgencyDocxContractError, match="target_locator"):
        parse_mapping_plan(json.dumps(payload), outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_mapping_plan_rejects_duplicate_target() -> None:
    payload = _plan_payload(
        text_placements=[
            {
                "target_locator": "paragraph:0",
                "source_ref": "system.display_name",
                "mode": "replace",
            },
            {
                "target_locator": "paragraph:0",
                "source_ref": "profile.profile_id",
                "mode": "append",
            },
        ]
    )
    with pytest.raises(AgencyDocxContractError, match="duplicate target_locator"):
        parse_mapping_plan(json.dumps(payload), outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_mapping_plan_rejects_out_of_bounds_control_table_index() -> None:
    payload = _plan_payload(control_table={"table_index": 3, "column_map": _column_map()})
    with pytest.raises(AgencyDocxContractError, match="out of bounds"):
        parse_mapping_plan(json.dumps(payload), outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_mapping_plan_rejects_duplicate_column_map_indices() -> None:
    payload = _plan_payload(
        control_table={
            "table_index": 0,
            "column_map": _column_map(title=0),
        }
    )
    with pytest.raises(AgencyDocxContractError, match="distinct"):
        parse_mapping_plan(json.dumps(payload), outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_mapping_plan_normalizes_append_table_column_map() -> None:
    permuted = {
        column: index
        for index, column in enumerate(
            (
                "evidence_links",
                "implementation_statement",
                "responsibility",
                "implementation_status",
                "title",
                "control_id",
            )
        )
    }
    payload = _plan_payload(
        control_table={"table_index": None, "column_map": permuted},
    )
    plan = parse_mapping_plan(
        json.dumps(payload),
        outline=OUTLINE,
        allowed_section_ids=SECTIONS,
    )
    assert dict(plan.control_table.column_map) == canonical_append_column_map()


def test_mapping_plan_requires_exception_severity() -> None:
    payload = _plan_payload(
        control_table={"table_index": None, "column_map": _column_map()},
        exceptions=[{"code": "x", "message": "y"}],
    )
    with pytest.raises(AgencyDocxContractError, match="missing="):
        parse_mapping_plan(json.dumps(payload), outline=OUTLINE, allowed_section_ids=SECTIONS)


def test_review_response_parses_blocker_and_warning() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "summary": "Needs attention.",
            "issues": [
                {
                    "severity": "blocker",
                    "code": "missing_control_rows",
                    "message": "Rendered table missing controls.",
                    "locator": "table:0:cell:1:0",
                },
                {
                    "severity": "warning",
                    "code": "empty_cell",
                    "message": "Cell still empty.",
                    "locator": None,
                },
            ],
        }
    )
    summary, issues = parse_review_response(raw)
    assert summary == "Needs attention."
    assert issues[0].severity == "blocker"
    assert issues[1].severity == "warning"


def test_review_response_rejects_extra_issue_keys() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "summary": "Bad issue.",
            "issues": [
                {
                    "severity": "warning",
                    "code": "x",
                    "message": "y",
                    "locator": None,
                    "extra": True,
                }
            ],
        }
    )
    with pytest.raises(AgencyDocxContractError, match="extra="):
        parse_review_response(raw)
