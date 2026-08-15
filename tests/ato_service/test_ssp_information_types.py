"""Tests for structured NIST SP 800-60 information type mapping."""

from __future__ import annotations

import json

import pytest

from ato_service.ssp_workspace.contracts import (
    FactContent,
    Provenance,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.editing import edit_section
from ato_service.ssp_workspace.information_types import (
    InformationTypeMapping,
    active_information_types_status,
    apply_information_type_register,
    build_information_types_export_block,
    mark_information_types_stale_if_needed,
    parse_information_type_register,
    validate_information_type_register,
)


def test_validate_information_type_register_requires_mapping() -> None:
    with pytest.raises(ValueError, match="at least one"):
        validate_information_type_register(())


def test_parse_and_validate_information_type_register() -> None:
    payload = {
        "information_types": [
            {
                "catalog_identifier": "C.3.5.1",
                "description": "General support systems processed by the application.",
            }
        ]
    }
    mappings = parse_information_type_register(json.dumps(payload))
    validate_information_type_register(mappings)
    assert mappings[0].catalog_identifier == "C.3.5.1"


def test_validate_information_type_register_requires_adjustment_rationale() -> None:
    mappings = (
        InformationTypeMapping(
            entry_id="1",
            catalog_identifier="C.3.5.1",
            description="General support systems processed by the application.",
            adjusted_confidentiality="high",
            adjusted_integrity=None,
            adjusted_availability=None,
            adjustment_rationale="",
            evidence=(),
        ),
    )
    with pytest.raises(ValueError, match="adjustment rationale"):
        validate_information_type_register(mappings)


def test_mark_information_types_stale_when_section_edited() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.information_types_status",
                value="confirmed",
                provenance=Provenance.ISSO_ENTERED,
            ),
        ),
        sections=(
            SectionContent(
                key="system.data_types",
                title="Information Types",
                content='{"information_types":[]}',
                state=SectionState.EDITED,
            ),
        ),
    )
    updated = mark_information_types_stale_if_needed(
        content,
        section_key="system.data_types",
    )
    facts = {fact.key: fact for fact in updated.facts}
    assert facts["system.information_types_status"].value == "stale"


def test_apply_information_type_register_sets_confirmed_status() -> None:
    content = RevisionContent(
        sections=(
            SectionContent(
                key="system.data_types",
                title="Information Types",
                content="",
                state=SectionState.EMPTY,
            ),
        )
    )
    updated = apply_information_type_register(
        content,
        mappings=(
            InformationTypeMapping(
                entry_id="entry-1",
                catalog_identifier="C.3.5.1",
                description="General support systems processed by the application.",
                adjusted_confidentiality=None,
                adjusted_integrity=None,
                adjusted_availability=None,
                adjustment_rationale="",
                evidence=(),
            ),
        ),
    )
    assert active_information_types_status(
        {fact.key: fact for fact in updated.facts}
    ) == "confirmed"
    section_content = next(
        section.content
        for section in updated.sections
        if section.key == "system.data_types"
    )
    parsed = parse_information_type_register(section_content)
    assert parsed[0].catalog_identifier == "C.3.5.1"


def test_build_information_types_export_block() -> None:
    block = build_information_types_export_block(
        sections={
            "system.data_types": json.dumps(
                {
                    "information_types": [
                        {
                            "entry_id": "entry-1",
                            "catalog_identifier": "C.3.5.1",
                            "description": "General support systems processed by the application.",
                            "adjusted_confidentiality": None,
                            "adjusted_integrity": None,
                            "adjusted_availability": None,
                            "adjustment_rationale": "",
                            "evidence": [],
                        }
                    ]
                }
            )
        },
        evidence_catalog={},
    )
    assert block["status"] == "confirmed"
    assert block["entries"][0]["catalog_title"] == "General Support Systems"
    assert block["entries"][0]["effective_confidentiality"] == "low"


def test_edit_section_marks_confirmed_information_types_stale() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.information_types_status",
                value="confirmed",
                provenance=Provenance.ISSO_ENTERED,
            ),
        ),
        sections=(
            SectionContent(
                key="system.data_types",
                title="Information Types",
                content='{"information_types":[{"catalog_identifier":"C.3.5.1","description":"Updated mapping text for support systems.","evidence":[]}]}',
                state=SectionState.EDITED,
            ),
        ),
    )
    updated = edit_section(
        content,
        section_key="system.data_types",
        text='{"information_types":[{"catalog_identifier":"C.3.5.8","description":"Human resources records processed by the application.","evidence":[]}]}',
    )
    facts = {fact.key: fact for fact in updated.facts}
    assert facts["system.information_types_status"].value == "stale"
