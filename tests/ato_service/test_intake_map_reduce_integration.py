"""Service-level MAP→REDUCE integration tests for Phase 3 intake."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

import pytest

from ato_service.draft_builder import AggregatedIntakeDraft
from ato_service.extraction.types import ExtractionOutcome, ExtractedSegment
from ato_service.intake import (
    ArtifactSnapshot,
    IntakeRevisionSnapshot,
    _apply_intake_reduce,
)
from ato_service.intake_map import (
    IntakeMapStepResult as MapOrchestrationStepResult,
    PendingIntakeMapOutcome,
    ParsedMapFact,
    ParsedMapResponse,
    ParsedMapSuggestions,
)

REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ARTIFACT_ID_B = uuid.UUID("66666666-6666-4666-8666-666666666666")
STEP_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
STEP_ID_B = uuid.UUID("55555555-5555-4555-8555-555555555555")
SHA256 = "a" * 64
CHUNK_ID = f"{ARTIFACT_ID}:1"


def _text_locator() -> dict[str, Any]:
    return {
        "kind": "text_offsets",
        "start_byte": 0,
        "end_byte": 120,
        "normalized_start": 0,
        "normalized_end": 120,
    }


def _empty_fisma_document() -> dict[str, Any]:
    return {
        "package": {
            "profile_id": "fisma_agency_security",
            "title": "",
            "prepared_for": "",
            "reporting_period": None,
        },
        "system": {
            "display_name": "Demo System",
            "authorization_boundary": "",
            "mission_summary": "",
            "impact_level": None,
            "authorization_path": "agency",
        },
        "contacts": {
            "system_owner": [],
            "isso": [],
            "issm": [],
            "control_owners": [],
            "assessors": [],
            "approvers": [],
        },
        "control_set": {
            "source": {},
            "tailoring": [],
            "organization_defined_parameters": {},
            "inheritance": [],
        },
        "security_controls": {},
        "evidence": {},
        "findings": {},
        "poam_candidates": {},
        "assessor_inputs": {},
        "privacy": {
            "artifacts_present": False,
            "scope_notice": "Privacy review is external to this product.",
        },
        "fedramp_20x": None,
        "fedramp_rev5_transition": None,
        "fisma_agency_security": {"security_plan_sections": {}},
        "extensions": {},
    }


def _artifact_snapshot() -> ArtifactSnapshot:
    return ArtifactSnapshot(
        artifact_id=ARTIFACT_ID,
        package_revision_id=REVISION_ID,
        display_filename="security-plan.txt",
        storage_key="ab/" + SHA256,
        sha256=SHA256,
        size_bytes=120,
        declared_media_type="text/plain",
        detected_media_type="text/plain",
        artifact_kind="evidence_document",
        malware_scan_status="clean",
        extraction_status="pending",
    )


def _extraction_outcome(*, text: str) -> ExtractionOutcome:
    return ExtractionOutcome(
        status="succeeded",
        detected_format="text",
        detected_media_type="text/plain",
        page_count=None,
        total_text_characters=len(text),
        vision_status="not_needed",
        segments=(
            ExtractedSegment(
                segment_index=1,
                text=text,
                locator=_text_locator(),
                extraction_method="text",
            ),
        ),
    )


def _revision_snapshot(*, profile_id: str | None = "fisma_agency_security") -> IntakeRevisionSnapshot:
    return IntakeRevisionSnapshot(
        package_revision_id=REVISION_ID,
        revision_version=3,
        status="extracting",
        profile_id=profile_id,
        impact_level="moderate",
        content_manifest_sha256="c" * 64,
        data_origin=None,
        sensitivity=None,
        system_id=SYSTEM_ID,
        system_display_name="MAP REDUCE Test",
        artifacts=(_artifact_snapshot(),),
    )


def _deterministic_draft() -> AggregatedIntakeDraft:
    return AggregatedIntakeDraft(
        document=_empty_fisma_document(),
        field_provenance={},
        system_context_proposal=None,
        segment_count=1,
    )


def _map_step_result(
    *,
    artifact_id: uuid.UUID = ARTIFACT_ID,
    step_id: uuid.UUID = STEP_ID,
    validation_outcome: str = "accepted",
    parsed: ParsedMapResponse | None = None,
    omitted_chunk_ids: tuple[str, ...] = (),
    context_complete: bool = True,
) -> MapOrchestrationStepResult:
    return MapOrchestrationStepResult(
        artifact_id=artifact_id,
        step_id=step_id,
        step_key=f"imap_{artifact_id.hex}",
        input_digest="d" * 64,
        validation_outcome=validation_outcome,  # type: ignore[arg-type]
        llm_call_count=1 if validation_outcome in {"accepted", "repair_succeeded"} else 0,
        omitted_chunk_ids=omitted_chunk_ids,
        context_complete=context_complete,
        fact_bundle_sha256="e" * 64,
        prompt_version="1.0.0",
        prompt_sha256="f" * 64,
        response_sha256="0" * 64 if validation_outcome in {"accepted", "repair_succeeded"} else None,
        parsed_response=parsed,
        error_code=None,
    )


def _accepted_response(*, title: str = "Accepted MAP Title") -> ParsedMapResponse:
    return ParsedMapResponse(
        facts=(
            ParsedMapFact(
                fact_key="package.title",
                value=title,
                value_kind="direct_evidence",
                source_artifact_id=ARTIFACT_ID,
                segment_index=1,
                chunk_ids=(CHUNK_ID,),
                confidence="high",
            ),
        ),
        suggestions=ParsedMapSuggestions(
            profile_id="fisma_agency_security",
            impact_level="moderate",
            certification_class=None,
        ),
    )


def _artifact_outcomes() -> tuple[tuple[ArtifactSnapshot, ExtractionOutcome], ...]:
    return ((_artifact_snapshot(), _extraction_outcome(text="System title text")),)


def test_accepted_map_merges_into_draft_with_provenance() -> None:
    intake_map = PendingIntakeMapOutcome(
        skipped=False,
        reconciliation_required=False,
        step_results=(
            _map_step_result(parsed=_accepted_response()),
        ),
        protected_artifacts={},
        runtime_metadata={"schema_id": "test", "prompt_version": "1.0.0"},
    )

    outcome = _apply_intake_reduce(
        aggregated=_deterministic_draft(),
        snapshot=_revision_snapshot(),
        intake_map=intake_map,
        artifact_outcomes=_artifact_outcomes(),
    )

    assert outcome.merge_result.document["package"]["title"] == "Accepted MAP Title"
    provenance = outcome.merge_result.field_provenance["/package/title"]
    assert provenance["source_sha256"] == SHA256
    assert provenance["extraction_method"] == "llm_normalize"
    assert outcome.audit_metadata["merged_target_count"] == 1


def test_conflict_produces_unresolved_record_without_overwrite() -> None:
    intake_map = PendingIntakeMapOutcome(
        skipped=False,
        reconciliation_required=False,
        step_results=(
            _map_step_result(
                parsed=_accepted_response(title="First Title"),
                context_complete=True,
            ),
            _map_step_result(
                parsed=_accepted_response(title="Second Title"),
                step_id=STEP_ID_B,
                context_complete=True,
            ),
        ),
        protected_artifacts={},
        runtime_metadata={"schema_id": "test", "prompt_version": "1.0.0"},
    )

    outcome = _apply_intake_reduce(
        aggregated=_deterministic_draft(),
        snapshot=_revision_snapshot(),
        intake_map=intake_map,
        artifact_outcomes=_artifact_outcomes(),
    )

    assert outcome.merge_result.document["package"]["title"] == ""
    assert len(outcome.merge_result.conflicts) == 1
    assert outcome.merge_result.conflicts[0].resolution == "unresolved"


def test_omitted_context_surfaces_in_extensions() -> None:
    intake_map = PendingIntakeMapOutcome(
        skipped=False,
        reconciliation_required=False,
        step_results=(
            _map_step_result(
                parsed=_accepted_response(),
                omitted_chunk_ids=(CHUNK_ID,),
                context_complete=False,
            ),
        ),
        protected_artifacts={},
        runtime_metadata={"schema_id": "test", "prompt_version": "1.0.0"},
    )

    outcome = _apply_intake_reduce(
        aggregated=_deterministic_draft(),
        snapshot=_revision_snapshot(),
        intake_map=intake_map,
        artifact_outcomes=_artifact_outcomes(),
    )

    extensions = outcome.merge_result.document["extensions"]
    assert extensions["intake_context_complete"] is False
    assert extensions["intake_omitted_chunks"][0]["chunk_id"] == CHUNK_ID


def test_pre_attestation_skipped_map_still_commits_deterministic_draft() -> None:
    intake_map = PendingIntakeMapOutcome(
        skipped=False,
        reconciliation_required=False,
        step_results=(
            _map_step_result(
                validation_outcome="skipped_pre_attestation_policy",
                parsed=None,
                context_complete=True,
            ),
        ),
        protected_artifacts={},
        runtime_metadata={"schema_id": "test", "prompt_version": "1.0.0"},
    )

    outcome = _apply_intake_reduce(
        aggregated=_deterministic_draft(),
        snapshot=_revision_snapshot(),
        intake_map=intake_map,
        artifact_outcomes=_artifact_outcomes(),
    )

    assert outcome.merge_result.document["package"]["title"] == ""
    assert outcome.audit_metadata["merged_target_count"] == 0
    assert outcome.merge_result.context_complete is True


def test_rejected_policy_does_not_pollute_draft_with_model_values() -> None:
    intake_map = PendingIntakeMapOutcome(
        skipped=False,
        reconciliation_required=False,
        step_results=(
            _map_step_result(
                validation_outcome="rejected_policy",
                parsed=None,
                context_complete=True,
            ),
        ),
        protected_artifacts={},
        runtime_metadata={"schema_id": "test", "prompt_version": "1.0.0"},
    )

    outcome = _apply_intake_reduce(
        aggregated=_deterministic_draft(),
        snapshot=_revision_snapshot(),
        intake_map=intake_map,
        artifact_outcomes=_artifact_outcomes(),
    )

    assert outcome.merge_result.document["package"]["title"] == ""
    assert outcome.merge_result.field_provenance == {}
    assert outcome.merge_result.merged_targets == ()


def test_no_map_phase_preserves_deterministic_draft_only() -> None:
    outcome = _apply_intake_reduce(
        aggregated=_deterministic_draft(),
        snapshot=_revision_snapshot(),
        intake_map=None,
        artifact_outcomes=_artifact_outcomes(),
    )

    assert outcome.audit_metadata == {}
    assert outcome.merge_result.document["package"]["title"] == ""


def test_reduce_replay_digest_is_stable() -> None:
    intake_map = PendingIntakeMapOutcome(
        skipped=False,
        reconciliation_required=False,
        step_results=(_map_step_result(parsed=_accepted_response()),),
        protected_artifacts={},
        runtime_metadata={"schema_id": "test", "prompt_version": "1.0.0"},
    )
    kwargs = {
        "aggregated": _deterministic_draft(),
        "snapshot": _revision_snapshot(),
        "intake_map": intake_map,
        "artifact_outcomes": _artifact_outcomes(),
    }
    first = _apply_intake_reduce(**kwargs)
    second = _apply_intake_reduce(**kwargs)
    assert first.merge_digest == second.merge_digest
