"""Contract tests for canonical SSP workspace content."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceArtifactContent,
    EvidenceLink,
    EvidenceState,
    FactContent,
    ProfileRequirement,
    Provenance,
    RevisionContent,
    SectionContent,
    SectionState,
    revision_content_sha256,
)


def test_revision_hash_is_stable_for_exact_content() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.name",
                value="Atlas",
                provenance=Provenance.ISSO_ENTERED,
            ),
        ),
        sections=(
            SectionContent(
                key="system_description",
                title="System Description",
                content="Atlas supports case management.",
                state=SectionState.EDITED,
            ),
        ),
        controls=(
            ControlContent(
                control_id="AC-2",
                title="Account Management",
                implementation_statement="The service uses agency identity.",
                state=ControlState.REVIEWED,
            ),
        ),
    )

    reconstructed = RevisionContent.model_validate(content.model_dump(mode="json"))

    assert revision_content_sha256(content) == revision_content_sha256(reconstructed)
    assert len(revision_content_sha256(content)) == 64


def test_revision_hash_changes_when_content_changes() -> None:
    first = RevisionContent(
        facts=(
            FactContent(
                key="system.name",
                value="Atlas",
                provenance=Provenance.ISSO_ENTERED,
            ),
        )
    )
    second = RevisionContent(
        facts=(
            FactContent(
                key="system.name",
                value="Atlas 2",
                provenance=Provenance.ISSO_ENTERED,
            ),
        )
    )

    assert revision_content_sha256(first) != revision_content_sha256(second)


def test_agent_generated_fact_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="require evidence"):
        FactContent(
            key="hosting.model",
            value="agency_cloud",
            provenance=Provenance.AGENT_GENERATED,
        )

    artifact_id = uuid.uuid4()
    fact = FactContent(
        key="hosting.model",
        value="agency_cloud",
        provenance=Provenance.AGENT_GENERATED,
        evidence=(EvidenceLink(artifact_id=artifact_id, locator={"page": 3}),),
    )
    assert fact.evidence[0].artifact_id == artifact_id


def test_direct_evidence_contract_binds_storage_key_to_digest() -> None:
    digest = "a" * 64
    artifact = EvidenceArtifactContent(
        evidence_artifact_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        storage_key=f"aa/{digest}",
        size_bytes=10,
        display_filename="diagram.png",
        media_type="image/png",
        detected_format="png",
        sha256=digest,
        state=EvidenceState.PROCESSED,
        extracted_segments=({"kind": "vision", "text": "Agency subnet"},),
    )
    assert artifact.size_bytes == 10

    with pytest.raises(ValidationError, match="must contain"):
        EvidenceArtifactContent(
            evidence_artifact_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            storage_key=f"bb/{'b' * 64}",
            size_bytes=10,
            display_filename="diagram.png",
            media_type="image/png",
            sha256=digest,
            state=EvidenceState.UPLOADED,
        )


def test_revision_rejects_duplicate_canonical_keys() -> None:
    fact = FactContent(
        key="system.name",
        value="Atlas",
        provenance=Provenance.ISSO_ENTERED,
    )
    with pytest.raises(ValidationError, match="duplicate fact key"):
        RevisionContent(facts=(fact, fact))


def test_profile_requirement_rejects_non_string_enum() -> None:
    with pytest.raises(ValidationError, match="only for string"):
        ProfileRequirement(
            key="users.count",
            value_type="number",
            enum_values=("1", "2"),
        )
