from __future__ import annotations

import uuid

import pytest

from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceLink,
    FactContent,
    Provenance,
    QuestionState,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.editing import (
    WorkspaceEditError,
    answer_question,
    edit_control,
    edit_section,
    merge_generation,
)
from ato_service.ssp_workspace.generation_contracts import (
    GeneratedControl,
    GeneratedCategorization,
    GeneratedQuestion,
    GeneratedSection,
    GenerationResult,
    ProfileValidationError,
    validate_workspace_implementation_statement,
)


ARTIFACT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _content() -> RevisionContent:
    return RevisionContent(
        facts=(
            FactContent(
                key="system.purpose",
                value="Provides internal case management.",
                provenance=Provenance.EXTRACTED,
                evidence=(
                    EvidenceLink(
                        artifact_id=ARTIFACT_ID,
                        locator={"page": 1},
                    ),
                ),
            ),
        ),
        sections=(
            SectionContent(
                key="system_description",
                title="System Description",
                content="",
                state=SectionState.EMPTY,
            ),
        ),
        controls=(
            ControlContent(
                control_id="AC-1",
                title="Policy and Procedures",
                state=ControlState.EMPTY,
            ),
        ),
    )


def _owner_content() -> RevisionContent:
    return RevisionContent(
        facts=(
            FactContent(
                key="system.owner",
                value="Dana Holloway",
                provenance=Provenance.EXTRACTED,
                evidence=(
                    EvidenceLink(
                        artifact_id=ARTIFACT_ID,
                        locator={"page": 1},
                    ),
                ),
            ),
        ),
        sections=(
            SectionContent(
                key="system.owner",
                title="System Owner",
                content="",
                state=SectionState.EMPTY,
            ),
        ),
    )


def test_generation_populates_objects_and_deduplicates_questions() -> None:
    result = GenerationResult(
        sections=(
            GeneratedSection(
                section_id="system_description",
                content="The system provides internal case management.",
                supporting_fact_ids=("system.purpose",),
            ),
        ),
        controls=(
            GeneratedControl(
                control_id="AC-1",
                implementation_status="implemented",
                responsibility="system_specific",
                implementation_statement="The agency maintains the policy.",
                supporting_fact_ids=("system.purpose",),
            ),
        ),
        questions=(
            GeneratedQuestion(
                question_key="q_stable",
                target_type="control",
                target_id="AC-1",
                question="Who reviews the policy?",
                owner_type="isso",
            ),
        ),
    )

    first = merge_generation(_content(), result)
    second = merge_generation(first, result)

    assert first.sections[0].state is SectionState.GENERATED
    assert first.controls[0].state is ControlState.GENERATED
    assert len(first.questions) == 1
    assert len(second.questions) == 1


def test_generation_stores_grounded_categorization_as_unconfirmed_proposal() -> None:
    result = GenerationResult(
        sections=(),
        controls=(),
        questions=(),
        categorization=GeneratedCategorization(
            confidentiality="moderate",
            integrity="moderate",
            availability="low",
            confidentiality_rationale="Disclosure could cause serious harm.",
            integrity_rationale="Incorrect records could cause serious harm.",
            availability_rationale="Short outages can be handled manually.",
            supporting_fact_ids=("system.purpose",),
        ),
    )

    updated = merge_generation(_content(), result)
    facts = {fact.key: fact for fact in updated.facts}

    assert facts["system.confidentiality_impact"].value == "moderate"
    assert (
        facts["system.confidentiality_impact"].provenance
        is Provenance.AGENT_GENERATED
    )
    assert facts["system.confidentiality_impact"].evidence[0].artifact_id == ARTIFACT_ID
    assert "system.impact_level" not in facts


def test_generation_does_not_leave_owner_question_when_owner_is_populated() -> None:
    result = GenerationResult(
        sections=(
            GeneratedSection(
                section_id="system.owner",
                content="Dana Holloway, Director",
                supporting_fact_ids=("system.owner",),
            ),
        ),
        controls=(),
        questions=(
            GeneratedQuestion(
                question_key="owner_question",
                target_type="ssp_section",
                target_id="system.owner",
                question="Who is the current system owner?",
                owner_type="system_owner",
            ),
        ),
    )

    updated = merge_generation(_owner_content(), result)

    assert updated.sections[0].content == "Dana Holloway, Director"
    assert updated.questions == ()


def test_owner_answer_updates_section_and_resolves_duplicate_questions() -> None:
    result = GenerationResult(
        sections=(),
        controls=(),
        questions=(
            GeneratedQuestion(
                question_key="owner_current",
                target_type="ssp_section",
                target_id="system.owner",
                question="Who is the current system owner?",
                owner_type="system_owner",
            ),
            GeneratedQuestion(
                question_key="owner_official",
                target_type="ssp_section",
                target_id="system.owner",
                question="Who is the official system owner?",
                owner_type="system_owner",
            ),
        ),
    )
    content = merge_generation(_owner_content(), result)

    answered = answer_question(
        content,
        question_id=content.questions[0].question_id,
        answer="Dana Holloway, Director, Office of Grants Operations",
    )

    assert answered.sections[0].content == (
        "Dana Holloway, Director, Office of Grants Operations"
    )
    assert answered.sections[0].state is SectionState.EDITED
    assert all(
        question.state is QuestionState.ANSWERED
        for question in answered.questions
    )


def test_manual_section_is_not_overwritten_by_generation() -> None:
    content = edit_section(
        _content(),
        section_key="system_description",
        text="ISSO-authored description.",
    )
    result = GenerationResult(
        sections=(
            GeneratedSection(
                section_id="system_description",
                content="Agent text.",
                supporting_fact_ids=("system.purpose",),
            ),
        ),
        controls=(),
        questions=(),
    )

    updated = merge_generation(content, result)

    assert updated.sections[0].content == "ISSO-authored description."
    assert updated.sections[0].state is SectionState.EDITED


def test_direct_control_edit_and_question_answer() -> None:
    result = GenerationResult(
        sections=(),
        controls=(),
        questions=(
            GeneratedQuestion(
                question_key="q_one",
                target_type="control",
                target_id="AC-1",
                question="Who owns this control?",
                owner_type="isso",
            ),
        ),
    )
    content = merge_generation(_content(), result)
    edited = edit_control(
        content,
        control_id="AC-1",
        implementation_statement="The ISSO owns this control.",
        implementation_status="implemented",
        responsibility="system_specific",
    )
    answered = answer_question(
        edited,
        question_id=edited.questions[0].question_id,
        answer="The ISSO.",
    )

    assert edited.controls[0].state is ControlState.REVIEWED
    assert answered.questions[0].state is QuestionState.ANSWERED


def test_direct_control_edit_rejects_oscal_parameter_placeholder_syntax() -> None:
    with pytest.raises(ProfileValidationError, match="placeholder syntax"):
        validate_workspace_implementation_statement(
            "The agency policy uses {{ insert: param, ac-1_prm_1 }}."
        )


def test_unknown_target_fails_without_mutating_input() -> None:
    content = _content()
    with pytest.raises(WorkspaceEditError, match="unknown SSP section"):
        edit_section(content, section_key="missing", text="text")
    assert content.sections[0].content == ""
