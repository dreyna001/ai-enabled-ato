"""Focused tests for closed SSP provider-facing output schemas."""

from __future__ import annotations

import copy
import json

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from pydantic_ai import NativeOutput, StructuredDict
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
import pytest

from ato_service.ssp_workspace.model_schemas import (
    AGENCY_DOCX_MAPPING_SCHEMA_NAME,
    AGENCY_DOCX_REVIEW_SCHEMA_NAME,
    CATEGORIZATION_SCHEMA_NAME,
    DIAGRAM_PROPOSAL_SCHEMA_NAME,
    INITIAL_GENERATION_SCHEMA_NAME,
    PATCH_SCHEMA_NAME,
    VISION_FACTS_SCHEMA_NAME,
    AgencyDocxMappingOutput,
    DiagramProposalOutput,
    InitialGenerationOutput,
    PatchOutput,
    VisionFactsOutput,
    normalize_native_output,
    output_schema_for,
)
from ato_service.ssp_workspace.generation_contracts import parse_patch_response

_SCHEMA_NAMES = (
    INITIAL_GENERATION_SCHEMA_NAME,
    CATEGORIZATION_SCHEMA_NAME,
    PATCH_SCHEMA_NAME,
    AGENCY_DOCX_MAPPING_SCHEMA_NAME,
    AGENCY_DOCX_REVIEW_SCHEMA_NAME,
    VISION_FACTS_SCHEMA_NAME,
    DIAGRAM_PROPOSAL_SCHEMA_NAME,
)


def _schema_nodes(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _schema_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_nodes(child)


def _valid_fixture(schema_name: str) -> dict[str, object]:
    if schema_name == INITIAL_GENERATION_SCHEMA_NAME:
        return {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [],
            "questions": [],
            "categorization": None,
        }
    if schema_name == CATEGORIZATION_SCHEMA_NAME:
        return {"schema_version": "1.0.0", "categorization": None}
    if schema_name == PATCH_SCHEMA_NAME:
        return {
            "schema_version": "1.0.0",
            "patches": [],
            "questions_to_add": [],
            "question_ids_to_resolve": [],
            "change_summary": "",
        }
    if schema_name == AGENCY_DOCX_MAPPING_SCHEMA_NAME:
        return {
            "schema_version": "1.0.0",
            "text_placements": [],
            "control_table": {
                "table_index": None,
                "column_map": {
                    "control_id": 0,
                    "title": 1,
                    "implementation_status": 2,
                    "responsibility": 3,
                    "implementation_statement": 4,
                    "evidence_links": 5,
                },
            },
            "exceptions": [],
            "summary": "",
        }
    if schema_name == AGENCY_DOCX_REVIEW_SCHEMA_NAME:
        return {"schema_version": "1.0.0", "summary": "", "issues": []}
    if schema_name == VISION_FACTS_SCHEMA_NAME:
        return {"schema_version": "1.0.0", "observations": []}
    if schema_name == DIAGRAM_PROPOSAL_SCHEMA_NAME:
        return {
            "schema_version": "1.0.0",
            "boundary_narrative": "The synthetic authorization boundary is present.",
            "components": [],
            "interconnections": [],
            "conflicts": [],
        }
    raise AssertionError(f"unhandled schema name: {schema_name}")


@pytest.mark.parametrize("schema_name", _SCHEMA_NAMES)
def test_active_schemas_are_closed_and_native_output_compatible(
    schema_name: str,
) -> None:
    schema = output_schema_for(schema_name)

    Draft202012Validator.check_schema(schema)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert NativeOutput(StructuredDict(schema), strict=True).strict is True

    provider_schema = OpenAIJsonSchemaTransformer(schema, strict=True).walk()
    for node in _schema_nodes(provider_schema):
        if node.get("type") == "object":
            properties = node.get("properties", {})
            assert set(properties) <= set(node.get("required", ()))


def test_schema_lookup_rejects_unknown_output() -> None:
    with pytest.raises(ValueError, match="unknown SSP model output"):
        output_schema_for("not-an-active-output")


def test_initial_generation_model_rejects_nested_extra_keys() -> None:
    payload = _valid_fixture(INITIAL_GENERATION_SCHEMA_NAME)
    payload["sections"] = [
        {
            "section_id": "system.purpose",
            "content": "",
            "supporting_fact_ids": [],
            "unexpected": "must fail",
        }
    ]

    with pytest.raises(ValidationError):
        InitialGenerationOutput.model_validate(payload)


def test_patch_schema_keeps_target_variant_and_revision_bounds() -> None:
    payload = _valid_fixture(PATCH_SCHEMA_NAME)
    payload["patches"] = [
        {
            "target_type": "ssp_section",
            "target_id": "system.purpose",
            "expected_revision": 0,
            "changes": {"content": "new content"},
            "supporting_fact_ids": [],
        }
    ]

    with pytest.raises(ValidationError):
        PatchOutput.model_validate(payload)


def test_openai_strict_patch_output_normalizes_nullable_sparse_changes() -> None:
    schema = output_schema_for(PATCH_SCHEMA_NAME)
    provider_schema = OpenAIJsonSchemaTransformer(schema, strict=True).walk()
    payload = {
        "schema_version": "1.0.0",
        "patches": [
            {
                "target_type": "control",
                "target_id": "ac-2",
                "expected_revision": 3,
                "changes": {
                    "implementation_statement": "Use the approved identity service.",
                    "implementation_status": None,
                    "responsibility": None,
                },
                "supporting_fact_ids": ["fact-1"],
            }
        ],
        "questions_to_add": [],
        "question_ids_to_resolve": [],
        "change_summary": "Update the implementation statement.",
    }

    # This is the schema OpenAI strict mode receives: every change property is
    # required, and PydanticAI converts the discriminated union's oneOf to anyOf.
    assert set(schema["$defs"]["ControlPatchChanges"]["required"]) == {  # type: ignore[index]
        "implementation_statement",
        "implementation_status",
        "responsibility",
    }
    Draft202012Validator(schema).validate(payload)
    Draft202012Validator(provider_schema).validate(payload)
    assert "oneOf" not in str(provider_schema)

    normalized = normalize_native_output(schema, payload)
    assert normalized["patches"][0]["changes"] == {  # type: ignore[index]
        "implementation_statement": "Use the approved identity service."
    }
    parsed = parse_patch_response(
        json.dumps(normalized),
        allowed_section_ids=set(),
        allowed_control_ids={"ac-2"},
        allowed_fact_ids={"fact-1"},
        allowed_question_ids=set(),
        current_revisions={("control", "ac-2"): 3},
    )
    assert parsed.patches[0].changes == {
        "implementation_statement": "Use the approved identity service."
    }

    clearing_payload = copy.deepcopy(payload)
    clearing_payload["patches"][0]["changes"]["implementation_statement"] = ""  # type: ignore[index]
    clearing = normalize_native_output(schema, clearing_payload)
    assert clearing["patches"][0]["changes"] == {  # type: ignore[index]
        "implementation_statement": ""
    }


def test_vision_schema_preserves_normalized_locator_invariant() -> None:
    payload = {
        "schema_version": "1.0.0",
        "observations": [
            {
                "text": "A visible setting is enabled.",
                "excerpt": "Enabled",
                "locator": {"x": 0.8, "y": 0.0, "width": 0.3, "height": 0.1},
            }
        ],
    }

    with pytest.raises(ValidationError, match="within image bounds"):
        VisionFactsOutput.model_validate(payload)


def test_agency_mapping_and_diagram_models_preserve_domain_defaults() -> None:
    mapping = AgencyDocxMappingOutput.model_validate(
        _valid_fixture(AGENCY_DOCX_MAPPING_SCHEMA_NAME)
    )
    diagram_payload = _valid_fixture(DIAGRAM_PROPOSAL_SCHEMA_NAME)
    diagram_payload["components"] = [
        {"name": "Web tier", "purpose": "User interface", "placement": "inside"}
    ]

    diagram = DiagramProposalOutput.model_validate(diagram_payload)

    assert mapping.control_table.table_index is None
    assert diagram.components[0].component_id == ""
    assert diagram.components[0].source == "diagram"
    assert diagram.components[0].confidence == "medium"


def test_schema_is_returned_as_a_fresh_dictionary() -> None:
    first = output_schema_for(INITIAL_GENERATION_SCHEMA_NAME)
    changed = copy.deepcopy(first)
    changed["title"] = "mutated"

    assert output_schema_for(INITIAL_GENERATION_SCHEMA_NAME)["title"] != "mutated"
