from __future__ import annotations

import asyncio
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
    load_profile_bundle,
    resolve_profile,
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
    assert next(
        item for item in current_sections if item["section_id"] == section_id
    )["revision"] == 3


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
