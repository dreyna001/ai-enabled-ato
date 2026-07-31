"""Tests for bounded SSP generation and contextual-patch contracts."""

from __future__ import annotations

import json

import pytest

from dataclasses import replace

from ato_service.ssp_workspace.profile_bundles import (
    default_implementation_statement_deterministic_policy,
    default_implementation_statement_policy,
)
from ato_service.ssp_workspace.generation_contracts import (
    ControlResponsePolicy,
    GenerationContractError,
    PatchResult,
    ProfileValidationError,
    SelectedProfilePolicy,
    TargetedPatch,
    contains_oscal_parameter_insert_syntax,
    deterministic_question_key,
    parse_generation_response,
    parse_patch_response,
    requirement_text_has_unresolved_organization_parameters,
    validate_applied_patch_result,
    validate_workspace_implementation_statement,
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

    with pytest.raises(GenerationContractError, match="supporting facts"):
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


def test_patch_parses_markdown_fenced_json() -> None:
    inner = {
        "schema_version": "1.0.0",
        "patches": [],
        "questions_to_add": [],
        "question_ids_to_resolve": [],
        "change_summary": "No grounded edits.",
    }
    raw = f"```json\n{json.dumps(inner)}\n```"

    result = parse_patch_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
        allowed_question_ids=set(),
        current_revisions={},
    )

    assert result.change_summary == "No grounded edits."
    assert result.patches == ()


def _minimal_profile_policy(
    *,
    parameterized: frozenset[str] = frozenset(),
    implementation_statement_rules=None,
) -> SelectedProfilePolicy:
    statement = (
        implementation_statement_rules
        or default_implementation_statement_policy().deterministic
    )
    return SelectedProfilePolicy(
        sections={},
        control_response=ControlResponsePolicy(
            implementation_statuses=frozenset(
                {
                    "implemented",
                    "partially_implemented",
                    "planned",
                    "not_implemented",
                    "not_applicable",
                    "unknown",
                }
            ),
            responsibilities=frozenset(
                {"system_specific", "hybrid", "inherited", "unknown"}
            ),
            question_owner_types=frozenset(
                {"isso", "agency", "technical", "system_owner"}
            ),
            evidence_required_for_agent_statement=(
                statement.require_evidence_for_agent_non_unknown_claims
            ),
        ),
        implementation_statement_rules=statement,
        parameterized_control_ids=parameterized,
    )


def test_odp_detection_matches_oscal_insert_tokens_case_insensitively() -> None:
    assert requirement_text_has_unresolved_organization_parameters(
        "Retain audit records for {{ insert: param, au-11_odp }}."
    )
    assert requirement_text_has_unresolved_organization_parameters(
        "Retain audit records for {{ INSERT: PARAM , au-11_odp }}."
    )
    assert not requirement_text_has_unresolved_organization_parameters(
        "Retain audit records for ninety days."
    )
    assert contains_oscal_parameter_insert_syntax(
        "Bad {{ insert: param, au-11_odp }} copy."
    )


def test_generation_rejects_oscal_placeholder_in_implementation_statement() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AC-2",
                    "implementation_status": "unknown",
                    "responsibility": "unknown",
                    "implementation_statement": (
                        "Retain logs for {{ insert: param, au-11_odp }}."
                    ),
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )

    with pytest.raises(GenerationContractError, match="placeholder syntax"):
        parse_generation_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
        )


def test_generation_requires_question_for_unresolved_parameterized_control() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AU-11",
                    "implementation_status": "unknown",
                    "responsibility": "unknown",
                    "implementation_statement": "",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )

    with pytest.raises(GenerationContractError, match="parameterized controls"):
        parse_generation_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
            profile_policy=_minimal_profile_policy(parameterized=frozenset({"AU-11"})),
        )


def test_generation_requires_question_for_empty_statement_even_when_implemented() -> (
    None
):
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AU-11",
                    "implementation_status": "implemented",
                    "responsibility": "system_specific",
                    "implementation_statement": "",
                    "supporting_fact_ids": ["fact-log"],
                }
            ],
            "questions": [],
        }
    )

    with pytest.raises(GenerationContractError, match="parameterized controls"):
        parse_generation_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
            profile_policy=_minimal_profile_policy(parameterized=frozenset({"AU-11"})),
        )


def test_generation_allows_omitted_parameterized_controls_without_questions() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [],
            "questions": [],
        }
    )

    result = parse_generation_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
        profile_policy=_minimal_profile_policy(
            parameterized=frozenset({"AU-11", "AC-2"})
        ),
    )

    assert result.controls == ()
    assert result.questions == ()


def test_generation_allows_evidence_backed_parameterized_control_without_question() -> (
    None
):
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AU-11",
                    "implementation_status": "implemented",
                    "responsibility": "system_specific",
                    "implementation_statement": "Audit records are retained for 90 days.",
                    "supporting_fact_ids": ["fact-log"],
                }
            ],
            "questions": [],
        }
    )

    result = parse_generation_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
        profile_policy=_minimal_profile_policy(parameterized=frozenset({"AU-11"})),
    )

    assert result.controls[0].implementation_statement.startswith("Audit records")


def test_generation_skips_parameterized_question_rule_without_profile_policy() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AU-11",
                    "implementation_status": "unknown",
                    "responsibility": "unknown",
                    "implementation_statement": "",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )

    result = parse_generation_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
    )

    assert result.controls[0].control_id == "AU-11"
    assert result.questions == ()


def test_patch_rejects_oscal_placeholder_in_implementation_statement() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "patches": [
                {
                    "target_type": "control",
                    "target_id": "AC-2",
                    "expected_revision": 1,
                    "changes": {
                        "implementation_statement": (
                            "Updated {{ insert: param, ac-2_odp }} text."
                        )
                    },
                    "supporting_fact_ids": ["fact-idp"],
                }
            ],
            "questions_to_add": [],
            "question_ids_to_resolve": [],
            "change_summary": "Invalid placeholder.",
        }
    )

    with pytest.raises(GenerationContractError, match="placeholder syntax"):
        parse_patch_response(
            raw,
            allowed_section_ids=SECTIONS,
            allowed_control_ids=CONTROLS,
            allowed_fact_ids=FACTS,
            allowed_question_ids=set(),
            current_revisions={("control", "AC-2"): 1},
        )


def test_workspace_implementation_statement_rejects_oscal_placeholders() -> None:
    with pytest.raises(ProfileValidationError, match="placeholder syntax"):
        validate_workspace_implementation_statement(
            "Policy for {{ insert: param, ac-1_prm_1 }}."
        )


def test_validate_applied_patch_result_rejects_oscal_placeholder_statement() -> None:
    result = PatchResult(
        patches=(
            TargetedPatch(
                target_type="control",
                target_id="AC-2",
                expected_revision=1,
                changes={
                    "implementation_statement": (
                        "Updated {{ insert: param, ac-2_odp }} text."
                    )
                },
                supporting_fact_ids=("fact-idp",),
            ),
        ),
        questions_to_add=(),
        question_ids_to_resolve=(),
        change_summary="Invalid placeholder.",
    )

    with pytest.raises(ProfileValidationError, match="placeholder syntax"):
        validate_applied_patch_result(
            result,
            _minimal_profile_policy(),
            allowed_fact_ids=FACTS,
        )


def _statement_policy(**overrides):
    return replace(default_implementation_statement_deterministic_policy(), **overrides)


def test_statement_policy_disabled_oscal_rejection_allows_direct_save_syntax() -> None:
    policy = _minimal_profile_policy(
        implementation_statement_rules=_statement_policy(
            reject_oscal_parameter_insert_syntax=False,
        )
    )
    validate_workspace_implementation_statement(
        "Policy for {{ insert: param, ac-1_prm_1 }}.",
        profile_policy=policy,
    )


def test_statement_policy_disabled_oscal_rejection_allows_generation_syntax() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AC-2",
                    "implementation_status": "unknown",
                    "responsibility": "unknown",
                    "implementation_statement": "Uses {{ insert: param, ac-2_odp }}.",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )
    result = parse_generation_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
        profile_policy=_minimal_profile_policy(
            implementation_statement_rules=_statement_policy(
                reject_oscal_parameter_insert_syntax=False,
                require_evidence_for_agent_non_unknown_claims=False,
            )
        ),
    )
    assert "{{ insert: param" in result.controls[0].implementation_statement


def test_statement_policy_disabled_odp_question_rule_allows_empty_response() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AU-11",
                    "implementation_status": "unknown",
                    "responsibility": "unknown",
                    "implementation_statement": "",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )
    result = parse_generation_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
        profile_policy=_minimal_profile_policy(
            parameterized=frozenset({"AU-11"}),
            implementation_statement_rules=_statement_policy(
                require_question_for_unresolved_parameterized_controls=False,
            ),
        ),
    )
    assert result.controls[0].control_id == "AU-11"


def test_statement_policy_merged_evidence_requirement_can_disable_agent_facts() -> None:
    policy = _minimal_profile_policy(
        implementation_statement_rules=_statement_policy(
            require_evidence_for_agent_non_unknown_claims=False,
        )
    )
    assert policy.control_response.evidence_required_for_agent_statement is False
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AC-2",
                    "implementation_status": "implemented",
                    "responsibility": "system_specific",
                    "implementation_statement": "Accounts are managed centrally.",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )
    result = parse_generation_response(
        raw,
        allowed_section_ids=SECTIONS,
        allowed_control_ids=CONTROLS,
        allowed_fact_ids=FACTS,
        profile_policy=policy,
    )
    assert result.controls[0].supporting_fact_ids == ()


def test_validate_applied_patch_respects_disabled_oscal_rejection() -> None:
    result = PatchResult(
        patches=(
            TargetedPatch(
                target_type="control",
                target_id="AC-2",
                expected_revision=1,
                changes={
                    "implementation_statement": (
                        "Updated {{ insert: param, ac-2_odp }} text."
                    )
                },
                supporting_fact_ids=("fact-idp",),
            ),
        ),
        questions_to_add=(),
        question_ids_to_resolve=(),
        change_summary="Allowed when policy disabled.",
    )
    validate_applied_patch_result(
        result,
        _minimal_profile_policy(
            implementation_statement_rules=_statement_policy(
                reject_oscal_parameter_insert_syntax=False,
            )
        ),
        allowed_fact_ids=FACTS,
    )
