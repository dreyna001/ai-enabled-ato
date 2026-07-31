"""Selected-profile enforcement for SSP generation and workspace edits."""

from __future__ import annotations

import json

import pytest
import uuid

from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceLink,
    FactContent,
    Provenance,
    ProfileRequirement,
)
from ato_service.ssp_workspace.profile_bundles import (
    default_implementation_statement_policy,
)
from ato_service.ssp_workspace.generation_contracts import (
    ControlResponsePolicy,
    GenerationContractError,
    PatchResult,
    ProfileValidationError,
    SelectedProfilePolicy,
    SspItemPolicy,
    TargetedPatch,
    parse_generation_response,
    parse_patch_response,
    validate_applied_patch_result,
    validate_workspace_control_fields,
)
from ato_service.ssp_workspace.metrics import (
    agent_control_blocks_approval,
    calculate_workspace_metrics,
)


def _narrow_policy() -> SelectedProfilePolicy:
    return SelectedProfilePolicy(
        sections={
            "purpose": SspItemPolicy(
                item_id="purpose",
                required=True,
                value_type="string",
                min_length=10,
                allowed_values=frozenset({"allowed-only"}),
                standard_refs=("SP 800-18",),
            ),
            "components": SspItemPolicy(
                item_id="components",
                required=False,
                value_type="string_list",
                min_length=2,
                allowed_values=frozenset(),
                standard_refs=(),
            ),
        },
        control_response=ControlResponsePolicy(
            implementation_statuses=frozenset({"unknown", "implemented"}),
            responsibilities=frozenset({"unknown", "system_specific"}),
            question_owner_types=frozenset({"isso"}),
            evidence_required_for_agent_statement=True,
        ),
        implementation_statement_rules=(
            default_implementation_statement_policy().deterministic
        ),
    )


def test_narrowed_enums_reject_then_legacy_defaults_accept() -> None:
    policy = _narrow_policy()
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AC-2",
                    "implementation_status": "planned",
                    "responsibility": "unknown",
                    "implementation_statement": "",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )
    with pytest.raises(GenerationContractError) as caught:
        parse_generation_response(
            raw,
            allowed_section_ids={"purpose"},
            allowed_control_ids={"AC-2"},
            allowed_fact_ids=set(),
            profile_policy=policy,
        )
    assert caught.value.failure_kind == "allowlist"
    assert caught.value.repairable is True

    result = parse_generation_response(
        raw.replace("planned", "unknown"),
        allowed_section_ids={"purpose"},
        allowed_control_ids={"AC-2"},
        allowed_fact_ids=set(),
        profile_policy=policy,
    )
    assert result.controls[0].implementation_status == "unknown"


def test_agent_control_status_without_facts_is_rejected() -> None:
    policy = _narrow_policy()
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [],
            "controls": [
                {
                    "control_id": "AC-2",
                    "implementation_status": "implemented",
                    "responsibility": "unknown",
                    "implementation_statement": "",
                    "supporting_fact_ids": [],
                }
            ],
            "questions": [],
        }
    )
    with pytest.raises(GenerationContractError, match="supporting facts"):
        parse_generation_response(
            raw,
            allowed_section_ids={"purpose"},
            allowed_control_ids={"AC-2"},
            allowed_fact_ids={"fact-1"},
            profile_policy=policy,
        )


def test_section_min_length_and_allowed_values_are_repairable_schema_errors() -> None:
    policy = _narrow_policy()
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [
                {
                    "section_id": "purpose",
                    "content": "short",
                    "supporting_fact_ids": ["fact-1"],
                }
            ],
            "controls": [],
            "questions": [],
        }
    )
    with pytest.raises(GenerationContractError) as caught:
        parse_generation_response(
            raw,
            allowed_section_ids={"purpose"},
            allowed_control_ids=set(),
            allowed_fact_ids={"fact-1"},
            profile_policy=policy,
        )
    assert caught.value.failure_kind == "schema"
    assert caught.value.repairable is True

    raw_allowed = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [
                {
                    "section_id": "purpose",
                    "content": "not-in-allowlist-value",
                    "supporting_fact_ids": ["fact-1"],
                }
            ],
            "controls": [],
            "questions": [],
        }
    )
    with pytest.raises(GenerationContractError) as caught_value:
        parse_generation_response(
            raw_allowed,
            allowed_section_ids={"purpose"},
            allowed_control_ids=set(),
            allowed_fact_ids={"fact-1"},
            profile_policy=policy,
        )
    assert "not allowed" in caught_value.value.detail


def test_string_list_cardinality_enforced_for_non_empty_section() -> None:
    policy = _narrow_policy()
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "sections": [
                {
                    "section_id": "components",
                    "content": "- only one",
                    "supporting_fact_ids": ["fact-1"],
                }
            ],
            "controls": [],
            "questions": [],
        }
    )
    with pytest.raises(GenerationContractError, match="at least 2 list items"):
        parse_generation_response(
            raw,
            allowed_section_ids={"purpose", "components"},
            allowed_control_ids=set(),
            allowed_fact_ids={"fact-1"},
            profile_policy=policy,
        )


def test_human_workspace_validation_rejects_invalid_control_enums() -> None:
    policy = _narrow_policy()
    with pytest.raises(ProfileValidationError, match="implementation_status"):
        validate_workspace_control_fields(
            implementation_status="planned",
            responsibility="unknown",
            profile_policy=policy,
        )


def test_applied_patch_revalidates_grounded_control_metadata() -> None:
    policy = _narrow_policy()
    result = PatchResult(
        patches=(
            TargetedPatch(
                target_type="control",
                target_id="AC-2",
                expected_revision=1,
                changes={
                    "implementation_status": "implemented",
                    "responsibility": "system_specific",
                },
                supporting_fact_ids=(),
            ),
        ),
        questions_to_add=(),
        question_ids_to_resolve=(),
        change_summary="bad",
    )
    with pytest.raises(ProfileValidationError, match="supporting facts"):
        validate_applied_patch_result(result, policy, allowed_fact_ids={"fact-1"})


def test_optional_requirements_do_not_count_toward_completion() -> None:
    requirements = (
        ProfileRequirement(key="required.item", value_type="string"),
        ProfileRequirement(
            key="optional.item",
            value_type="string",
            required=False,
        ),
    )
    facts = (
        FactContent(
            key="required.item",
            value="complete value",
            provenance=Provenance.ISSO_ENTERED,
        ),
    )
    metrics = calculate_workspace_metrics(
        evidence=(),
        facts=facts,
        requirements=requirements,
        controls=(),
        questions=(),
        evidence_link_count=0,
    )
    assert metrics.total_required_items == 1
    assert metrics.satisfied_required_items == 1
    assert metrics.ssp_completion_percent == 100


def test_agent_control_blocks_approval_until_reviewed_or_grounded() -> None:
    artifact_id = uuid.uuid4()
    grounded = ControlContent(
        control_id="AC-2",
        title="Account Management",
        implementation_status="implemented",
        responsibility="system_specific",
        implementation_statement="Grounded statement.",
        state=ControlState.GENERATED,
        evidence=(EvidenceLink(artifact_id=artifact_id, locator={"page": 1}),),
    )
    ungrounded = grounded.model_copy(update={"evidence": ()})
    reviewed = ungrounded.model_copy(update={"state": ControlState.REVIEWED})

    assert not agent_control_blocks_approval(grounded)
    assert agent_control_blocks_approval(ungrounded)
    assert not agent_control_blocks_approval(
        ungrounded,
        evidence_required=False,
    )
    assert not agent_control_blocks_approval(reviewed)


def test_patch_parses_with_profile_policy_and_rejects_invalid_owner() -> None:
    policy = _narrow_policy()
    raw = json.dumps(
        {
            "schema_version": "1.0.0",
            "patches": [],
            "questions_to_add": [
                {
                    "target_type": "control",
                    "target_id": "AC-2",
                    "question": "Who owns this?",
                    "owner_type": "agency",
                }
            ],
            "question_ids_to_resolve": [],
            "change_summary": "questions",
        }
    )
    with pytest.raises(GenerationContractError, match="owner_type"):
        parse_patch_response(
            raw,
            allowed_section_ids={"purpose"},
            allowed_control_ids={"AC-2"},
            allowed_fact_ids=set(),
            allowed_question_ids=set(),
            current_revisions={},
            profile_policy=policy,
        )
