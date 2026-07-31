from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from ato_service.ssp_workspace.generation import (
    ContextualEditRequest,
    ControlState,
    EvidenceFact,
    InitialGenerationRequest,
    ModelPrompt,
    OpenQuestionState,
    SspGenerationError,
    SspSectionState,
    generate_contextual_patch,
    generate_initial_ssp,
)
from ato_service.ssp_workspace.profile_bundles import (
    ImplementationStatementAgentInstructions,
    ImplementationStatementPolicy,
    ProfileControl,
    default_implementation_statement_deterministic_policy,
    default_implementation_statement_policy,
    load_profile_bundle,
    resolve_profile,
)
from ato_service.ssp_workspace.generation_contracts import (
    SelectedProfilePolicy,
    requirement_text_has_unresolved_organization_parameters,
)

_PROFILE_PATH = (
    Path(__file__).parents[2]
    / "reference"
    / "ssp_profiles"
    / "synthetic-fisma-rev5-1.0.0"
)


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _profile():
    return resolve_profile(load_profile_bundle(_PROFILE_PATH), "low")


def _initial_request() -> InitialGenerationRequest:
    return InitialGenerationRequest(
        system_name="Synthetic System",
        profile=_profile(),
        source_ids=("source-1",),
        facts=(
            EvidenceFact(
                fact_id="fact-1",
                source_id="source-1",
                text="The system is hosted in an agency data center.",
            ),
        ),
    )


def _profile_with_statement_policy(
    agent_instructions: ImplementationStatementAgentInstructions,
) -> object:
    profile = _profile()
    policy = ImplementationStatementPolicy(
        policy_version="test-synthetic",
        deterministic=default_implementation_statement_deterministic_policy(),
        agent_instructions=agent_instructions,
        authority_refs=(),
    )
    return replace(profile, implementation_statement_policy=policy)


def _valid_generation_payload() -> dict[str, object]:
    profile = _profile()
    section_id = profile.ssp_required_items[0].item_id
    control_id = profile.controls[0].control_id
    return {
        "schema_version": "1.0.0",
        "sections": [
            {
                "section_id": section_id,
                "content": "The system is hosted in an agency data center.",
                "supporting_fact_ids": ["fact-1"],
            }
        ],
        "controls": [
            {
                "control_id": control_id,
                "implementation_status": "unknown",
                "responsibility": "unknown",
                "implementation_statement": "",
                "supporting_fact_ids": [],
            }
        ],
        "questions": [
            {
                "target_type": "control",
                "target_id": control_id,
                "question": "Who implements this control?",
                "owner_type": "technical",
            }
        ],
    }


def test_initial_generation_builds_grounded_prompt_and_accepts_valid_output() -> None:
    prompts: list[ModelPrompt] = []

    async def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return json.dumps(_valid_generation_payload())

    result = _run(generate_initial_ssp(_initial_request(), model))

    assert result.attempts == 1
    assert result.repair_attempted is False


def test_initial_generation_prompt_uses_profile_control_and_section_policy() -> None:
    import json as json_module

    prompts: list[ModelPrompt] = []

    async def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return json_module.dumps(_valid_generation_payload())

    _run(generate_initial_ssp(_initial_request(), model))

    payload = json_module.loads(prompts[0].user)
    section = payload["ssp_sections"][0]
    assert "required" in section
    assert "min_length" in section
    assert "allowed_values" in section
    assert "standard_refs" in section
    assert "control_response_policy" in payload
    contract = payload["output_contract"]
    assert isinstance(contract["controls"][0]["implementation_status"], list)
    assert isinstance(contract["questions"][0]["owner_type"], list)


def test_unconfirmed_generation_returns_grounded_categorization_proposal() -> None:
    prompts: list[ModelPrompt] = []
    payload = _valid_generation_payload()
    payload["categorization"] = {
        "confidentiality": "moderate",
        "integrity": "moderate",
        "availability": "low",
        "confidentiality_rationale": "Disclosure could cause serious harm.",
        "integrity_rationale": "Incorrect records could cause serious harm.",
        "availability_rationale": "Short outages can be handled manually.",
        "supporting_fact_ids": ["fact-1"],
    }

    def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return json.dumps(payload)

    result = _run(
        generate_initial_ssp(
            replace(_initial_request(), categorization_confirmed=False),
            model,
        )
    )

    prompt = json.loads(prompts[0].user)
    assert prompt["profile"]["system_categorization_status"] == "unconfirmed"
    assert result.value.categorization.confidentiality == "moderate"
    assert result.value.sections[0].supporting_fact_ids == ("fact-1",)
    prompt_payload = json.loads(prompts[0].user)
    assert prompt_payload["evidence_facts"] == [
        {
            "fact_id": "fact-1",
            "source_id": "source-1",
            "text": "The system is hosted in an agency data center.",
        }
    ]
    assert "Never invent" in prompts[0].system


def test_initial_generation_repairs_one_schema_failure() -> None:
    responses = iter(("not-json", json.dumps(_valid_generation_payload())))
    prompts: list[ModelPrompt] = []

    def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return next(responses)

    result = _run(generate_initial_ssp(_initial_request(), model))

    assert result.attempts == 2
    assert result.repair_attempted is True
    repair_payload = json.loads(prompts[1].user)
    assert repair_payload["invalid_response"] == "not-json"
    assert "validation_error" in repair_payload


def test_initial_generation_supplies_omitted_envelope_boilerplate() -> None:
    payload = _valid_generation_payload()
    payload.pop("schema_version")
    payload.pop("questions")

    result = _run(
        generate_initial_ssp(
            _initial_request(),
            lambda _: json.dumps(payload),
        )
    )

    assert result.attempts == 1
    assert result.repair_attempted is False
    assert result.value.sections
    assert result.value.controls
    assert result.value.questions == ()


def test_initial_generation_normalizes_string_list_section_content() -> None:
    payload = _valid_generation_payload()
    payload["sections"][0]["content"] = [  # type: ignore[index]
        "PostgreSQL database",
        "Application service",
    ]

    result = _run(
        generate_initial_ssp(
            _initial_request(),
            lambda _: json.dumps(payload),
        )
    )

    assert result.value.sections[0].content == (
        "- PostgreSQL database\n- Application service"
    )


def test_unknown_grounding_id_fails_closed_without_repair() -> None:
    payload = _valid_generation_payload()
    payload["sections"][0]["supporting_fact_ids"] = ["fabricated-fact"]  # type: ignore[index]
    call_count = 0

    async def model(_: ModelPrompt) -> str:
        nonlocal call_count
        call_count += 1
        return json.dumps(payload)

    with pytest.raises(SspGenerationError) as caught:
        _run(generate_initial_ssp(_initial_request(), model))

    assert caught.value.failure_kind == "source_binding"
    assert caught.value.attempts == 1
    assert caught.value.repair_attempted is False
    assert call_count == 1


def test_two_malformed_responses_fail_deterministically() -> None:
    async def model(_: ModelPrompt) -> str:
        return "{"

    with pytest.raises(SspGenerationError) as caught:
        _run(generate_initial_ssp(_initial_request(), model))

    assert caught.value.failure_kind == "parse"
    assert caught.value.attempts == 2
    assert caught.value.repair_attempted is True
    assert caught.value.last_raw_response == "{"


def test_input_fact_must_reference_an_allowed_source() -> None:
    request = _initial_request()
    bad_request = InitialGenerationRequest(
        system_name=request.system_name,
        profile=request.profile,
        source_ids=request.source_ids,
        facts=(
            EvidenceFact(
                fact_id="fact-1",
                source_id="unknown-source",
                text="Unsupported.",
            ),
        ),
    )

    with pytest.raises(ValueError, match="unknown source_id"):
        _run(generate_initial_ssp(bad_request, lambda _: "{}"))


def test_contextual_patch_uses_current_revisions_and_question_allowlist() -> None:
    profile = _profile()
    section_id = profile.ssp_required_items[0].item_id
    request = ContextualEditRequest(
        system_name="Synthetic System",
        profile=profile,
        source_ids=("source-1",),
        facts=(
            EvidenceFact(
                fact_id="fact-1",
                source_id="source-1",
                text="The system owner confirmed the system purpose.",
            ),
        ),
        sections=tuple(
            SspSectionState(
                section_id=item.item_id,
                revision=3 if item.item_id == section_id else 1,
                content="",
            )
            for item in profile.ssp_required_items
        ),
        controls=tuple(
            ControlState(
                control_id=control.control_id,
                revision=1,
                implementation_status="unknown",
                responsibility="unknown",
                implementation_statement="",
            )
            for control in profile.controls
        ),
        open_questions=(
            OpenQuestionState(
                question_id="question-1",
                target_type="ssp_section",
                target_id=section_id,
                question="What is the system purpose?",
            ),
        ),
        instruction="Apply the confirmed system purpose.",
    )
    response = {
        "schema_version": "1.0.0",
        "patches": [
            {
                "target_type": "ssp_section",
                "target_id": section_id,
                "expected_revision": 3,
                "changes": {"content": "The system purpose was confirmed."},
                "supporting_fact_ids": ["fact-1"],
            }
        ],
        "questions_to_add": [],
        "question_ids_to_resolve": ["question-1"],
        "change_summary": "Updated the confirmed system purpose.",
    }
    prompts: list[ModelPrompt] = []

    def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return json.dumps(response)

    result = _run(generate_contextual_patch(request, model))

    assert result.value.patches[0].expected_revision == 3
    assert result.value.question_ids_to_resolve == ("question-1",)
    current_sections = json.loads(prompts[0].user)["current_sections"]
    assert (
        next(item for item in current_sections if item["section_id"] == section_id)[
            "revision"
        ]
        == 3
    )


def test_contextual_patch_prompt_includes_profile_implementation_rules() -> None:
    profile = _profile()
    request = ContextualEditRequest(
        system_name="Synthetic System",
        profile=profile,
        source_ids=("source-1",),
        facts=(
            EvidenceFact(
                fact_id="fact-1",
                source_id="source-1",
                text="Shared identity provider handles authentication.",
            ),
        ),
        sections=tuple(
            SspSectionState(
                section_id=item.item_id,
                revision=1,
                content="",
            )
            for item in profile.ssp_required_items
        ),
        controls=tuple(
            ControlState(
                control_id=control.control_id,
                revision=1,
                implementation_status="unknown",
                responsibility="unknown",
                implementation_statement="",
            )
            for control in profile.controls
        ),
        open_questions=(),
        instruction="Clarify inherited authentication.",
    )
    prompts: list[ModelPrompt] = []

    def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return json.dumps(
            {
                "schema_version": "1.0.0",
                "patches": [],
                "questions_to_add": [],
                "question_ids_to_resolve": [],
                "change_summary": "No changes.",
            }
        )

    _run(generate_contextual_patch(request, model))

    payload = json.loads(prompts[0].user)
    rules = payload["control_implementation_rules"]
    default_policy = default_implementation_statement_policy()
    assert rules["organization_defined_parameters"] == list(
        default_policy.agent_instructions.organization_defined_parameters
    )
    assert rules["inherited_and_hybrid_responsibility"] == list(
        default_policy.agent_instructions.inherited_and_hybrid_responsibility
    )
    assert rules["statement_content"] == []
    assert rules["semantic_review"] == []
    assert "control_implementation_rules for statement_content" in payload["task"]


def test_contextual_patch_supplies_omitted_envelope_boilerplate() -> None:
    profile = _profile()
    section_id = profile.ssp_required_items[0].item_id
    request = ContextualEditRequest(
        system_name="Synthetic System",
        profile=profile,
        source_ids=("source-1",),
        facts=(
            EvidenceFact(
                fact_id="fact-1",
                source_id="source-1",
                text="The system owner confirmed the system purpose.",
            ),
        ),
        sections=tuple(
            SspSectionState(
                section_id=item.item_id,
                revision=2,
                content="",
            )
            for item in profile.ssp_required_items
        ),
        controls=tuple(
            ControlState(
                control_id=control.control_id,
                revision=2,
                implementation_status="unknown",
                responsibility="unknown",
                implementation_statement="",
            )
            for control in profile.controls
        ),
        open_questions=(),
        instruction="Apply the confirmed system purpose.",
    )
    response = {
        "patches": [
            {
                "target_type": "ssp_section",
                "target_id": section_id,
                "expected_revision": 2,
                "changes": {"content": "The system purpose was confirmed."},
                "supporting_fact_ids": ["fact-1"],
            }
        ],
        "change_summary": "Updated the confirmed system purpose.",
    }

    result = _run(generate_contextual_patch(request, lambda _: json.dumps(response)))

    assert result.attempts == 1
    assert result.repair_attempted is False
    assert result.value.patches[0].target_id == section_id
    assert result.value.questions_to_add == ()
    assert result.value.question_ids_to_resolve == ()


def test_contextual_patch_rejects_incomplete_current_state_before_model_call() -> None:
    profile = _profile()
    called = False
    request = ContextualEditRequest(
        system_name="Synthetic System",
        profile=profile,
        source_ids=(),
        facts=(),
        sections=(),
        controls=(),
        open_questions=(),
        instruction="Update the draft.",
    )

    def model(_: ModelPrompt) -> str:
        nonlocal called
        called = True
        return "{}"

    with pytest.raises(ValueError, match="current sections"):
        _run(generate_contextual_patch(request, model))

    assert called is False


def test_common_prompt_marks_odp_controls_from_requirement_text() -> None:
    profile = _profile()
    first = profile.controls[0]
    odp_control = ProfileControl(
        control_id=first.control_id,
        title=first.title,
        requirement_text=(
            "Retain audit records for {{ insert: param, au-11_odp }} as required."
        ),
        catalog_pointer=first.catalog_pointer,
    )
    profile_with_odp = replace(profile, controls=(odp_control, *profile.controls[1:]))
    request = replace(_initial_request(), profile=profile_with_odp)
    prompts: list[ModelPrompt] = []

    def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return json.dumps(_valid_generation_payload())

    _run(generate_initial_ssp(request, model))

    payload = json.loads(prompts[0].user)
    flagged = next(
        item for item in payload["controls"] if item["control_id"] == first.control_id
    )
    assert flagged["has_unresolved_organization_parameters"] is True
    assert requirement_text_has_unresolved_organization_parameters(
        flagged["requirement_text"]
    )


def test_initial_generation_prompt_includes_odp_and_inheritance_rules() -> None:
    prompts: list[ModelPrompt] = []

    def model(prompt: ModelPrompt) -> str:
        prompts.append(prompt)
        return json.dumps(_valid_generation_payload())

    _run(generate_initial_ssp(_initial_request(), model))

    payload = json.loads(prompts[0].user)
    rules = payload["control_implementation_rules"]
    default_policy = default_implementation_statement_policy()
    assert rules["organization_defined_parameters"] == list(
        default_policy.agent_instructions.organization_defined_parameters
    )
    assert rules["inherited_and_hybrid_responsibility"] == list(
        default_policy.agent_instructions.inherited_and_hybrid_responsibility
    )
    assert rules["statement_content"] == []
    assert rules["semantic_review"] == []
    assert "control_implementation_rules for statement_content" in payload["task"]
    assert "has_unresolved_organization_parameters=true" in payload["task"]
    assert "organization-defined parameter placeholder syntax" not in prompts[0].system


def test_custom_implementation_statement_policy_reaches_initial_and_patch_prompts() -> (
    None
):
    marker = "SYNTHETIC-CUSTOM-STATEMENT-POLICY-BYTES"
    custom_instructions = ImplementationStatementAgentInstructions(
        statement_content=(f"{marker}-statement",),
        organization_defined_parameters=(f"{marker}-odp",),
        inherited_and_hybrid_responsibility=(f"{marker}-inherit",),
        semantic_review=(f"{marker}-semantic",),
    )
    profile = _profile_with_statement_policy(custom_instructions)
    initial_request = replace(_initial_request(), profile=profile)
    patch_request = ContextualEditRequest(
        system_name="Synthetic System",
        profile=profile,
        source_ids=("source-1",),
        facts=_initial_request().facts,
        sections=tuple(
            SspSectionState(
                section_id=item.item_id,
                revision=1,
                content="",
            )
            for item in profile.ssp_required_items
        ),
        controls=tuple(
            ControlState(
                control_id=control.control_id,
                revision=1,
                implementation_status="unknown",
                responsibility="unknown",
                implementation_statement="",
            )
            for control in profile.controls
        ),
        open_questions=(),
        instruction="Apply evidence.",
    )
    captured: list[ModelPrompt] = []

    def model(prompt: ModelPrompt) -> str:
        captured.append(prompt)
        if len(captured) == 1:
            return json.dumps(_valid_generation_payload())
        return json.dumps(
            {
                "schema_version": "1.0.0",
                "patches": [],
                "questions_to_add": [],
                "question_ids_to_resolve": [],
                "change_summary": "No changes.",
            }
        )

    _run(generate_initial_ssp(initial_request, model))
    _run(generate_contextual_patch(patch_request, model))

    for prompt in captured:
        rules = json.loads(prompt.user)["control_implementation_rules"]
        assert rules["statement_content"] == [f"{marker}-statement"]
        assert rules["organization_defined_parameters"] == [f"{marker}-odp"]
        assert rules["inherited_and_hybrid_responsibility"] == [f"{marker}-inherit"]
        assert rules["semantic_review"] == [f"{marker}-semantic"]
        assert (
            "Never invent values for organization-defined parameters"
            not in json.dumps(rules)
        )


def test_selected_profile_policy_derives_parameterized_controls_from_profile() -> None:
    profile = _profile()
    first = profile.controls[0]
    odp_control = ProfileControl(
        control_id=first.control_id,
        title=first.title,
        requirement_text="Policy for {{ insert: param, ac-1_prm_1 }}.",
        catalog_pointer=first.catalog_pointer,
    )
    profile_with_odp = replace(profile, controls=(odp_control, *profile.controls[1:]))
    policy = SelectedProfilePolicy.from_resolved(profile_with_odp)

    assert first.control_id in policy.parameterized_control_ids


def test_selected_profile_policy_legacy_defaults_empty_parameterized_set() -> None:
    policy = SelectedProfilePolicy.from_resolved(_profile())

    assert policy.parameterized_control_ids == frozenset()
