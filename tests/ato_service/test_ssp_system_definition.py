"""Tests for structured system definition (boundary, components, interconnections)."""

from __future__ import annotations

import json
import uuid

import pytest

from ato_service.ssp_workspace.contracts import (
    FactContent,
    FactState,
    Provenance,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.editing import edit_section
from ato_service.ssp_workspace.system_definition import (
    AuthorizationBoundary,
    DiagramLink,
    Interconnection,
    SystemComponent,
    active_system_definition_status,
    apply_system_definition_sections,
    build_system_definition_export_block,
    mark_system_definition_stale_if_needed,
    parse_authorization_boundary,
    parse_component_inventory,
    parse_interconnection_register,
    validate_authorization_boundary,
    validate_component_inventory,
    validate_interconnection_register,
)


def test_validate_authorization_boundary_requires_narrative_length() -> None:
    with pytest.raises(ValueError, match="at least 20 characters"):
        validate_authorization_boundary(
            AuthorizationBoundary(narrative="too short", diagram_links=())
        )


def test_parse_and_validate_component_inventory() -> None:
    payload = {
        "components": [
            {
                "component_id": "web",
                "name": "Web tier",
                "purpose": "Public UI",
                "placement": "inside",
                "evidence": [],
            }
        ]
    }
    components = parse_component_inventory(json.dumps(payload))
    validate_component_inventory(components)
    assert components[0].name == "Web tier"


def test_mark_system_definition_stale_when_boundary_edited() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.system_definition_status",
                value="confirmed",
                provenance=Provenance.ISSO_ENTERED,
            ),
        ),
        sections=(
            SectionContent(
                key="system.authorization_boundary",
                title="Authorization Boundary",
                content='{"narrative":"x"*20,"diagram_links":[]}',
                state=SectionState.EDITED,
            ),
        ),
    )
    updated = mark_system_definition_stale_if_needed(
        content,
        section_key="system.authorization_boundary",
    )
    facts = {fact.key: fact for fact in updated.facts}
    assert facts["system.system_definition_status"].value == "stale"


def test_apply_system_definition_sections_sets_confirmed_status() -> None:
    artifact_id = uuid.uuid4()
    content = RevisionContent(
        sections=(
            SectionContent(
                key="system.authorization_boundary",
                title="Authorization Boundary",
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
    updated = apply_system_definition_sections(
        content,
        boundary=AuthorizationBoundary(
            narrative="The authorization boundary includes all production hosts.",
            diagram_links=(
                DiagramLink(
                    artifact_id=artifact_id,
                    locator={"page": 1},
                    label="Network diagram",
                ),
            ),
        ),
        components=(
            SystemComponent(
                component_id="app",
                name="Application tier",
                purpose="Business logic",
                placement="inside",
                evidence=(),
            ),
        ),
        interconnections=(
            Interconnection(
                interconnection_id="idp",
                connected_organization="Agency IAM",
                connected_system="Identity Provider",
                direction="outbound",
                data_types=("authentication tokens",),
                interface_protocol="HTTPS",
                connection_owner="ISSO",
                agreement_type="MOU",
                agreement_id="MOU-1",
                agreement_status="active",
                agreement_expiration="2027-01-01",
                boundary_protections="TLS mutual auth",
                evidence=(),
            ),
        ),
    )
    assert active_system_definition_status(
        {fact.key: fact for fact in updated.facts}
    ) == "confirmed"
    boundary = parse_authorization_boundary(
        next(
            section.content
            for section in updated.sections
            if section.key == "system.authorization_boundary"
        )
    )
    assert boundary is not None
    assert boundary.diagram_links[0].artifact_id == artifact_id


def test_build_system_definition_export_block() -> None:
    block = build_system_definition_export_block(
        sections={
            "system.authorization_boundary": json.dumps(
                {
                    "narrative": "Boundary narrative text long enough.",
                    "diagram_links": [],
                }
            ),
            "system.components": json.dumps(
                {
                    "components": [
                        {
                            "component_id": "app",
                            "name": "App",
                            "purpose": "Logic",
                            "placement": "inside",
                            "evidence": [],
                        }
                    ]
                }
            ),
            "system.interconnections": json.dumps(
                {
                    "interconnections": [
                        {
                            "interconnection_id": "x",
                            "connected_organization": "Org",
                            "connected_system": "Sys",
                            "direction": "inbound",
                            "data_types": ["PII"],
                            "interface_protocol": "HTTPS",
                            "connection_owner": "Owner",
                            "agreement_type": "ISA",
                            "agreement_id": "",
                            "agreement_status": "",
                            "agreement_expiration": "",
                            "boundary_protections": "Firewall",
                            "evidence": [],
                        }
                    ]
                }
            ),
        },
        evidence_catalog={},
    )
    assert block["authorization_boundary"]["narrative"].startswith("Boundary")
    assert len(block["components"]) == 1
    assert len(block["interconnections"]) == 1


def test_edit_section_marks_confirmed_system_definition_stale() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.system_definition_status",
                value="confirmed",
                provenance=Provenance.ISSO_ENTERED,
            ),
        ),
        sections=(
            SectionContent(
                key="system.interconnections",
                title="Interconnections",
                content='{"interconnections":[]}',
                state=SectionState.EDITED,
            ),
        ),
    )
    updated = edit_section(
        content,
        section_key="system.interconnections",
        text='{"interconnections":[{"interconnection_id":"1","connected_organization":"A","connected_system":"B","direction":"inbound","data_types":["x"],"interface_protocol":"HTTPS","connection_owner":"O","agreement_type":"MOU","agreement_id":"","agreement_status":"","agreement_expiration":"","boundary_protections":"fw","evidence":[]}]}',
    )
    facts = {fact.key: fact for fact in updated.facts}
    assert facts["system.system_definition_status"].value == "stale"
