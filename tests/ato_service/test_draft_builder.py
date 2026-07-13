"""Focused tests for deterministic package draft builder (Component A Diff 3)."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from ato_service.draft_builder import (
    AggregatedIntakeDraft,
    DraftBuildError,
    build_initial_draft,
    validate_package_draft_document,
)
from ato_service.extraction import ExtractionContext, ExtractionLimits, VisionPolicy, extract_content

ROOT = Path(__file__).resolve().parents[2]
FISMA_FIXTURE_PATH = (
    ROOT / "data/synthetic-packages/fisma-demo-portal/agency-security-plan-excerpt.json"
)

LIMITS = ExtractionLimits(
    max_pdf_pages_per_file=3,
    max_extracted_text_characters_per_file=10_000,
    max_zip_members_per_archive=5,
    max_zip_uncompressed_bytes_per_archive=10_000,
    max_zip_decompression_ratio=10,
    max_xml_depth=8,
    max_xml_elements=100,
    max_xml_attributes_per_element=8,
    max_xml_text_node_characters=500,
)

SYSTEM_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _revision(*, profile_id: str = "fisma_agency_security") -> SimpleNamespace:
    return SimpleNamespace(
        package_revision_id=REVISION_ID,
        profile_id=profile_id,
        impact_level="moderate",
    )


def _system() -> SimpleNamespace:
    return SimpleNamespace(
        system_id=SYSTEM_ID,
        display_name="Fallback System Name",
    )


def _artifact(*, content: bytes, artifact_id: uuid.UUID = ARTIFACT_ID) -> SimpleNamespace:
    digest = hashlib.sha256(content).hexdigest()
    return SimpleNamespace(
        artifact_id=artifact_id,
        sha256=digest,
        size_bytes=len(content),
        display_filename="upload.json",
    )


def _extract_json_outcome(content: bytes) -> object:
    return extract_content(
        content_bytes=content,
        sha256=hashlib.sha256(content).hexdigest(),
        limits=LIMITS,
        context=ExtractionContext(
            declared_media_type="application/json",
            detected_media_type="application/json",
            declared_format="json",
            artifact_kind="manifest",
            filename="upload.json",
        ),
        vision_policy=VisionPolicy(vision_allowed=False),
    )


def test_builds_fisma_shell_from_revision_metadata() -> None:
    draft = build_initial_draft(
        revision=_revision(),
        system=_system(),
        artifacts=[],
        artifact_outcomes=[],
    )

    assert draft.document["package"]["profile_id"] == "fisma_agency_security"
    assert draft.document["fedramp_20x"] is None
    assert draft.document["fedramp_rev5_transition"] is None
    assert draft.document["fisma_agency_security"] == {"security_plan_sections": {}}
    assert draft.document["assessor_inputs"] == {}
    validate_package_draft_document(draft.document)


def test_maps_synthetic_agency_security_plan_excerpt_to_canonical_draft() -> None:
    content = FISMA_FIXTURE_PATH.read_bytes()
    artifact = _artifact(content=content)
    outcome = _extract_json_outcome(content)

    draft = build_initial_draft(
        revision=_revision(),
        system=_system(),
        artifacts=[artifact],
        artifact_outcomes=[(artifact, outcome)],
    )

    assert draft.document["package"]["title"].startswith("Synthetic FISMA")
    assert draft.document["system"]["display_name"] == "Customer Records Portal"
    assert draft.document["system"]["mission_summary"].startswith("Internal web")
    assert draft.document["system"]["impact_level"] == "moderate"
    assert draft.document["security_controls"]["AC-1"]["implementation_statement"].startswith(
        "Access control policy"
    )
    assert draft.document["security_controls"]["AC-1"]["implementation_status"] == "implemented"
    assert draft.document["evidence"]["agency_security_plan"]["document_type"] == (
        "security_plan_excerpt"
    )
    assert draft.document["contacts"]["system_owner"][0]["email"] == "demo.owner@agency.example"
    assert draft.field_provenance["/system/display_name"]["source_locator"] == {
        "kind": "json_pointer",
        "json_pointer": "/system/name"
    }
    assert draft.field_provenance["/security_controls"]["source_locator"] == {
        "kind": "json_pointer",
        "json_pointer": "/security_controls"
    }
    assert draft.system_context_proposal is not None
    assert draft.system_context_proposal["status"] == "proposed"
    assert draft.system_context_proposal["requires_human_approval"] is True
    assert "system_context_proposal" in draft.document["extensions"]
    fisma = draft.document["fisma_agency_security"]
    assert isinstance(fisma.get("customer_defined_fields"), dict)
    validate_package_draft_document(draft.document)


def test_fedramp_20x_profile_shell_is_schema_valid() -> None:
    draft = build_initial_draft(
        revision=_revision(profile_id="fedramp_20x_program"),
        system=_system(),
        artifacts=[],
        artifact_outcomes=[],
    )

    assert draft.document["fedramp_20x"] is not None
    assert draft.document["fisma_agency_security"] is None
    assert draft.document["system"]["impact_level"] is None
    validate_package_draft_document(draft.document)


def test_fedramp_rev5_profile_shell_is_schema_valid() -> None:
    draft = build_initial_draft(
        revision=_revision(profile_id="fedramp_rev5_transition"),
        system=_system(),
        artifacts=[],
        artifact_outcomes=[],
    )

    assert draft.document["fedramp_rev5_transition"] is not None
    assert draft.document["fisma_agency_security"] is None
    validate_package_draft_document(draft.document)


def test_pdf_segments_land_in_extensions_unmapped_segments() -> None:
    artifact = _artifact(content=b"%PDF-1.4 demo")
    outcome = SimpleNamespace(
        status="succeeded",
        detected_format="pdf",
        detected_media_type="application/pdf",
        page_count=1,
        total_text_characters=12,
        vision_status="not_needed",
        segments=(
            SimpleNamespace(
                segment_index=0,
                text="Narrative body",
                locator={"page": 1, "char_range": [0, 12]},
                extraction_method="text",
                metadata=None,
            ),
        ),
    )

    draft = build_initial_draft(
        revision=_revision(),
        system=_system(),
        artifacts=[artifact],
        artifact_outcomes=[(artifact, outcome)],
    )

    segments = draft.document["extensions"]["unmapped_segments"]
    assert len(segments) == 1
    assert segments[0]["detected_format"] == "pdf"
    assert segments[0]["text"] == "Narrative body"
    validate_package_draft_document(draft.document)


def test_evidence_only_outcome_is_preserved_in_extensions() -> None:
    artifact = _artifact(content=b"\x89PNG\r\n\x1a\n")
    outcome = SimpleNamespace(
        status="evidence_only",
        detected_format="png",
        detected_media_type="image/png",
        page_count=None,
        total_text_characters=0,
        vision_status="evidence_only",
        segments=(),
    )

    draft = build_initial_draft(
        revision=_revision(),
        system=_system(),
        artifacts=[artifact],
        artifact_outcomes=[(artifact, outcome)],
    )

    records = draft.document["extensions"]["evidence_only_artifacts"]
    assert records[0]["status"] == "evidence_only"
    assert records[0]["vision_status"] == "evidence_only"
    validate_package_draft_document(draft.document)


def test_duplicate_draft_pointer_conflict_from_two_manifests() -> None:
    first_content = b'{"system":{"name":"First"}}'
    second_content = b'{"system":{"name":"Second"}}'
    first = _artifact(
        content=first_content,
        artifact_id=uuid.UUID("44444444-4444-4444-8444-444444444444"),
    )
    second = _artifact(
        content=second_content,
        artifact_id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
    )

    with pytest.raises(DraftBuildError) as exc_info:
        build_initial_draft(
            revision=_revision(),
            system=_system(),
            artifacts=[first, second],
            artifact_outcomes=[
                (first, _extract_json_outcome(first_content)),
                (second, _extract_json_outcome(second_content)),
            ],
        )

    assert exc_info.value.error_code == "duplicate_canonical_id"


def test_identical_duplicate_values_reject_ambiguous_provenance() -> None:
    content = b'{"system":{"name":"Same"}}'
    first = _artifact(
        content=content,
        artifact_id=uuid.UUID("44444444-4444-4444-8444-444444444444"),
    )
    second = _artifact(
        content=content,
        artifact_id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
    )

    with pytest.raises(DraftBuildError) as exc_info:
        build_initial_draft(
            revision=_revision(),
            system=_system(),
            artifacts=[first, second],
            artifact_outcomes=[
                (first, _extract_json_outcome(content)),
                (second, _extract_json_outcome(content)),
            ],
        )

    assert exc_info.value.error_code == "duplicate_canonical_id"


def test_rejects_outcome_for_artifact_outside_revision() -> None:
    content = b'{"system":{"name":"Unexpected"}}'
    artifact = _artifact(content=content)

    with pytest.raises(DraftBuildError) as exc_info:
        build_initial_draft(
            revision=_revision(),
            system=_system(),
            artifacts=[],
            artifact_outcomes=[(artifact, _extract_json_outcome(content))],
        )

    assert exc_info.value.error_code == "draft_schema_invalid"


def test_assembled_document_validates_against_package_draft_schema() -> None:
    content = FISMA_FIXTURE_PATH.read_bytes()
    artifact = _artifact(content=content)
    draft = build_initial_draft(
        revision=_revision(),
        system=_system(),
        artifacts=[artifact],
        artifact_outcomes=[(artifact, _extract_json_outcome(content))],
    )

    assert isinstance(draft, AggregatedIntakeDraft)
    validate_package_draft_document(draft.document)
