"""Structured NIST SP 800-60 information type mapping for system.data_types."""

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
from ato_service.ssp_workspace.sp800_60_catalog import (
    Sp80060Catalog,
    Sp80060InformationType,
    load_sp800_60_catalog,
)

INFORMATION_TYPES_STATUS_KEY = "system.information_types_status"
INFORMATION_TYPES_SECTION_KEY = "system.data_types"

InformationTypesStatus = Literal["unconfirmed", "confirmed", "stale"]
ImpactLevel = Literal["low", "moderate", "high"]


@dataclass(frozen=True, slots=True)
class InformationTypeMapping:
    entry_id: str
    catalog_identifier: str
    description: str
    adjusted_confidentiality: ImpactLevel | None
    adjusted_integrity: ImpactLevel | None
    adjusted_availability: ImpactLevel | None
    adjustment_rationale: str
    evidence: tuple[EvidenceLink, ...]

    def effective_impacts(
        self,
        catalog_entry: Sp80060InformationType,
    ) -> tuple[ImpactLevel, ImpactLevel, ImpactLevel]:
        return (
            self.adjusted_confidentiality or catalog_entry.confidentiality,
            self.adjusted_integrity or catalog_entry.integrity,
            self.adjusted_availability or catalog_entry.availability,
        )

    def has_adjustment(self, catalog_entry: Sp80060InformationType) -> bool:
        return any(
            (
                self.adjusted_confidentiality
                and self.adjusted_confidentiality != catalog_entry.confidentiality,
                self.adjusted_integrity
                and self.adjusted_integrity != catalog_entry.integrity,
                self.adjusted_availability
                and self.adjusted_availability != catalog_entry.availability,
            )
        )

    def to_dict(self, *, catalog_entry: Sp80060InformationType) -> dict[str, Any]:
        conf, integ, avail = self.effective_impacts(catalog_entry)
        return {
            "entry_id": self.entry_id,
            "catalog_identifier": self.catalog_identifier,
            "catalog_title": catalog_entry.title,
            "description": self.description.strip(),
            "catalog_confidentiality": catalog_entry.confidentiality,
            "catalog_integrity": catalog_entry.integrity,
            "catalog_availability": catalog_entry.availability,
            "adjusted_confidentiality": self.adjusted_confidentiality,
            "adjusted_integrity": self.adjusted_integrity,
            "adjusted_availability": self.adjusted_availability,
            "effective_confidentiality": conf,
            "effective_integrity": integ,
            "effective_availability": avail,
            "adjustment_rationale": self.adjustment_rationale.strip(),
            "evidence": [
                {
                    "artifact_id": str(link.artifact_id),
                    "locator": dict(sorted(link.locator.items())),
                }
                for link in self.evidence
            ],
        }


def active_information_types_status(
    facts: Mapping[str, FactContent],
) -> InformationTypesStatus | None:
    fact = facts.get(INFORMATION_TYPES_STATUS_KEY)
    if fact is None or fact.state is not FactState.ACTIVE:
        return None
    if not isinstance(fact.value, str):
        return None
    if fact.value not in {"unconfirmed", "confirmed", "stale"}:
        return None
    return fact.value  # type: ignore[return-value]


def parse_information_type_register(content: str) -> tuple[InformationTypeMapping, ...]:
    if not content.strip():
        return ()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("information type register must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("information type register must be a JSON object")
    raw_items = payload.get("information_types", [])
    if not isinstance(raw_items, list):
        raise ValueError("information_types must be an array")
    return tuple(_parse_mapping(entry) for entry in raw_items)


def validate_information_type_register(
    mappings: tuple[InformationTypeMapping, ...],
    *,
    catalog: Sp80060Catalog | None = None,
) -> None:
    catalog = catalog or load_sp800_60_catalog()
    by_id = catalog.by_identifier()
    if not mappings:
        raise ValueError("at least one SP 800-60 information type mapping is required")
    seen: set[str] = set()
    for mapping in mappings:
        if mapping.catalog_identifier not in by_id:
            raise ValueError(
                f"unknown SP 800-60 identifier: {mapping.catalog_identifier}"
            )
        if mapping.catalog_identifier in seen:
            raise ValueError(
                f"duplicate SP 800-60 identifier: {mapping.catalog_identifier}"
            )
        seen.add(mapping.catalog_identifier)
        if not mapping.description.strip():
            raise ValueError("each information type mapping requires a description")
        catalog_entry = by_id[mapping.catalog_identifier]
        for field_name, adjusted in (
            ("confidentiality", mapping.adjusted_confidentiality),
            ("integrity", mapping.adjusted_integrity),
            ("availability", mapping.adjusted_availability),
        ):
            if adjusted is not None and adjusted not in {"low", "moderate", "high"}:
                raise ValueError(f"adjusted {field_name} must be low, moderate, or high")
        if mapping.has_adjustment(catalog_entry) and not mapping.adjustment_rationale.strip():
            raise ValueError(
                "adjustment rationale is required when catalog impacts are adjusted"
            )


def structured_section_metric_value(content: str) -> list[dict[str, Any]] | None:
    try:
        mappings = parse_information_type_register(content)
    except ValueError:
        return None
    if not mappings:
        return None
    catalog = load_sp800_60_catalog()
    by_id = catalog.by_identifier()
    return [
        mapping.to_dict(catalog_entry=by_id[mapping.catalog_identifier])
        for mapping in mappings
    ]


def mark_information_types_stale_if_needed(
    content: RevisionContent,
    *,
    section_key: str,
) -> RevisionContent:
    if section_key != INFORMATION_TYPES_SECTION_KEY:
        return content
    facts = {item.key: item for item in content.facts}
    if active_information_types_status(facts) != "confirmed":
        return content
    facts[INFORMATION_TYPES_STATUS_KEY] = FactContent(
        key=INFORMATION_TYPES_STATUS_KEY,
        value="stale",
        provenance=Provenance.ISSO_ENTERED,
    )
    return content.model_copy(
        update={"facts": tuple(facts[key] for key in sorted(facts))},
    )


def apply_information_type_register(
    content: RevisionContent,
    *,
    mappings: tuple[InformationTypeMapping, ...],
) -> RevisionContent:
    validate_information_type_register(mappings)
    catalog = load_sp800_60_catalog()
    by_id = catalog.by_identifier()
    section_json = json.dumps(
        {
            "information_types": [
                mapping.to_dict(catalog_entry=by_id[mapping.catalog_identifier])
                for mapping in mappings
            ]
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    sections = {section.key: section for section in content.sections}
    current = sections.get(INFORMATION_TYPES_SECTION_KEY)
    if current is None:
        raise ValueError("information types section is unavailable")
    sections[INFORMATION_TYPES_SECTION_KEY] = SectionContent(
        key=current.key,
        title=current.title,
        content=section_json,
        state=SectionState.EDITED,
        evidence=current.evidence,
    )
    facts = {item.key: item for item in content.facts}
    facts[INFORMATION_TYPES_STATUS_KEY] = FactContent(
        key=INFORMATION_TYPES_STATUS_KEY,
        value="confirmed",
        provenance=Provenance.ISSO_ENTERED,
    )
    return content.model_copy(
        update={
            "sections": tuple(sections[key] for key in sorted(sections)),
            "facts": tuple(facts[key] for key in sorted(facts)),
        },
    )


def build_information_types_export_block(
    *,
    sections: Mapping[str, str],
    evidence_catalog: Mapping[str, str],
) -> dict[str, Any]:
    mappings = parse_information_type_register(
        sections.get(INFORMATION_TYPES_SECTION_KEY, "")
    )
    if not mappings:
        return {"status": "unconfirmed", "catalog_version": "", "entries": []}
    catalog = load_sp800_60_catalog()
    by_id = catalog.by_identifier()

    def enrich_links(links: tuple[EvidenceLink, ...]) -> list[dict[str, Any]]:
        return [
            {
                "artifact_id": str(link.artifact_id),
                "locator": dict(sorted(link.locator.items())),
                "display_filename": evidence_catalog.get(str(link.artifact_id), ""),
            }
            for link in links
        ]

    return {
        "status": "confirmed",
        "catalog_source_id": catalog.source_id,
        "catalog_version": catalog.version,
        "entries": [
            {
                **mapping.to_dict(catalog_entry=by_id[mapping.catalog_identifier]),
                "evidence": enrich_links(mapping.evidence),
            }
            for mapping in mappings
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
        raise ValueError(
            "information type evidence must reference workspace artifacts"
        )
    return links


def _parse_mapping(raw: Any) -> InformationTypeMapping:
    if not isinstance(raw, dict):
        raise ValueError("information type entries must be objects")
    entry_id = str(raw.get("entry_id") or uuid.uuid4())
    catalog_identifier = str(raw.get("catalog_identifier") or "").strip()
    if not catalog_identifier:
        raise ValueError("catalog_identifier is required")
    return InformationTypeMapping(
        entry_id=entry_id,
        catalog_identifier=catalog_identifier,
        description=str(raw.get("description") or ""),
        adjusted_confidentiality=_optional_impact(raw.get("adjusted_confidentiality")),
        adjusted_integrity=_optional_impact(raw.get("adjusted_integrity")),
        adjusted_availability=_optional_impact(raw.get("adjusted_availability")),
        adjustment_rationale=str(raw.get("adjustment_rationale") or ""),
        evidence=_parse_evidence_links(raw.get("evidence")),
    )


def _optional_impact(value: Any) -> ImpactLevel | None:
    if value in (None, ""):
        return None
    if value not in {"low", "moderate", "high"}:
        raise ValueError("impact adjustments must be low, moderate, or high")
    return value  # type: ignore[return-value]


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
