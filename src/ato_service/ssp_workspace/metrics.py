"""Deterministic workflow metrics calculated from persisted workspace records."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceState,
    FactContent,
    ProfileRequirement,
    Provenance,
    QuestionContent,
    QuestionState,
)

SUPPORTED_SCREENSHOT_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/tiff", "image/webp"}
)


@dataclass(frozen=True, slots=True)
class EvidenceMetricRecord:
    state: EvidenceState
    media_type: str


@dataclass(frozen=True, slots=True)
class WorkspaceMetrics:
    evidence: int
    processed_evidence: int
    screenshots: int
    selected_controls: int
    controls_drafted: int
    partial_controls: int
    open_questions: int
    evidence_links: int
    satisfied_required_items: int
    total_required_items: int
    ssp_completion_percent: int


def requirement_is_satisfied(
    requirement: ProfileRequirement,
    fact: FactContent | None,
) -> bool:
    """Evaluate one profile requirement without model judgment."""

    if fact is None or fact.state.value != "active":
        return False
    if not _value_matches(requirement, fact.value):
        return False
    return not (
        requirement.evidence_required_for_agent_value
        and fact.provenance is Provenance.AGENT_GENERATED
        and not fact.evidence
    )


def _value_matches(requirement: ProfileRequirement, value: Any) -> bool:
    value_type = requirement.value_type
    if value_type == "string":
        if not isinstance(value, str) or len(value.strip()) < requirement.min_length:
            return False
        return not requirement.enum_values or value in requirement.enum_values
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "array":
        return isinstance(value, list) and len(value) >= requirement.min_length
    if value_type == "object":
        return isinstance(value, dict) and len(value) >= requirement.min_length
    return False


def calculate_workspace_metrics(
    *,
    evidence: Iterable[EvidenceMetricRecord],
    facts: Iterable[FactContent],
    requirements: Iterable[ProfileRequirement],
    controls: Iterable[ControlContent],
    questions: Iterable[QuestionContent],
    evidence_link_count: int,
) -> WorkspaceMetrics:
    """Calculate all count and profile-coverage metrics from fixed inputs."""

    if evidence_link_count < 0:
        raise ValueError("evidence_link_count cannot be negative")
    evidence_rows = tuple(evidence)
    fact_by_key: Mapping[str, FactContent] = {fact.key: fact for fact in facts}
    required = tuple(item for item in requirements if item.required)
    control_rows = tuple(controls)
    question_rows = tuple(questions)
    satisfied = sum(
        requirement_is_satisfied(item, fact_by_key.get(item.key)) for item in required
    )
    total = len(required)
    completion = round(100 * satisfied / total) if total else 100
    drafted = sum(
        bool(control.implementation_statement.strip())
        and control.state
        in {ControlState.GENERATED, ControlState.PARTIAL, ControlState.REVIEWED}
        for control in control_rows
    )
    partial = sum(
        control.state in {ControlState.EMPTY, ControlState.PARTIAL}
        or not control.implementation_statement.strip()
        or not control.evidence
        for control in control_rows
    )
    return WorkspaceMetrics(
        evidence=len(evidence_rows),
        processed_evidence=sum(
            item.state is EvidenceState.PROCESSED for item in evidence_rows
        ),
        screenshots=sum(
            item.media_type.lower() in SUPPORTED_SCREENSHOT_MEDIA_TYPES
            for item in evidence_rows
        ),
        selected_controls=len(control_rows),
        controls_drafted=drafted,
        partial_controls=partial,
        open_questions=sum(
            item.state is QuestionState.OPEN for item in question_rows
        ),
        evidence_links=evidence_link_count,
        satisfied_required_items=satisfied,
        total_required_items=total,
        ssp_completion_percent=completion,
    )


def workspace_is_reviewable(
    *,
    requirements: Iterable[ProfileRequirement],
    facts: Iterable[FactContent],
    controls: Iterable[ControlContent],
    questions: Iterable[QuestionContent],
    all_jobs_terminal: bool,
    revision_saved: bool,
    content_consistent: bool,
) -> bool:
    """Apply the product's deterministic reviewability gate."""

    if not (all_jobs_terminal and revision_saved and content_consistent):
        return False
    fact_by_key = {fact.key: fact for fact in facts}
    open_targets = {
        (question.target_type, question.target_key)
        for question in questions
        if question.state is QuestionState.OPEN
    }
    for requirement in requirements:
        if (
            requirement.required
            and not requirement_is_satisfied(
                requirement, fact_by_key.get(requirement.key)
            )
            and ("fact", requirement.key) not in open_targets
        ):
            return False
    for control in controls:
        if not control.implementation_statement.strip() and not (
            control.unresolved_reason
            or ("control", control.control_id) in open_targets
        ):
            return False
    return True
