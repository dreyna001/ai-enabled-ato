"""Tests for bounded SSP generation and contextual-patch contracts."""

from __future__ import annotations

import json

import pytest

from ato_service.ssp_workspace.generation_contracts import (
    GenerationContractError,
    deterministic_question_key,
    parse_generation_response,
    parse_patch_response,
)

SECTIONS = {"purpose", "boundary"}
CONTROLS = {"AC-2", "AU-11"}
FACTS = {"fact-purpose", "fact-idp", "fact-log"}


def test_generation_parses_allowlisted_grounded_content() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [
                {
                    "section_id": "purpose",
                    "content": "The system supports grants.",
                    "supporting_fact_ids": ["fact-purpose"],
                }
            ],
            "controls": [
                {
                    "control_id": "AC-2",
                    "implementation_status": "partially_implemented",
                    "responsibility": "hybrid",
                    "implementation_statement": "Agency identity manages accounts.",
                    "supporting_fact_ids": ["fact-idp"],
                }
            ],
            "questions": [
                {
                    "target_type": "control",
                    "target_id": "AU-11",
                    "question": "What is the audit retention period?",
                    "owner_type": "agency",
                }
            ],
        }
    )

    result = parse_generation_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
    )

    assert result.sections[0].section_id == "purpose"
    assert result.controls[0].control_id == "AC-2"
    assert result.questions[0].question_key == deterministic_question_key(
        target_type="control",
        target_id="AU-11",
        question="What is the audit retention period?",
    )


def test_generation_rejects_statement_without_supporting_fact() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AC-2",
                    "implementation_status": "implemented",
                    "responsibility": "hybrid",
                    "implementation_statement": "Unsupported statement.",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )

    with pytest.raises(GenerationContractError, match="without supporting facts"):
        parse_generation_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
        )


def test_generation_rejects_control_outside_profile() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "ZZ-1",
                    "implementation_status": "unknown",
                    "responsibility": "unknown",
                    "implementation_statement": "",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )

    with pytest.raises(GenerationContractError, match="control_id is not allowed"):
        parse_generation_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
        )


def test_patch_rejects_stale_target_before_application() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "patches": [
                {
                    "target_type": "control",
                    "target_id": "AC-2",
                    "expected_revision": 2,
                    "changes": {
                        "implementation_statement": "Updated grounded statement."
                    },
                    "supporting_fact_ids": ["fact-idp"],
                }
            ],
            "questions_to_add": [],
            "question_ids_to_resolve": [],
            "change_summary": "Update AC-2.",
        }
    )

    with pytest.raises(GenerationContractError) as caught:
        parse_patch_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
            allowed_question_ids=set(),
            current_revisions={("control", "AC-2"): 3},
        )
    assert caught.value.failure_kind == "stale"
    assert caught.value.repairable is False


def test_patch_limits_changes_to_target_specific_fields() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "patches": [
                {
                    "target_type": "ssp_section",
                    "target_id": "boundary",
                    "expected_revision": 1,
                    "changes": {"implementation_status": "implemented"},
                    "supporting_fact_ids": [],
                }
            ],
            "questions_to_add": [],
            "question_ids_to_resolve": [],
            "change_summary": "Invalid field.",
        }
    )

    with pytest.raises(GenerationContractError) as caught:
        parse_patch_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
            allowed_question_ids=set(),
            current_revisions={("ssp_section", "boundary"): 1},
        )
    assert caught.value.failure_kind == "allowlist"
