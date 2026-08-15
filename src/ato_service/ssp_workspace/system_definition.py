"""Structured authorization boundary, component inventory, and interconnection register."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from ato_service.ssp_workspace.contracts import (
    EvidenceLink,
    FactContent,
    FactState,
    Provenance,
    RevisionContent,
    SectionContent,
    SectionState,
)

SYSTEM_DEFINITION_STATUS_KEY = "system.system_definition_status"

STRUCTURED_SECTION_KEYS = frozenset(
    {
        "system.authorization_boundary",
        "system.components",
        "system.interconnections",
    }
)

BoundaryPlacement = Literal["inside", "outside", "crossing"]
InterconnectionDirection = Literal["inbound", "outbound", "bidirectional"]
SystemDefinitionStatus = Literal["unconfirmed", "confirmed", "stale"]

STALE_TRIGGER_SECTION_KEYS = frozenset(
    {
        "system.authorization_boundary",
        "system.components",
        "system.interconnections",
    }
)


@dataclass(frozen=True, slots=True)
class DiagramLink:
    artifact_id: uuid.UUID
    locator: dict[str, Any]
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifact_id": str(self.artifact_id),
            "locator": dict(sorted(self.locator.items())),
        }
        if self.label.strip():
            payload["label"] = self.label.strip()
        return payload


@dataclass(frozen=True, slots=True)
class AuthorizationBoundary:
    narrative: str
    diagram_links: tuple[DiagramLink, ...]

    def to_section_json(self) -> str:
        return json.dumps(
            {
                "narrative": self.narrative.strip(),
                "diagram_links": [link.to_dict() for link in self.diagram_links],
            },
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class SystemComponent:
    component_id: str
    name: str
    purpose: str
    placement: BoundaryPlacement
    evidence: tuple[EvidenceLink, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "name": self.name.strip(),
            "purpose": self.purpose.strip(),
            "placement": self.placement,
            "evidence": [
                {
                    "artifact_id": str(link.artifact_id),
                    "locator": dict(sorted(link.locator.items())),
                }
                for link in self.evidence
            ],
        }


@dataclass(frozen=True, slots=True)
class Interconnection:
    interconnection_id: str
    connected_organization: str
    connected_system: str
    direction: InterconnectionDirection
    data_types: tuple[str, ...]
    interface_protocol: str
    connection_owner: str
    agreement_type: str
    agreement_id: str
    agreement_status: str
    agreement_expiration: str
    boundary_protections: str
    evidence: tuple[EvidenceLink, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "interconnection_id": self.interconnection_id,
            "connected_organization": self.connected_organization.strip(),
            "connected_system": self.connected_system.strip(),
            "direction": self.direction,
            "data_types": [item.strip() for item in self.data_types if item.strip()],
            "interface_protocol": self.interface_protocol.strip(),
            "connection_owner": self.connection_owner.strip(),
            "agreement_type": self.agreement_type.strip(),
            "agreement_id": self.agreement_id.strip(),
            "agreement_status": self.agreement_status.strip(),
            "agreement_expiration": self.agreement_expiration.strip(),
            "boundary_protections": self.boundary_protections.strip(),
            "evidence": [
                {
                    "artifact_id": str(link.artifact_id),
                    "locator": dict(sorted(link.locator.items())),
                }
                for link in self.evidence
            ],
        }


def active_system_definition_status(
    facts: Mapping[str, FactContent],
) -> SystemDefinitionStatus | None:
    fact = facts.get(SYSTEM_DEFINITION_STATUS_KEY)
    if fact is None or fact.state is not FactState.ACTIVE:
        return None
    if not isinstance(fact.value, str):
        return None
    if fact.value not in {"unconfirmed", "confirmed", "stale"}:
        return None
    return fact.value  # type: ignore[return-value]


def parse_authorization_boundary(content: str) -> AuthorizationBoundary | None:
    if not content.strip():
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return AuthorizationBoundary(narrative=content.strip(), diagram_links=())
    if not isinstance(payload, dict):
        raise ValueError("authorization boundary must be a JSON object")
    narrative = payload.get("narrative", "")
    if not isinstance(narrative, str):
        raise ValueError("authorization boundary narrative must be a string")
    raw_links = payload.get("diagram_links", [])
    if not isinstance(raw_links, list):
        raise ValueError("diagram_links must be an array")
    links: list[DiagramLink] = []
    for entry in raw_links:
        if not isinstance(entry, dict):
            raise ValueError("diagram_links entries must be objects")
        artifact_id = entry.get("artifact_id")
        locator = entry.get("locator")
        if not isinstance(artifact_id, str) or not isinstance(locator, dict) or not locator:
            raise ValueError("diagram_links require artifact_id and locator")
        links.append(
            DiagramLink(
                artifact_id=uuid.UUID(artifact_id),
                locator=dict(locator),
                label=str(entry.get("label") or ""),
            )
        )
    return AuthorizationBoundary(narrative=narrative, diagram_links=tuple(links))


def parse_component_inventory(content: str) -> tuple[SystemComponent, ...]:
    if not content.strip():
        return ()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("component inventory must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("component inventory must be a JSON object")
    raw_components = payload.get("components", [])
    if not isinstance(raw_components, list):
        raise ValueError("components must be an array")
    return tuple(_parse_component(entry) for entry in raw_components)


def parse_interconnection_register(content: str) -> tuple[Interconnection, ...]:
    if not content.strip():
        return ()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("interconnection register must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("interconnection register must be a JSON object")
    raw_items = payload.get("interconnections", [])
    if not isinstance(raw_items, list):
        raise ValueError("interconnections must be an array")
    return tuple(_parse_interconnection(entry) for entry in raw_items)


def validate_authorization_boundary(boundary: AuthorizationBoundary) -> None:
    if len(boundary.narrative.strip()) < 20:
        raise ValueError("authorization boundary narrative requires at least 20 characters")
    for link in boundary.diagram_links:
        if not link.locator:
            raise ValueError("diagram links require a non-empty locator")


def validate_component_inventory(components: tuple[SystemComponent, ...]) -> None:
    if not components:
        raise ValueError("at least one system component is required")
    for component in components:
        if not component.name.strip():
            raise ValueError("each component requires a name")
        if not component.purpose.strip():
            raise ValueError("each component requires a purpose")
        if component.placement not in {"inside", "outside", "crossing"}:
            raise ValueError("component placement must be inside, outside, or crossing")


def validate_interconnection_register(
    interconnections: tuple[Interconnection, ...],
) -> None:
    if not interconnections:
        raise ValueError("at least one interconnection is required")
    for item in interconnections:
        required_text = (
            item.connected_organization,
            item.connected_system,
            item.interface_protocol,
            item.connection_owner,
            item.agreement_type,
            item.boundary_protections,
        )
        if any(not value.strip() for value in required_text):
            raise ValueError("interconnection required text fields cannot be empty")
        if not item.data_types:
            raise ValueError("each interconnection requires at least one data type")
        if item.direction not in {"inbound", "outbound", "bidirectional"}:
            raise ValueError(
                "interconnection direction must be inbound, outbound, or bidirectional"
            )


def structured_section_metric_value(
    section_key: str,
    content: str,
    *,
    structured_kind: str | None,
) -> Any:
    if structured_kind == "authorization_boundary":
        boundary = parse_authorization_boundary(content)
        if boundary is None:
            return None
        return {
            "narrative": boundary.narrative.strip(),
            "diagram_links": [link.to_dict() for link in boundary.diagram_links],
        }
    if structured_kind == "component_inventory":
        components = parse_component_inventory(content)
        return [component.to_dict() for component in components]
    if structured_kind == "interconnection_register":
        interconnections = parse_interconnection_register(content)
        return [item.to_dict() for item in interconnections]
    if section_key == "system.authorization_boundary":
        boundary = parse_authorization_boundary(content)
        if boundary is None:
            return None
        return {
            "narrative": boundary.narrative.strip(),
            "diagram_links": [link.to_dict() for link in boundary.diagram_links],
        }
    if section_key == "system.components":
        return [component.to_dict() for component in parse_component_inventory(content)]
    if section_key == "system.interconnections":
        return [
            item.to_dict() for item in parse_interconnection_register(content)
        ]
    return None


def mark_system_definition_stale_if_needed(
    content: RevisionContent,
    *,
    section_key: str,
) -> RevisionContent:
    if section_key not in STALE_TRIGGER_SECTION_KEYS:
        return content
    facts = {item.key: item for item in content.facts}
    if active_system_definition_status(facts) != "confirmed":
        return content
    facts[SYSTEM_DEFINITION_STATUS_KEY] = FactContent(
        key=SYSTEM_DEFINITION_STATUS_KEY,
        value="stale",
        provenance=Provenance.ISSO_ENTERED,
    )
    return content.model_copy(
        update={"facts": tuple(facts[key] for key in sorted(facts))},
    )


def apply_system_definition_sections(
    content: RevisionContent,
    *,
    boundary: AuthorizationBoundary,
    components: tuple[SystemComponent, ...],
    interconnections: tuple[Interconnection, ...],
) -> RevisionContent:
    sections = {section.key: section for section in content.sections}
    updates = {
        "system.authorization_boundary": boundary.to_section_json(),
        "system.components": json.dumps(
            {"components": [component.to_dict() for component in components]},
            ensure_ascii=False,
            sort_keys=True,
        ),
        "system.interconnections": json.dumps(
            {
                "interconnections": [
                    item.to_dict() for item in interconnections
                ]
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        "system.diagram_references": _diagram_reference_lines(boundary),
    }
    for key, text in updates.items():
        current = sections.get(key)
        if current is None:
            continue
        sections[key] = SectionContent(
            key=current.key,
            title=current.title,
            content=text,
            state=SectionState.EDITED if text.strip() else SectionState.EMPTY,
            evidence=current.evidence,
        )
    facts = {item.key: item for item in content.facts}
    facts[SYSTEM_DEFINITION_STATUS_KEY] = FactContent(
        key=SYSTEM_DEFINITION_STATUS_KEY,
        value="confirmed",
        provenance=Provenance.ISSO_ENTERED,
    )
    return content.model_copy(
        update={
            "sections": tuple(sections[key] for key in sorted(sections)),
            "facts": tuple(facts[key] for key in sorted(facts)),
        },
    )


def build_system_definition_export_block(
    *,
    sections: Mapping[str, str],
    evidence_catalog: Mapping[str, str],
) -> dict[str, Any]:
    boundary = parse_authorization_boundary(
        sections.get("system.authorization_boundary", "")
    )
    components = parse_component_inventory(sections.get("system.components", ""))
    interconnections = parse_interconnection_register(
        sections.get("system.interconnections", "")
    )

    def enrich_links(links: tuple[EvidenceLink, ...]) -> list[dict[str, Any]]:
        return [
            {
                "artifact_id": str(link.artifact_id),
                "locator": dict(sorted(link.locator.items())),
                "display_filename": evidence_catalog.get(str(link.artifact_id), ""),
            }
            for link in links
        ]

    boundary_block: dict[str, Any] | None = None
    if boundary is not None:
        boundary_block = {
            "narrative": boundary.narrative.strip(),
            "diagram_links": [
                {
                    **link.to_dict(),
                    "display_filename": evidence_catalog.get(
                        str(link.artifact_id),
                        "",
                    ),
                }
                for link in boundary.diagram_links
            ],
        }
    return {
        "status": "confirmed",
        "authorization_boundary": boundary_block,
        "components": [
            {
                **component.to_dict(),
                "evidence": enrich_links(component.evidence),
            }
            for component in components
        ],
        "interconnections": [
            {
                **item.to_dict(),
                "evidence": enrich_links(item.evidence),
            }
            for item in interconnections
        ],
    }


async def resolve_workspace_evidence_links(
    session: Any,
    *,
    workspace_id: uuid.UUID,
    links: tuple[EvidenceLink, ...],
) -> tuple[EvidenceLink, ...]:
    from sqlalchemy import select

    from ato_service.db.models import SspEvidenceArtifact

    if not links:
        return ()
    artifact_ids = {link.artifact_id for link in links}
    rows = (
        await session.execute(
            select(SspEvidenceArtifact.evidence_artifact_id).where(
                SspEvidenceArtifact.workspace_id == workspace_id,
                SspEvidenceArtifact.removed_at.is_(None),
                SspEvidenceArtifact.evidence_artifact_id.in_(artifact_ids),
            )
        )
    ).scalars()
    if set(rows) != artifact_ids:
        raise ValueError("system definition evidence must reference workspace artifacts")
    return links


def _diagram_reference_lines(boundary: AuthorizationBoundary) -> str:
    lines: list[str] = []
    for link in boundary.diagram_links:
        label = link.label.strip() or str(link.artifact_id)
        locator = json.dumps(link.locator, sort_keys=True, ensure_ascii=False)
        lines.append(f"- {label} ({locator})")
    return "\n".join(lines)


def _parse_component(raw: Any) -> SystemComponent:
    if not isinstance(raw, dict):
        raise ValueError("component entries must be objects")
    component_id = str(raw.get("component_id") or uuid.uuid4())
    name = raw.get("name")
    purpose = raw.get("purpose")
    placement = raw.get("placement")
    if not isinstance(name, str) or not isinstance(purpose, str) or not isinstance(
        placement, str
    ):
        raise ValueError("component name, purpose, and placement are required")
    return SystemComponent(
        component_id=component_id,
        name=name,
        purpose=purpose,
        placement=placement,  # type: ignore[arg-type]
        evidence=_parse_evidence_links(raw.get("evidence")),
    )


def _parse_interconnection(raw: Any) -> Interconnection:
    if not isinstance(raw, dict):
        raise ValueError("interconnection entries must be objects")
    interconnection_id = str(raw.get("interconnection_id") or uuid.uuid4())
    data_types_raw = raw.get("data_types", [])
    if not isinstance(data_types_raw, list):
        raise ValueError("interconnection data_types must be an array")
    data_types = tuple(
        str(item).strip() for item in data_types_raw if str(item).strip()
    )
    direction = raw.get("direction")
    if not isinstance(direction, str):
        raise ValueError("interconnection direction is required")
    return Interconnection(
        interconnection_id=interconnection_id,
        connected_organization=str(raw.get("connected_organization") or ""),
        connected_system=str(raw.get("connected_system") or ""),
        direction=direction,  # type: ignore[arg-type]
        data_types=data_types,
        interface_protocol=str(raw.get("interface_protocol") or ""),
        connection_owner=str(raw.get("connection_owner") or ""),
        agreement_type=str(raw.get("agreement_type") or ""),
        agreement_id=str(raw.get("agreement_id") or ""),
        agreement_status=str(raw.get("agreement_status") or ""),
        agreement_expiration=str(raw.get("agreement_expiration") or ""),
        boundary_protections=str(raw.get("boundary_protections") or ""),
        evidence=_parse_evidence_links(raw.get("evidence")),
    )


def _parse_evidence_links(raw: Any) -> tuple[EvidenceLink, ...]:
    if raw in (None, ()):
        return ()
    if not isinstance(raw, list):
        raise ValueError("evidence must be an array")
    links: list[EvidenceLink] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("evidence entries must be objects")
        artifact_id = entry.get("artifact_id")
        locator = entry.get("locator")
        if not isinstance(artifact_id, str) or not isinstance(locator, dict) or not locator:
            raise ValueError("evidence entries require artifact_id and locator")
        links.append(
            EvidenceLink(
                artifact_id=uuid.UUID(artifact_id),
                locator=dict(locator),
            )
        )
    return tuple(links)
