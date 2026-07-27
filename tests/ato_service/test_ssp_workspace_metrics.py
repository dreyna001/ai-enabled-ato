"""Deterministic metric tests for SSP workspaces."""

from __future__ import annotations

import uuid

import pytest

from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceLink,
    EvidenceState,
    FactContent,
    ProfileRequirement,
    Provenance,
    QuestionContent,
)
from ato_service.ssp_workspace.metrics import (
    EvidenceMetricRecord,
    calculate_workspace_metrics,
    workspace_is_reviewable,
)


def _fixture_inputs():
    evidence_id = uuid.uuid4()
    facts = (
        FactContent(
            key="system.name",
            value="Atlas",
            provenance=Provenance.ISSO_ENTERED,
        ),
        FactContent(
            key="hosting.model",
            value="agency_cloud",
            provenance=Provenance.AGENT_GENERATED,
            evidence=(
                EvidenceLink(artifact_id=evidence_id, locator={"page": 1}),
            ),
        ),
    )
    requirements = (
        ProfileRequirement(key="system.name", value_type="string"),
        ProfileRequirement(
            key="hosting.model",
            value_type="string",
            enum_values=("on_premises", "agency_cloud", "hybrid"),
        ),
        ProfileRequirement(key="authorization.boundary", value_type="string"),
    )
    controls = (
        ControlContent(
            control_id="AC-2",
            title="Account Management",
            implementation_statement="Agency identity manages accounts.",
            state=ControlState.GENERATED,
            evidence=(
                EvidenceLink(artifact_id=evidence_id, locator={"page": 2}),
            ),
        ),
        ControlContent(
            control_id="AU-2",
            title="Event Logging",
            state=ControlState.PARTIAL,
            unresolved_reason="Logging events are not identified.",
        ),
    )
    question = QuestionContent(
        question_id=uuid.uuid4(),
        question="What is the authorization boundary?",
        target_type="fact",
        target_key="authorization.boundary",
        owner_type="isso",
    )
    return facts, requirements, controls, (question,)


def test_metrics_are_counts_and_profile_coverage_not_llm_estimates() -> None:
    facts, requirements, controls, questions = _fixture_inputs()
    evidence = (
        EvidenceMetricRecord(
            state=EvidenceState.PROCESSED,
            media_type="application/pdf",
        ),
        EvidenceMetricRecord(
            state=EvidenceState.UPLOADED,
            media_type="image/png",
        ),
    )

    metrics = calculate_workspace_metrics(
        evidence=evidence,
        facts=facts,
        requirements=requirements,
        controls=controls,
        questions=questions,
        evidence_link_count=2,
    )

    assert metrics.evidence == 2
    assert metrics.processed_evidence == 1
    assert metrics.screenshots == 1
    assert metrics.selected_controls == 2
    assert metrics.controls_drafted == 1
    assert metrics.partial_controls == 1
    assert metrics.open_questions == 1
    assert metrics.satisfied_required_items == 2
    assert metrics.total_required_items == 3
    assert metrics.ssp_completion_percent == 67


def test_no_required_items_has_full_schema_coverage() -> None:
    metrics = calculate_workspace_metrics(
        evidence=(),
        facts=(),
        requirements=(),
        controls=(),
        questions=(),
        evidence_link_count=0,
    )
    assert metrics.ssp_completion_percent == 100


def test_negative_evidence_link_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_workspace_metrics(
            evidence=(),
            facts=(),
            requirements=(),
            controls=(),
            questions=(),
            evidence_link_count=-1,
        )


def test_reviewable_allows_tracked_unknowns_but_requires_terminal_jobs() -> None:
    facts, requirements, controls, questions = _fixture_inputs()

    assert workspace_is_reviewable(
        requirements=requirements,
        facts=facts,
        controls=controls,
        questions=questions,
        all_jobs_terminal=True,
        revision_saved=True,
        content_consistent=True,
    )
    assert not workspace_is_reviewable(
        requirements=requirements,
        facts=facts,
        controls=controls,
        questions=questions,
        all_jobs_terminal=False,
        revision_saved=True,
        content_consistent=True,
    )
