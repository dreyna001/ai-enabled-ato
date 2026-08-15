from __future__ import annotations

import uuid

import pytest

from ato_service.ssp_workspace.categorization import (
    CategorizationEvidenceInput,
    build_confirmed_categorization_facts,
    mark_categorization_stale_if_needed,
    validate_categorization_evidence,
)
from ato_service.ssp_workspace.contracts import (
    EvidenceLink,
    FactContent,
    Provenance,
    RevisionContent,
    SectionContent,
    SectionState,
)


def _link() -> EvidenceLink:
    return EvidenceLink(
        artifact_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        locator={"kind": "categorization_attestation"},
    )


def test_validate_categorization_evidence_requires_each_dimension() -> None:
    with pytest.raises(ValueError, match="confidentiality"):
        validate_categorization_evidence(
            CategorizationEvidenceInput(
                confidentiality=(),
                integrity=(_link(),),
                availability=(_link(),),
            )
        )


def test_mark_categorization_stale_when_trigger_section_edited() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.categorization_status",
                value="confirmed",
                provenance=Provenance.ISSO_ENTERED,
            ),
            FactContent(
                key="system.impact_level",
                value="moderate",
                provenance=Provenance.ISSO_ENTERED,
            ),
        ),
        sections=(
            SectionContent(
                key="system.purpose",
                title="Purpose",
                content="Old purpose",
                state=SectionState.EDITED,
            ),
        ),
    )
    updated = mark_categorization_stale_if_needed(content, section_key="system.purpose")
    facts = {item.key: item for item in updated.facts}
    assert facts["system.categorization_status"].value == "stale"
    assert facts["system.provisional_impact_level"].value == "moderate"


def test_build_confirmed_categorization_facts_attaches_evidence() -> None:
    evidence = CategorizationEvidenceInput(
        confidentiality=(_link(),),
        integrity=(_link(),),
        availability=(_link(),),
    )
    facts = build_confirmed_categorization_facts(
        confidentiality="low",
        integrity="moderate",
        availability="low",
        rationales=(
            "Conf rationale",
            "Int rationale",
            "Avail rationale",
        ),
        evidence=evidence,
    )
    assert facts["system.confidentiality_impact"].evidence == evidence.confidentiality
    assert facts["system.categorization_status"].value == "confirmed"
