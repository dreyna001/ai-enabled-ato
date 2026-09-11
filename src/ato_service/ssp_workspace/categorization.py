"""FIPS 199 categorization rules for the agency FISMA SSP profile."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from ato_service.ssp_workspace.contracts import (
    EvidenceLink,
    FactContent,
    FactState,
    Provenance,
    RevisionContent,
)

# Sections whose edits invalidate a confirmed FIPS 199 categorization.
STALE_TRIGGER_SECTION_KEYS = frozenset(
    {
        "system.purpose",
        "system.authorization_boundary",
        "system.data_types",
    }
)

IMPACT_FACT_KEYS = (
    "system.confidentiality_impact",
    "system.integrity_impact",
    "system.availability_impact",
)

RATIONALE_FACT_KEYS = (
    "system.confidentiality_impact_rationale",
    "system.integrity_impact_rationale",
    "system.availability_impact_rationale",
)

CATEGORIZATION_STATUS_KEY = "system.categorization_status"

FIPS_IMPACT_LEVELS = frozenset({"low", "moderate", "high"})


class CategorizationValidationError(ValueError):
    """Bounded categorization validation failure with a client field path."""

    error_code = "request_schema_invalid"

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class CategorizationEvidenceInput:
    confidentiality: tuple[EvidenceLink, ...]
    integrity: tuple[EvidenceLink, ...]
    availability: tuple[EvidenceLink, ...]


def active_categorization_status(
    facts: Mapping[str, FactContent],
) -> str | None:
    fact = facts.get(CATEGORIZATION_STATUS_KEY)
    if fact is None or fact.state is not FactState.ACTIVE:
        return None
    if not isinstance(fact.value, str):
        return None
    return fact.value


def mark_categorization_stale_if_needed(
    content: RevisionContent,
    *,
    section_key: str,
) -> RevisionContent:
    """Mark confirmed categorization stale when a trigger section changes."""

    if section_key not in STALE_TRIGGER_SECTION_KEYS:
        return content
    facts = {item.key: item for item in content.facts}
    if active_categorization_status(facts) != "confirmed":
        return content
    facts[CATEGORIZATION_STATUS_KEY] = FactContent(
        key=CATEGORIZATION_STATUS_KEY,
        value="stale",
        provenance=Provenance.ISSO_ENTERED,
    )
    impact = facts.get("system.impact_level")
    if impact is not None:
        facts["system.provisional_impact_level"] = FactContent(
            key="system.provisional_impact_level",
            value=impact.value,
            provenance=Provenance.ISSO_ENTERED,
        )
    return content.model_copy(
        update={"facts": tuple(facts[key] for key in sorted(facts))},
    )


def validate_categorization_impacts(
    confidentiality: str,
    integrity: str,
    availability: str,
) -> tuple[str, str, str]:
    impacts = (confidentiality, integrity, availability)
    labels = ("confidentiality", "integrity", "availability")
    for label, value in zip(labels, impacts, strict=True):
        if value not in FIPS_IMPACT_LEVELS:
            raise CategorizationValidationError(
                "Select low, moderate, or high for each security objective.",
                field=label,
            )
    return impacts


def validate_categorization_rationales(
    confidentiality_rationale: str,
    integrity_rationale: str,
    availability_rationale: str,
) -> tuple[str, str, str]:
    rationales = (
        confidentiality_rationale.strip(),
        integrity_rationale.strip(),
        availability_rationale.strip(),
    )
    rationale_fields = (
        "confidentiality_rationale",
        "integrity_rationale",
        "availability_rationale",
    )
    for field, value in zip(rationale_fields, rationales, strict=True):
        if not value:
            raise CategorizationValidationError(
                "Enter a rationale for each security objective.",
                field=field,
            )
    return rationales


def validate_categorization_evidence(
    evidence: CategorizationEvidenceInput,
) -> None:
    if not evidence.confidentiality:
        raise CategorizationValidationError(
            "Select at least one processed artifact for confidentiality evidence.",
            field="confidentiality_evidence",
        )
    if not evidence.integrity:
        raise CategorizationValidationError(
            "Select at least one processed artifact for integrity evidence.",
            field="integrity_evidence",
        )
    if not evidence.availability:
        raise CategorizationValidationError(
            "Select at least one processed artifact for availability evidence.",
            field="availability_evidence",
        )


def high_water_mark_impact(
    confidentiality: str,
    integrity: str,
    availability: str,
) -> str:
    rank = {"low": 0, "moderate": 1, "high": 2}
    return max((confidentiality, integrity, availability), key=rank.__getitem__)


def build_confirmed_categorization_facts(
    *,
    confidentiality: str,
    integrity: str,
    availability: str,
    rationales: tuple[str, str, str],
    evidence: CategorizationEvidenceInput,
) -> dict[str, FactContent]:
    conf_rationale, int_rationale, avail_rationale = rationales
    return {
        CATEGORIZATION_STATUS_KEY: FactContent(
            key=CATEGORIZATION_STATUS_KEY,
            value="confirmed",
            provenance=Provenance.ISSO_ENTERED,
        ),
        IMPACT_FACT_KEYS[0]: FactContent(
            key=IMPACT_FACT_KEYS[0],
            value=confidentiality,
            provenance=Provenance.ISSO_ENTERED,
            evidence=evidence.confidentiality,
        ),
        IMPACT_FACT_KEYS[1]: FactContent(
            key=IMPACT_FACT_KEYS[1],
            value=integrity,
            provenance=Provenance.ISSO_ENTERED,
            evidence=evidence.integrity,
        ),
        IMPACT_FACT_KEYS[2]: FactContent(
            key=IMPACT_FACT_KEYS[2],
            value=availability,
            provenance=Provenance.ISSO_ENTERED,
            evidence=evidence.availability,
        ),
        RATIONALE_FACT_KEYS[0]: FactContent(
            key=RATIONALE_FACT_KEYS[0],
            value=conf_rationale,
            provenance=Provenance.ISSO_ENTERED,
        ),
        RATIONALE_FACT_KEYS[1]: FactContent(
            key=RATIONALE_FACT_KEYS[1],
            value=int_rationale,
            provenance=Provenance.ISSO_ENTERED,
        ),
        RATIONALE_FACT_KEYS[2]: FactContent(
            key=RATIONALE_FACT_KEYS[2],
            value=avail_rationale,
            provenance=Provenance.ISSO_ENTERED,
        ),
    }


def serialize_evidence_links(
    links: tuple[EvidenceLink, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": str(link.artifact_id),
            "locator": dict(sorted(link.locator.items())),
        }
        for link in links
    ]


def build_categorization_export_block(
    *,
    facts: Mapping[str, Any],
    fact_evidence: Mapping[str, tuple[EvidenceLink, ...]],
    evidence_catalog: Mapping[str, str],
    overall_impact: str,
) -> dict[str, Any]:
    """Build a normalized categorization subsection for approved exports."""

    def dimension(key: str, rationale_key: str) -> dict[str, Any]:
        links = fact_evidence.get(key, ())
        return {
            "impact": facts.get(key),
            "rationale": facts.get(rationale_key),
            "evidence": [
                {
                    "artifact_id": str(link.artifact_id),
                    "locator": dict(sorted(link.locator.items())),
                    "display_filename": evidence_catalog.get(
                        str(link.artifact_id),
                        "",
                    ),
                }
                for link in links
            ],
        }

    return {
        "status": facts.get(CATEGORIZATION_STATUS_KEY, "confirmed"),
        "overall_impact": overall_impact,
        "confidentiality": dimension(
            IMPACT_FACT_KEYS[0],
            RATIONALE_FACT_KEYS[0],
        ),
        "integrity": dimension(IMPACT_FACT_KEYS[1], RATIONALE_FACT_KEYS[1]),
        "availability": dimension(IMPACT_FACT_KEYS[2], RATIONALE_FACT_KEYS[2]),
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
        raise CategorizationValidationError(
            "Selected evidence is no longer in this workspace. Reload and re-select processed artifacts.",
            field="confidentiality_evidence",
        )
    return links
