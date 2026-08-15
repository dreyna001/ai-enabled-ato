"""Tests for architecture diagram analysis and system definition proposals."""

from __future__ import annotations

import json
import uuid

import pytest

from ato_service.ssp_workspace.contracts import (
    EvidenceLink,
    FactContent,
    Provenance,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.diagram_analysis import (
    DiagramAnalysisResult,
    DiagramComponent,
    DiagramConflict,
    DiagramInterconnection,
    _parse_analysis_response,
    apply_system_definition_proposal,
    build_system_definition_from_analysis,
    collect_text_evidence_context,
)
from ato_service.ssp_workspace.system_definition import active_system_definition_status


def test_parse_analysis_response_accepts_minimal_contract() -> None:
    payload = {
        "schema_version": "1.0.0",
        "boundary_narrative": "The authorization boundary includes all production hosts.",
        "components": [
            {
                "name": "Web tier",
                "purpose": "Public UI",
                "placement": "inside",
            }
        ],
        "interconnections": [
            {
                "connected_organization": "Agency IAM",
                "connected_system": "Identity Provider",
                "direction": "outbound",
                "data_types": ["authentication tokens"],
                "interface_protocol": "HTTPS",
            }
        ],
        "conflicts": [],
    }
    parsed = _parse_analysis_response(json.dumps(payload))
    assert parsed.boundary_narrative.startswith("The authorization boundary")
    assert parsed.components[0].name == "Web tier"
    assert parsed.interconnections[0].connected_system == "Identity Provider"


def test_collect_text_evidence_context_includes_matching_facts() -> None:
    artifact_id = uuid.uuid4()
    content = RevisionContent(
        facts=(
            FactContent(
                key=f"evidence.{artifact_id}.0",
                value="Diagram shows three tiers.",
                provenance=Provenance.EXTRACTED,
                evidence=(
                    EvidenceLink(
                        artifact_id=artifact_id,
                        locator={"page": 1},
                    ),
                ),
            ),
        ),
        sections=(
            SectionContent(
                key="system.purpose",
                title="Purpose",
                content="Mission support application",
                state=SectionState.EDITED,
            ),
        ),
    )
    snippets = collect_text_evidence_context(content, artifact_id=artifact_id)
    assert "Diagram shows three tiers." in snippets
    assert "Mission support application" in snippets


def test_build_system_definition_from_analysis_maps_domain_types() -> None:
    artifact_id = uuid.uuid4()
    analysis = DiagramAnalysisResult(
        boundary_narrative="Boundary includes application subnet and database subnet.",
        components=(
            DiagramComponent(
                component_id="web",
                name="Web tier",
                purpose="UI",
                placement="inside",
                source="diagram",
                confidence="high",
            ),
        ),
        interconnections=(
            DiagramInterconnection(
                interconnection_id="idp",
                connected_organization="Agency IAM",
                connected_system="Identity Provider",
                direction="outbound",
                data_types=("authentication tokens",),
                interface_protocol="HTTPS",
                source="diagram",
                confidence="medium",
            ),
        ),
        conflicts=(
            DiagramConflict(
                field="component count",
                diagram_value="three tiers",
                text_value="two tiers",
                note="Review diagram against narrative.",
            ),
        ),
        attempts=1,
        repair_attempted=False,
    )
    boundary, components, interconnections = build_system_definition_from_analysis(
        analysis,
        artifact_id=artifact_id,
        locator={"page": 1},
        diagram_label="architecture.png",
    )
    assert boundary.diagram_links[0].artifact_id == artifact_id
    assert components[0].name == "Web tier"
    assert interconnections[0].connected_system == "Identity Provider"


def test_apply_system_definition_proposal_sets_unconfirmed_status() -> None:
    content = RevisionContent(
        sections=(
            SectionContent(
                key="system.authorization_boundary",
                title="Boundary",
                content="",
                state=SectionState.EMPTY,
            ),
            SectionContent(
                key="system.components",
                title="Components",
                content="",
                state=SectionState.EMPTY,
            ),
            SectionContent(
                key="system.interconnections",
                title="Interconnections",
                content="",
                state=SectionState.EMPTY,
            ),
            SectionContent(
                key="system.diagram_references",
                title="Diagram References",
                content="",
                state=SectionState.EMPTY,
            ),
        )
    )
    artifact_id = uuid.uuid4()
    analysis = DiagramAnalysisResult(
        boundary_narrative="Boundary includes all managed production components.",
        components=(
            DiagramComponent(
                component_id="app",
                name="Application tier",
                purpose="Business logic",
                placement="inside",
                source="diagram",
                confidence="high",
            ),
        ),
        interconnections=(
            DiagramInterconnection(
                interconnection_id="ext",
                connected_organization="External agency",
                connected_system="Shared logging",
                direction="outbound",
                data_types=("audit events",),
                interface_protocol="TLS",
                source="diagram",
                confidence="low",
            ),
        ),
        conflicts=(),
        attempts=1,
        repair_attempted=False,
    )
    boundary, components, interconnections = build_system_definition_from_analysis(
        analysis,
        artifact_id=artifact_id,
        locator={"page": 1},
    )
    updated = apply_system_definition_proposal(
        content,
        boundary=boundary,
        components=components,
        interconnections=interconnections,
        proposal_metadata={"source": "diagram_analysis"},
    )
    facts = {fact.key: fact for fact in updated.facts}
    assert active_system_definition_status(facts) == "unconfirmed"
    assert "system.system_definition_proposal" in facts


def test_parse_analysis_response_rejects_short_boundary() -> None:
    payload = {
        "schema_version": "1.0.0",
        "boundary_narrative": "too short",
        "components": [],
        "interconnections": [],
        "conflicts": [],
    }
    with pytest.raises(Exception, match="length"):
        _parse_analysis_response(json.dumps(payload))
