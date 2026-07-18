"""Focused tests for deterministic intake REDUCE merge (Phase 3C)."""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest

from ato_service.draft_builder import validate_package_draft_document
from ato_service.intake_merge import (
    IntakeMergeError,
    adapt_orchestrated_map_steps_for_reduce,
    finalize_intake_merge_result,
    merge_result_digest,
    reduce_intake_map_results,
    target_pointer_for_map_fact_key,
    validate_intake_map_step_result,
)
from ato_service.intake_map import (
    IntakeMapStepResult as MapOrchestrationStepResult,
    ParsedMapFact,
    ParsedMapResponse,
    ParsedMapSuggestions,
)
from ato_service.normalize_proposal.constants import PROHIBITED_TARGET_PREFIXES

ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
STEP_ID_A = uuid.UUID("44444444-4444-4444-8444-444444444444")
STEP_ID_B = uuid.UUID("55555555-5555-4555-8555-555555555555")
SHA256 = "a" * 64
CHUNK_ID = "chunk-" + ("b" * 59)


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


def _proposal(
    *,
    target_pointer: str = "/system/mission_summary",
    proposed_value: Any = "Mission from MAP.",
    evidence_kind: str = "direct_evidence",
    artifact_id: uuid.UUID = ARTIFACT_ID,
    model_step_id: uuid.UUID = STEP_ID_A,
    chunk_id: str = CHUNK_ID,
    segment_index: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target_pointer": target_pointer,
        "proposed_value": proposed_value,
        "evidence_kind": evidence_kind,
        "source_artifact_id": str(artifact_id).lower(),
        "source_sha256": SHA256,
        "source_locator": {"kind": "chunk", "chunk_id": chunk_id},
        "model_step_id": str(model_step_id).lower(),
        "confidence": "high",
    }
    if segment_index is not None:
        payload["segment_index"] = segment_index
    else:
        payload["chunk_id"] = chunk_id
    return payload


def _map_step(
    *,
    step_id: uuid.UUID = STEP_ID_A,
    step_key: str = "intake_map_000",
    context_complete: bool = True,
    proposals: list[dict[str, Any]] | None = None,
    metadata_suggestions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "step_id": str(step_id).lower(),
        "step_key": step_key,
        "context_complete": context_complete,
        "proposals": proposals or [_proposal()],
        "metadata_suggestions": metadata_suggestions or {},
    }


def test_merge_writes_single_proposal_with_provenance() -> None:
    result = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=_empty_fisma_document(),
        base_field_provenance={},
        map_step_results=[_map_step()],
        system_display_name="Demo System",
    )

    assert result.document["system"]["mission_summary"] == "Mission from MAP."
    provenance = result.field_provenance["/system/mission_summary"]
    assert provenance["extraction_method"] == "llm_normalize"
    assert provenance["source_locator"]["evidence_kind"] == "direct_evidence"
    assert "/system/mission_summary" in result.merged_targets
    validate_package_draft_document(result.document)


def test_merge_deduplicates_same_pointer_and_value() -> None:
    second_artifact = uuid.UUID("66666666-6666-4666-8666-666666666666")
    step_a = _map_step(
        step_id=STEP_ID_A,
        step_key="intake_map_000",
        proposals=[_proposal(chunk_id="chunk-a" + ("c" * 55))],
    )
    step_b = _map_step(
        step_id=STEP_ID_B,
        step_key="intake_map_001",
        proposals=[
            _proposal(
                artifact_id=second_artifact,
                model_step_id=STEP_ID_B,
                chunk_id="chunk-b" + ("d" * 55),
            )
        ],
    )

    result = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=_empty_fisma_document(),
        base_field_provenance={},
        map_step_results=[step_b, step_a],
        system_display_name="Demo System",
    )

    assert result.document["system"]["mission_summary"] == "Mission from MAP."
    supplements = result.document["extensions"]["intake_provenance_supplements"]
    assert "/system/mission_summary" in supplements
    assert len(supplements["/system/mission_summary"]) == 1
    assert result.conflicts == ()


def test_merge_emits_conflict_for_same_pointer_different_values() -> None:
    step_a = _map_step(
        proposals=[_proposal(proposed_value="First mission.")],
    )
    step_b = _map_step(
        step_id=STEP_ID_B,
        step_key="intake_map_001",
        proposals=[_proposal(proposed_value="Second mission.", model_step_id=STEP_ID_B)],
    )

    result = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=_empty_fisma_document(),
        base_field_provenance={},
        map_step_results=[step_a, step_b],
        system_display_name="Demo System",
    )

    assert result.document["system"]["mission_summary"] == ""
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.target_pointer == "/system/mission_summary"
    assert conflict.resolution == "unresolved"
    assert len(conflict.candidates) == 2
    validate_package_draft_document(result.document)


def test_merge_conflict_with_deterministic_base_does_not_invalidate_draft() -> None:
    base = _empty_fisma_document()
    base["system"]["mission_summary"] = "Deterministic mission."
    provenance = {
        "/system/mission_summary": {
            "source_artifact_id": str(ARTIFACT_ID).lower(),
            "source_sha256": SHA256,
            "source_locator": {"kind": "json_pointer", "json_pointer": "/system/mission_summary"},
            "extraction_method": "deterministic",
            "model_step_id": None,
        }
    }

    result = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=base,
        base_field_provenance=provenance,
        map_step_results=[
            _map_step(proposals=[_proposal(proposed_value="Conflicting MAP mission.")])
        ],
        system_display_name="Demo System",
    )

    assert result.document["system"]["mission_summary"] == "Deterministic mission."
    assert len(result.conflicts) == 1
    validate_package_draft_document(result.document)


def test_merge_rejects_prohibited_prefix() -> None:
    for prefix in PROHIBITED_TARGET_PREFIXES:
        with pytest.raises(IntakeMergeError) as exc_info:
            reduce_intake_map_results(
                profile_id="fisma_agency_security",
                base_document=_empty_fisma_document(),
                base_field_provenance={},
                map_step_results=[
                    _map_step(proposals=[_proposal(target_pointer=prefix)])
                ],
                system_display_name="Demo System",
            )
        assert exc_info.value.error_code == "intake_merge_invalid_input"


def test_merge_rejects_human_only_metadata_suggestions() -> None:
    with pytest.raises(IntakeMergeError) as exc_info:
        validate_intake_map_step_result(
            _map_step(metadata_suggestions={"data_origin": "owner_attested"})
        )
    assert exc_info.value.error_code == "intake_merge_invalid_input"


def test_merge_rejects_unsupported_schema_version() -> None:
    payload = _map_step()
    payload["schema_version"] = "9.9.9"
    with pytest.raises(IntakeMergeError) as exc_info:
        validate_intake_map_step_result(payload)
    assert exc_info.value.error_code == "intake_merge_invalid_input"


def test_merge_rejects_missing_citation_anchor() -> None:
    proposal = _proposal()
    proposal.pop("chunk_id")
    with pytest.raises(IntakeMergeError):
        validate_intake_map_step_result(_map_step(proposals=[proposal]))


def test_merge_rejects_duplicate_proposal_identity_in_one_step() -> None:
    duplicate = _proposal()
    with pytest.raises(IntakeMergeError) as exc_info:
        validate_intake_map_step_result(_map_step(proposals=[duplicate, copy.deepcopy(duplicate)]))
    assert exc_info.value.error_code == "duplicate_canonical_id"


def test_merge_replay_is_idempotent() -> None:
    kwargs = {
        "profile_id": "fisma_agency_security",
        "base_document": _empty_fisma_document(),
        "base_field_provenance": {},
        "map_step_results": [_map_step()],
        "system_display_name": "Demo System",
    }
    first = reduce_intake_map_results(**kwargs)
    second = reduce_intake_map_results(**kwargs)
    assert merge_result_digest(first) == merge_result_digest(second)
    assert first.document == second.document
    assert first.field_provenance == second.field_provenance


def test_merge_labels_inference_separately() -> None:
    result = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=_empty_fisma_document(),
        base_field_provenance={},
        map_step_results=[
            _map_step(
                proposals=[
                    _proposal(
                        target_pointer="/system/authorization_boundary",
                        proposed_value="Boundary inferred from context.",
                        evidence_kind="inference",
                    )
                ]
            )
        ],
        system_display_name="Demo System",
    )

    locator = result.field_provenance["/system/authorization_boundary"]["source_locator"]
    assert locator["evidence_kind"] == "inference"


def test_merge_context_complete_requires_all_steps() -> None:
    result = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=_empty_fisma_document(),
        base_field_provenance={},
        map_step_results=[
            _map_step(context_complete=True),
            _map_step(
                step_id=STEP_ID_B,
                step_key="intake_map_001",
                context_complete=False,
                proposals=[],
            ),
        ],
        system_display_name="Demo System",
    )

    assert result.context_complete is False
    assert result.document["extensions"]["intake_context_complete"] is False
    assert any(gap.reason == "map_step_context_incomplete" for gap in result.gaps)


def test_merge_metadata_suggestion_conflict() -> None:
    result = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=_empty_fisma_document(),
        base_field_provenance={},
        map_step_results=[
            _map_step(metadata_suggestions={"impact_level": "moderate"}),
            _map_step(
                step_id=STEP_ID_B,
                step_key="intake_map_001",
                proposals=[],
                metadata_suggestions={"impact_level": "high"},
            ),
        ],
        system_display_name="Demo System",
    )

    assert "impact_level" not in result.metadata_suggestions
    assert any(
        conflict.target_pointer == "/_intake_metadata/impact_level"
        for conflict in result.conflicts
    )


def test_fact_key_allowlist_maps_dot_notation_to_pointer() -> None:
    assert target_pointer_for_map_fact_key("package.title") == "/package/title"
    assert target_pointer_for_map_fact_key("system.mission_summary") == "/system/mission_summary"
    assert target_pointer_for_map_fact_key("data_origin") is None


def test_adapter_binds_trusted_provenance_and_rejects_unmapped_facts() -> None:
    orchestration = MapOrchestrationStepResult(
        artifact_id=ARTIFACT_ID,
        step_id=STEP_ID_A,
        step_key="imap_test",
        input_digest="d" * 64,
        validation_outcome="accepted",
        llm_call_count=1,
        omitted_chunk_ids=("chunk-omitted",),
        context_complete=False,
        fact_bundle_sha256="e" * 64,
        prompt_version="1.0.0",
        prompt_sha256="f" * 64,
        response_sha256="0" * 64,
        parsed_response=ParsedMapResponse(
            facts=(
                ParsedMapFact(
                    fact_key="package.title",
                    value="Mapped Title",
                    value_kind="direct_evidence",
                    source_artifact_id=ARTIFACT_ID,
                    segment_index=1,
                    chunk_ids=(CHUNK_ID,),
                    confidence="high",
                ),
                ParsedMapFact(
                    fact_key="unknown.field",
                    value="ignored",
                    value_kind="direct_evidence",
                    source_artifact_id=ARTIFACT_ID,
                    segment_index=1,
                    chunk_ids=(CHUNK_ID,),
                    confidence="low",
                ),
            ),
            suggestions=ParsedMapSuggestions(
                profile_id="fisma_agency_security",
                impact_level="moderate",
                certification_class=None,
            ),
        ),
    )
    adapted = adapt_orchestrated_map_steps_for_reduce(
        (orchestration,),
        artifact_sha_by_id={ARTIFACT_ID: SHA256},
        segment_locators_by_artifact={
            ARTIFACT_ID: {1: {"kind": "text_offsets", "start_byte": 0, "end_byte": 10}}
        },
    )

    assert len(adapted.reduce_steps) == 1
    proposal = adapted.reduce_steps[0].proposals[0]
    assert proposal.target_pointer == "/package/title"
    assert proposal.source_sha256 == SHA256
    assert proposal.model_step_id == STEP_ID_A
    assert adapted.omitted_chunks[0]["chunk_id"] == "chunk-omitted"
    assert any(gap.reason.startswith("unmapped_fact_key:") for gap in adapted.adaptation_gaps)

    merge = reduce_intake_map_results(
        profile_id="fisma_agency_security",
        base_document=_empty_fisma_document(),
        base_field_provenance={},
        map_step_results=adapted.reduce_steps,
        system_display_name="Demo System",
    )
    finalized = finalize_intake_merge_result(
        merge,
        adaptation_gaps=adapted.adaptation_gaps,
        omitted_chunks=adapted.omitted_chunks,
    )
    assert finalized.document["package"]["title"] == "Mapped Title"
    assert finalized.document["extensions"]["intake_omitted_chunks"]
    validate_package_draft_document(finalized.document)
