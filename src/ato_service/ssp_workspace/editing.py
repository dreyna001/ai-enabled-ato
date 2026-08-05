"""Pure revision transforms for generation, direct edits, and agent patches."""

from __future__ import annotations

import uuid
from typing import Mapping

from pydantic import BaseModel

from ato_service.ssp_workspace.contracts import (
    ControlState,
    EvidenceLink,
    FactContent,
    Provenance,
    QuestionContent,
    QuestionState,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.generation_contracts import (
    GenerationResult,
    PatchResult,
)

_QUESTION_NAMESPACE = uuid.UUID("c2d1fae7-5965-477f-867d-41279b8f989d")
_DIRECT_ANSWER_SECTION_KEYS = frozenset(
    {
        "system.name",
        "system.identifier",
        "system.owner",
        "system.isso",
        "system.impact_level",
    }
)


class WorkspaceEditError(ValueError):
    """Raised when a requested transform does not match the current revision."""


class WorkspaceQuestionStateError(WorkspaceEditError):
    """Raised when an answer targets a question that is no longer open."""

    error_code = "illegal_state_transition"


def merge_generation(
    content: RevisionContent,
    result: GenerationResult,
) -> RevisionContent:
    """Merge grounded generation output without overwriting human-reviewed content."""

    facts = {fact.key: fact for fact in content.facts}
    sections = {section.key: section for section in content.sections}
    controls = {control.control_id: control for control in content.controls}
    questions = {str(question.question_id): question for question in content.questions}

    for generated in result.sections:
        current = sections.get(generated.section_id)
        if current is None:
            raise WorkspaceEditError(f"unknown SSP section: {generated.section_id}")
        if current.state in {SectionState.EDITED, SectionState.REVIEWED}:
            continue
        sections[generated.section_id] = _updated(
            current,
            content=generated.content,
            state=(
                SectionState.GENERATED
                if generated.content.strip()
                else SectionState.EMPTY
            ),
            evidence=_evidence_for_facts(generated.supporting_fact_ids, facts),
        )

    for generated in result.controls:
        current = controls.get(generated.control_id)
        if current is None:
            raise WorkspaceEditError(f"unknown control: {generated.control_id}")
        if current.state is ControlState.REVIEWED:
            continue
        has_statement = bool(generated.implementation_statement.strip())
        controls[generated.control_id] = _updated(
            current,
            implementation_status=generated.implementation_status,
            responsibility=generated.responsibility,
            implementation_statement=generated.implementation_statement,
            state=ControlState.GENERATED if has_statement else ControlState.PARTIAL,
            evidence=_evidence_for_facts(generated.supporting_fact_ids, facts),
            unresolved_reason=None if has_statement else "Information is not available.",
        )

    categorization_confirmed = any(
        (
            fact.key == "system.categorization_status"
            and fact.value == "confirmed"
        )
        or fact.key in {"system.impact_level", "impact_level"}
        for fact in facts.values()
    )
    if result.categorization is not None and not categorization_confirmed:
        proposal = result.categorization
        evidence = _evidence_for_facts(proposal.supporting_fact_ids, facts)
        proposed_values = {
            "system.confidentiality_impact": proposal.confidentiality,
            "system.integrity_impact": proposal.integrity,
            "system.availability_impact": proposal.availability,
            "system.confidentiality_impact_rationale": (
                proposal.confidentiality_rationale
            ),
            "system.integrity_impact_rationale": proposal.integrity_rationale,
            "system.availability_impact_rationale": (
                proposal.availability_rationale
            ),
        }
        for key, value in proposed_values.items():
            facts[key] = FactContent(
                key=key,
                value=value,
                provenance=Provenance.AGENT_GENERATED,
                evidence=evidence,
            )

    questions = _resolve_direct_section_questions(
        questions,
        sections=sections,
    )
    existing_question_keys = {
        _question_identity(question): question for question in questions.values()
    }
    for generated in result.questions:
        if _direct_section_answer(
            target_type=generated.target_type,
            target_key=generated.target_id,
            sections=sections,
        ):
            continue
        if generated.question_key in existing_question_keys:
            continue
        question_id = uuid.uuid5(_QUESTION_NAMESPACE, generated.question_key)
        questions[str(question_id)] = QuestionContent(
            question_id=question_id,
            question=generated.question,
            target_type=generated.target_type,
            target_key=generated.target_id,
            owner_type=generated.owner_type,
            state=QuestionState.OPEN,
        )

    return RevisionContent(
        facts=tuple(facts[key] for key in sorted(facts)),
        sections=tuple(sections[key] for key in sorted(sections)),
        controls=tuple(controls[key] for key in sorted(controls)),
        questions=tuple(
            questions[key] for key in sorted(questions)
        ),
    )


def apply_agent_patch(
    content: RevisionContent,
    result: PatchResult,
    *,
    current_revision: int,
) -> RevisionContent:
    """Apply one already-validated patch result atomically to a copy."""

    facts = {fact.key: fact for fact in content.facts}
    sections = {section.key: section for section in content.sections}
    controls = {control.control_id: control for control in content.controls}
    questions = {str(question.question_id): question for question in content.questions}

    for patch in result.patches:
        if patch.expected_revision != current_revision:
            raise WorkspaceEditError("patch is stale")
        evidence = _evidence_for_facts(patch.supporting_fact_ids, facts)
        if patch.target_type == "ssp_section":
            current = sections.get(patch.target_id)
            if current is None:
                raise WorkspaceEditError(f"unknown SSP section: {patch.target_id}")
            updated_text = patch.changes.get("content", current.content)
            sections[patch.target_id] = _updated(
                current,
                content=updated_text,
                state=SectionState.GENERATED if updated_text.strip() else SectionState.EMPTY,
                evidence=evidence or current.evidence,
            )
        else:
            current = controls.get(patch.target_id)
            if current is None:
                raise WorkspaceEditError(f"unknown control: {patch.target_id}")
            statement = patch.changes.get(
                "implementation_statement",
                current.implementation_statement,
            )
            controls[patch.target_id] = _updated(
                current,
                implementation_statement=statement,
                implementation_status=patch.changes.get(
                    "implementation_status",
                    current.implementation_status,
                ),
                responsibility=patch.changes.get(
                    "responsibility",
                    current.responsibility,
                ),
                state=ControlState.GENERATED if statement.strip() else ControlState.PARTIAL,
                evidence=evidence or current.evidence,
                unresolved_reason=None if statement.strip() else current.unresolved_reason,
            )

    for question_id in result.question_ids_to_resolve:
        current = questions.get(question_id)
        if current is None:
            raise WorkspaceEditError(f"unknown question: {question_id}")
        questions[question_id] = _updated(
            current,
            state=QuestionState.DISMISSED,
            answer=current.answer,
        )

    existing_question_keys = {
        _question_identity(question): question for question in questions.values()
    }
    for generated in result.questions_to_add:
        if generated.question_key in existing_question_keys:
            continue
        question_id = uuid.uuid5(_QUESTION_NAMESPACE, generated.question_key)
        questions[str(question_id)] = QuestionContent(
            question_id=question_id,
            question=generated.question,
            target_type=generated.target_type,
            target_key=generated.target_id,
            owner_type=generated.owner_type,
        )

    return RevisionContent(
        facts=content.facts,
        sections=tuple(sections[key] for key in sorted(sections)),
        controls=tuple(controls[key] for key in sorted(controls)),
        questions=tuple(questions[key] for key in sorted(questions)),
    )


def edit_section(
    content: RevisionContent,
    *,
    section_key: str,
    text: str,
) -> RevisionContent:
    sections = list(content.sections)
    for index, section in enumerate(sections):
        if section.key == section_key:
            sections[index] = _updated(
                section,
                content=text,
                state=SectionState.EDITED if text.strip() else SectionState.EMPTY,
            )
            questions = {
                str(question.question_id): question
                for question in content.questions
            }
            questions = _resolve_direct_section_questions(
                questions,
                sections={item.key: item for item in sections},
            )
            return _updated(
                content,
                sections=tuple(sections),
                questions=tuple(
                    questions[key] for key in sorted(questions)
                ),
            )
    raise WorkspaceEditError(f"unknown SSP section: {section_key}")


def edit_control(
    content: RevisionContent,
    *,
    control_id: str,
    implementation_statement: str,
    implementation_status: str | None,
    responsibility: str | None,
) -> RevisionContent:
    controls = list(content.controls)
    for index, control in enumerate(controls):
        if control.control_id == control_id:
            controls[index] = _updated(
                control,
                implementation_statement=implementation_statement,
                implementation_status=implementation_status,
                responsibility=responsibility,
                state=(
                    ControlState.REVIEWED
                    if implementation_statement.strip()
                    else ControlState.PARTIAL
                ),
                unresolved_reason=(
                    None
                    if implementation_statement.strip()
                    else control.unresolved_reason or "Information is not available."
                ),
            )
            return _updated(content, controls=tuple(controls))
    raise WorkspaceEditError(f"unknown control: {control_id}")


def answer_question(
    content: RevisionContent,
    *,
    question_id: uuid.UUID,
    answer: str,
) -> RevisionContent:
    normalized = answer.strip()
    if not normalized:
        raise WorkspaceEditError("answer cannot be empty")
    selected = next(
        (
            question
            for question in content.questions
            if question.question_id == question_id
        ),
        None,
    )
    if selected is None:
        raise WorkspaceEditError(f"unknown question: {question_id}")
    if selected.state is not QuestionState.OPEN:
        raise WorkspaceQuestionStateError("question is not open")

    sections = list(content.sections)
    if (
        selected.target_type == "ssp_section"
        and selected.target_key in _DIRECT_ANSWER_SECTION_KEYS
    ):
        for index, section in enumerate(sections):
            if section.key == selected.target_key:
                sections[index] = _updated(
                    section,
                    content=normalized,
                    state=SectionState.EDITED,
                )
                break
        else:
            raise WorkspaceEditError(
                f"unknown SSP section: {selected.target_key}"
            )

    questions = tuple(
        _updated(
            question,
            state=QuestionState.ANSWERED,
            answer=normalized,
        )
        if question.state is QuestionState.OPEN
        and question.target_type == selected.target_type
        and question.target_key == selected.target_key
        else question
        for question in content.questions
    )
    return _updated(
        content,
        sections=tuple(sections),
        questions=questions,
    )


def _direct_section_answer(
    *,
    target_type: str,
    target_key: str,
    sections: Mapping[str, SectionContent],
) -> str | None:
    if target_type != "ssp_section" or target_key not in _DIRECT_ANSWER_SECTION_KEYS:
        return None
    section = sections.get(target_key)
    if section is None:
        return None
    content = section.content.strip()
    return content or None


def _resolve_direct_section_questions(
    questions: Mapping[str, QuestionContent],
    *,
    sections: Mapping[str, SectionContent],
) -> dict[str, QuestionContent]:
    resolved: dict[str, QuestionContent] = {}
    for key, question in questions.items():
        answer = _direct_section_answer(
            target_type=question.target_type,
            target_key=question.target_key,
            sections=sections,
        )
        resolved[key] = (
            _updated(
                question,
                state=QuestionState.ANSWERED,
                answer=answer,
            )
            if question.state is QuestionState.OPEN and answer
            else question
        )
    return resolved


def _evidence_for_facts(
    fact_ids: tuple[str, ...],
    facts: Mapping[str, object],
) -> tuple[EvidenceLink, ...]:
    links: dict[tuple[uuid.UUID, str], EvidenceLink] = {}
    for fact_id in fact_ids:
        fact = facts.get(fact_id)
        if fact is None:
            raise WorkspaceEditError(f"unknown supporting fact: {fact_id}")
        for link in fact.evidence:  # type: ignore[attr-defined]
            key = (link.artifact_id, repr(sorted(link.locator.items())))
            links[key] = link
    return tuple(links[key] for key in sorted(links, key=lambda item: (str(item[0]), item[1])))


def _question_identity(question: QuestionContent) -> str:
    from ato_service.ssp_workspace.generation_contracts import deterministic_question_key

    return deterministic_question_key(
        target_type=question.target_type,
        target_id=question.target_key,
        question=question.question,
    )


def _updated[ModelT: BaseModel](model: ModelT, **changes: object) -> ModelT:
    payload = model.model_dump(mode="python")
    payload.update(changes)
    return type(model).model_validate(payload)
