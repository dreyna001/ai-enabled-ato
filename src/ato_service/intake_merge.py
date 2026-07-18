"""Deterministic REDUCE merge for Phase 3 intake MAP outputs."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from ato_service.draft_builder import (
    DOCUMENT_SCHEMA_VERSION,
    build_system_context_proposal_from_draft,
    validate_package_draft_document,
)
from ato_service.normalize_proposal.constants import PROHIBITED_TARGET_PREFIXES
from ato_service.normalize_proposal.json_utils import (
    is_valid_json_pointer,
    set_json_pointer,
    stable_json_dumps,
    value_at_json_pointer,
)
from ato_service.normalize_proposal.target_catalog import (
    catalog_for_profile,
    is_prohibited_target,
    is_target_allowed,
)

from ato_service.intake_map import (
    IntakeMapStepResult as MapOrchestrationStepResult,
    ParsedMapFact,
    ParsedMapResponse,
    ParsedMapSuggestions,
)
from ato_service.normalize_proposal.value_validation import validate_proposed_value_for_target

MAP_RESULT_SCHEMA_VERSION = "1.0.0"
METADATA_CONFLICT_PREFIX = "/_intake_metadata"
_HUMAN_ONLY_TARGET_MARKERS = ("data_origin", "sensitivity")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_FORMAT_CHECKER = FormatChecker()
_MAP_SUCCESS_OUTCOMES = frozenset({"accepted", "repair_succeeded"})
_PROFILE_IDS_FOR_FACT_KEYS = (
    "fisma_agency_security",
    "fedramp_20x_program",
    "fedramp_rev5_transition",
)


class IntakeMergeError(ValueError):
    """Raised when deterministic intake merge cannot proceed safely."""

    def __init__(self, message: str, *, error_code: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IntakeMapProposal:
    """One validated MAP field proposal ready for REDUCE."""

    target_pointer: str
    proposed_value: Any
    evidence_kind: str
    source_artifact_id: uuid.UUID
    source_sha256: str
    source_locator: dict[str, Any]
    model_step_id: uuid.UUID
    confidence: str
    chunk_id: str | None = None
    segment_index: int | None = None
    step_key: str = ""
    step_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class IntakeMapStepResult:
    """One validated MAP step artifact consumed by REDUCE."""

    step_id: uuid.UUID
    step_key: str
    context_complete: bool
    proposals: tuple[IntakeMapProposal, ...]
    metadata_suggestions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntakeFieldConflict:
    """Persisted conflict requiring human resolution."""

    conflict_id: str
    target_pointer: str
    resolution: str
    candidates: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class IntakeGap:
    """Catalog target or coverage gap surfaced during REDUCE."""

    target_pointer: str
    reason: str
    step_key: str | None = None


@dataclass(frozen=True, slots=True)
class IntakeMergeResult:
    """Deterministic REDUCE output for draft persistence."""

    document: dict[str, Any]
    field_provenance: dict[str, Any]
    conflicts: tuple[IntakeFieldConflict, ...]
    gaps: tuple[IntakeGap, ...]
    metadata_suggestions: dict[str, Any]
    context_complete: bool
    system_context_proposal: dict[str, Any] | None
    merged_targets: tuple[str, ...]
    rejected_proposals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdaptedMapReduceInput:
    """Deterministic MAP orchestration output adapted for REDUCE."""

    reduce_steps: tuple[IntakeMapStepResult, ...]
    adaptation_gaps: tuple[IntakeGap, ...]
    omitted_chunks: tuple[dict[str, Any], ...]


def reduce_intake_map_results(
    *,
    profile_id: str | None,
    base_document: dict[str, Any],
    base_field_provenance: dict[str, Any],
    map_step_results: Sequence[IntakeMapStepResult | Mapping[str, Any]],
    system_display_name: str,
) -> IntakeMergeResult:
    """Merge validated MAP outputs into one schema-valid draft package document."""
    validated_steps = tuple(
        _coerce_map_step_result(step, index=index)
        for index, step in enumerate(map_step_results)
    )
    merged_document = copy.deepcopy(base_document)
    merged_provenance = copy.deepcopy(base_field_provenance)
    extensions = merged_document.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        raise IntakeMergeError(
            "draft extensions must be an object",
            error_code="draft_schema_invalid",
        )

    proposals = _sorted_proposals(validated_steps)
    grouped: dict[str, list[IntakeMapProposal]] = {}
    for proposal in proposals:
        grouped.setdefault(proposal.target_pointer, []).append(proposal)

    conflicts: list[IntakeFieldConflict] = []
    gaps: list[IntakeGap] = []
    merged_targets: list[str] = []
    rejected_proposals: list[str] = []
    provenance_supplements: dict[str, list[dict[str, Any]]] = {}

    for target_pointer in sorted(grouped):
        entries = grouped[target_pointer]
        conflict = _merge_target_group(
            profile_id=profile_id,
            target_pointer=target_pointer,
            entries=entries,
            document=merged_document,
            provenance=merged_provenance,
            provenance_supplements=provenance_supplements,
        )
        if conflict is None:
            if target_pointer in merged_provenance:
                merged_targets.append(target_pointer)
            continue
        if conflict.resolution == "rejected":
            rejected_proposals.append(target_pointer)
            continue
        conflicts.append(conflict)

    if profile_id is not None:
        for spec in catalog_for_profile(profile_id):
            pointer = spec.pointer
            if pointer in grouped or pointer in conflicts_by_target(conflicts):
                continue
            try:
                current = value_at_json_pointer(merged_document, pointer)
            except (KeyError, IndexError, TypeError):
                current = None
            if current in (None, "", [], {}):
                gaps.append(
                    IntakeGap(
                        target_pointer=pointer,
                        reason="catalog_target_unfilled",
                    )
                )

    for step in validated_steps:
        if step.context_complete:
            continue
        gaps.append(
            IntakeGap(
                target_pointer="",
                reason="map_step_context_incomplete",
                step_key=step.step_key,
            )
        )

    metadata_suggestions, metadata_conflicts = _aggregate_metadata_suggestions(
        validated_steps
    )
    conflicts.extend(metadata_conflicts)

    context_complete = all(step.context_complete for step in validated_steps)
    extensions["intake_conflicts"] = [
        _conflict_to_dict(conflict) for conflict in _sorted_conflicts(conflicts)
    ]
    extensions["intake_gaps"] = [_gap_to_dict(gap) for gap in gaps]
    extensions["intake_metadata_suggestions"] = metadata_suggestions
    extensions["intake_context_complete"] = context_complete
    if provenance_supplements:
        extensions["intake_provenance_supplements"] = {
            pointer: supplements
            for pointer, supplements in sorted(provenance_supplements.items())
        }

    system_context_proposal = build_system_context_proposal_from_draft(
        document=merged_document,
        provenance=merged_provenance,
        system_display_name=system_display_name,
    )
    if system_context_proposal is not None:
        extensions["system_context_proposal"] = system_context_proposal

    validate_package_draft_document(merged_document)
    return IntakeMergeResult(
        document=merged_document,
        field_provenance=merged_provenance,
        conflicts=tuple(_sorted_conflicts(conflicts)),
        gaps=tuple(gaps),
        metadata_suggestions=metadata_suggestions,
        context_complete=context_complete,
        system_context_proposal=system_context_proposal,
        merged_targets=tuple(sorted(set(merged_targets))),
        rejected_proposals=tuple(sorted(set(rejected_proposals))),
    )


def merge_result_digest(result: IntakeMergeResult) -> str:
    """Return a stable digest for replay/idempotency comparisons."""
    payload = {
        "document": result.document,
        "field_provenance": result.field_provenance,
        "conflicts": [_conflict_to_dict(item) for item in result.conflicts],
        "gaps": [_gap_to_dict(item) for item in result.gaps],
        "metadata_suggestions": result.metadata_suggestions,
        "context_complete": result.context_complete,
        "merged_targets": list(result.merged_targets),
        "rejected_proposals": list(result.rejected_proposals),
    }
    return hashlib.sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()


def validate_intake_map_step_result(payload: Mapping[str, Any]) -> IntakeMapStepResult:
    """Validate one MAP step payload against the published REDUCE input contract."""
    return _coerce_map_step_result(payload, index=0)


@cache
def _allowed_map_fact_keys() -> frozenset[str]:
    keys: set[str] = set()
    for profile_id in _PROFILE_IDS_FOR_FACT_KEYS:
        for spec in catalog_for_profile(profile_id):
            keys.add(spec.pointer.lstrip("/").replace("/", "."))
    return frozenset(keys)


def target_pointer_for_map_fact_key(fact_key: str) -> str | None:
    """Return the canonical draft JSON pointer for one allowlisted MAP fact_key."""
    if fact_key not in _allowed_map_fact_keys():
        return None
    return "/" + fact_key.replace(".", "/")


def adapt_orchestrated_map_steps_for_reduce(
    map_step_results: Sequence[MapOrchestrationStepResult],
    *,
    artifact_sha_by_id: Mapping[uuid.UUID, str],
    segment_locators_by_artifact: Mapping[uuid.UUID, Mapping[int, dict[str, Any]]],
) -> AdaptedMapReduceInput:
    """Adapt validated MAP orchestration results into the REDUCE input contract."""
    reduce_steps: list[IntakeMapStepResult] = []
    adaptation_gaps: list[IntakeGap] = []
    omitted_chunks: list[dict[str, Any]] = []

    for orchestration in map_step_results:
        artifact_sha256 = artifact_sha_by_id.get(orchestration.artifact_id)
        if artifact_sha256 is None:
            raise IntakeMergeError(
                "MAP step artifact is missing from trusted extraction snapshot",
                error_code="intake_merge_invalid_input",
            )

        segment_locators = segment_locators_by_artifact.get(orchestration.artifact_id, {})
        for chunk_id in orchestration.omitted_chunk_ids:
            omitted_chunks.append(
                {
                    "artifact_id": str(orchestration.artifact_id).lower(),
                    "chunk_id": chunk_id,
                    "step_key": orchestration.step_key,
                }
            )

        proposals: list[IntakeMapProposal] = []
        metadata_suggestions: dict[str, Any] = {}
        if (
            orchestration.parsed_response is not None
            and orchestration.validation_outcome in _MAP_SUCCESS_OUTCOMES
        ):
            metadata_suggestions = _metadata_from_map_suggestions(
                orchestration.parsed_response.suggestions
            )
            for fact in orchestration.parsed_response.facts:
                proposal, gap = _proposal_from_map_fact(
                    fact=fact,
                    artifact_sha256=artifact_sha256,
                    segment_locators=segment_locators,
                    model_step_id=orchestration.step_id,
                    step_key=orchestration.step_key,
                )
                if proposal is not None:
                    proposals.append(proposal)
                elif gap is not None:
                    adaptation_gaps.append(gap)

        reduce_steps.append(
            IntakeMapStepResult(
                step_id=orchestration.step_id,
                step_key=orchestration.step_key,
                context_complete=orchestration.context_complete,
                proposals=tuple(proposals),
                metadata_suggestions=metadata_suggestions,
            )
        )

    omitted_chunks.sort(
        key=lambda item: (
            item["artifact_id"],
            item["chunk_id"],
            item["step_key"],
        )
    )
    return AdaptedMapReduceInput(
        reduce_steps=tuple(reduce_steps),
        adaptation_gaps=tuple(adaptation_gaps),
        omitted_chunks=tuple(omitted_chunks),
    )


def finalize_intake_merge_result(
    merge_result: IntakeMergeResult,
    *,
    adaptation_gaps: Sequence[IntakeGap],
    omitted_chunks: Sequence[Mapping[str, Any]],
) -> IntakeMergeResult:
    """Attach adapter-owned extension metadata to one REDUCE result."""
    if not adaptation_gaps and not omitted_chunks:
        return merge_result

    document = copy.deepcopy(merge_result.document)
    extensions = document.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        raise IntakeMergeError(
            "draft extensions must be an object",
            error_code="draft_schema_invalid",
        )

    combined_gaps = tuple(merge_result.gaps) + tuple(adaptation_gaps)
    if combined_gaps:
        extensions["intake_gaps"] = [_gap_to_dict(gap) for gap in combined_gaps]
    if omitted_chunks:
        extensions["intake_omitted_chunks"] = [
            {
                "artifact_id": str(item["artifact_id"]).lower(),
                "chunk_id": item["chunk_id"],
                "step_key": item["step_key"],
            }
            for item in sorted(
                omitted_chunks,
                key=lambda item: (
                    str(item["artifact_id"]).lower(),
                    str(item["chunk_id"]),
                    str(item["step_key"]),
                ),
            )
        ]

    validate_package_draft_document(document)
    return IntakeMergeResult(
        document=document,
        field_provenance=merge_result.field_provenance,
        conflicts=merge_result.conflicts,
        gaps=combined_gaps,
        metadata_suggestions=merge_result.metadata_suggestions,
        context_complete=merge_result.context_complete,
        system_context_proposal=merge_result.system_context_proposal,
        merged_targets=merge_result.merged_targets,
        rejected_proposals=merge_result.rejected_proposals,
    )


def intake_reduce_audit_metadata(
    merge_result: IntakeMergeResult,
    *,
    merge_digest: str | None = None,
) -> dict[str, Any]:
    """Return bounded audit metadata for one REDUCE commit."""
    payload: dict[str, Any] = {
        "context_complete": merge_result.context_complete,
        "merged_target_count": len(merge_result.merged_targets),
        "conflict_count": len(merge_result.conflicts),
        "gap_count": len(merge_result.gaps),
        "rejected_proposal_count": len(merge_result.rejected_proposals),
    }
    if merge_digest is not None:
        payload["merge_digest"] = merge_digest
    if merge_result.metadata_suggestions:
        payload["metadata_suggestions"] = dict(
            sorted(merge_result.metadata_suggestions.items())
        )
    return payload


def _metadata_from_map_suggestions(
    suggestions: ParsedMapSuggestions,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if suggestions.profile_id:
        metadata["profile_id"] = suggestions.profile_id.strip()
    if suggestions.impact_level in {"low", "moderate", "high"}:
        metadata["impact_level"] = suggestions.impact_level
    if suggestions.certification_class in {"A", "B", "C"}:
        metadata["certification_class"] = suggestions.certification_class
    return _validate_metadata_suggestions(metadata) if metadata else {}


def _proposal_from_map_fact(
    *,
    fact: ParsedMapFact,
    artifact_sha256: str,
    segment_locators: Mapping[int, dict[str, Any]],
    model_step_id: uuid.UUID,
    step_key: str,
) -> tuple[IntakeMapProposal | None, IntakeGap | None]:
    if fact.value_kind == "unknown":
        return None, IntakeGap(
            target_pointer="",
            reason="unsupported_value_kind",
            step_key=step_key,
        )

    target_pointer = target_pointer_for_map_fact_key(fact.fact_key)
    if target_pointer is None:
        return None, IntakeGap(
            target_pointer="",
            reason=f"unmapped_fact_key:{fact.fact_key}",
            step_key=step_key,
        )

    segment_locator = segment_locators.get(fact.segment_index)
    if segment_locator is None:
        return None, IntakeGap(
            target_pointer=target_pointer,
            reason="missing_trusted_segment_locator",
            step_key=step_key,
        )

    evidence_kind = fact.value_kind
    if evidence_kind not in {"direct_evidence", "inference"}:
        return None, IntakeGap(
            target_pointer=target_pointer,
            reason="unsupported_value_kind",
            step_key=step_key,
        )

    chunk_id = fact.chunk_ids[0] if fact.chunk_ids else None
    source_locator = copy.deepcopy(segment_locator)
    if chunk_id is not None:
        source_locator.setdefault("chunk_id", chunk_id)
    source_locator.setdefault("segment_index", fact.segment_index)

    return (
        IntakeMapProposal(
            target_pointer=target_pointer,
            proposed_value=fact.value,
            evidence_kind=evidence_kind,
            source_artifact_id=fact.source_artifact_id,
            source_sha256=artifact_sha256,
            source_locator=source_locator,
            model_step_id=model_step_id,
            confidence=fact.confidence,
            chunk_id=chunk_id,
            segment_index=fact.segment_index,
            step_key=step_key,
            step_id=model_step_id,
        ),
        None,
    )


def _coerce_map_step_result(
    payload: IntakeMapStepResult | Mapping[str, Any],
    *,
    index: int,
) -> IntakeMapStepResult:
    if isinstance(payload, IntakeMapStepResult):
        return payload
    if not isinstance(payload, dict):
        raise IntakeMergeError(
            f"map step {index} must be an object",
            error_code="intake_merge_invalid_input",
        )

    schema_errors = sorted(
        _map_step_validator().iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if schema_errors:
        raise IntakeMergeError(
            schema_errors[0].message,
            error_code="intake_merge_invalid_input",
        )

    if payload.get("schema_version") != MAP_RESULT_SCHEMA_VERSION:
        raise IntakeMergeError(
            "unsupported map step schema_version",
            error_code="intake_merge_invalid_input",
        )

    try:
        step_id = uuid.UUID(str(payload["step_id"]))
    except ValueError as exc:
        raise IntakeMergeError(
            "invalid map step_id",
            error_code="intake_merge_invalid_input",
        ) from exc

    step_key = payload["step_key"]
    proposals_raw = payload.get("proposals")
    if not isinstance(proposals_raw, list):
        raise IntakeMergeError(
            "map step proposals must be an array",
            error_code="intake_merge_invalid_input",
        )

    seen_proposal_ids: set[str] = set()
    parsed_proposals: list[IntakeMapProposal] = []
    for proposal_index, entry in enumerate(proposals_raw):
        proposal = _parse_map_proposal(
            entry,
            step_id=step_id,
            step_key=step_key,
            proposal_index=proposal_index,
        )
        proposal_id = _proposal_identity(proposal)
        if proposal_id in seen_proposal_ids:
            raise IntakeMergeError(
                f"duplicate proposal identity in step {step_key}: {proposal_id}",
                error_code="duplicate_canonical_id",
            )
        seen_proposal_ids.add(proposal_id)
        parsed_proposals.append(proposal)

    metadata = payload.get("metadata_suggestions")
    if metadata is None:
        metadata_dict: dict[str, Any] = {}
    elif isinstance(metadata, dict):
        metadata_dict = _validate_metadata_suggestions(metadata)
    else:
        raise IntakeMergeError(
            "metadata_suggestions must be an object",
            error_code="intake_merge_invalid_input",
        )

    return IntakeMapStepResult(
        step_id=step_id,
        step_key=step_key,
        context_complete=bool(payload["context_complete"]),
        proposals=tuple(parsed_proposals),
        metadata_suggestions=metadata_dict,
    )


def _parse_map_proposal(
    entry: Any,
    *,
    step_id: uuid.UUID,
    step_key: str,
    proposal_index: int,
) -> IntakeMapProposal:
    if not isinstance(entry, dict):
        raise IntakeMergeError(
            f"proposal {proposal_index} must be an object",
            error_code="intake_merge_invalid_input",
        )

    target_pointer = entry.get("target_pointer")
    if not isinstance(target_pointer, str) or not is_valid_json_pointer(target_pointer):
        raise IntakeMergeError(
            f"proposal {proposal_index} has invalid target_pointer",
            error_code="intake_merge_invalid_input",
        )
    _reject_human_only_or_prohibited_target(target_pointer)

    evidence_kind = entry.get("evidence_kind")
    if evidence_kind not in {"direct_evidence", "inference"}:
        raise IntakeMergeError(
            f"proposal {proposal_index} has unsupported evidence_kind",
            error_code="intake_merge_invalid_input",
        )

    source_sha256 = entry.get("source_sha256")
    if not isinstance(source_sha256, str) or _SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise IntakeMergeError(
            f"proposal {proposal_index} has invalid source_sha256",
            error_code="intake_merge_invalid_input",
        )

    source_locator = entry.get("source_locator")
    if not isinstance(source_locator, dict):
        raise IntakeMergeError(
            f"proposal {proposal_index} missing source_locator",
            error_code="intake_merge_invalid_input",
        )

    chunk_id = entry.get("chunk_id")
    segment_index = entry.get("segment_index")
    if not isinstance(chunk_id, str) and not isinstance(segment_index, int):
        raise IntakeMergeError(
            f"proposal {proposal_index} requires chunk_id or segment_index",
            error_code="intake_merge_invalid_input",
        )
    if isinstance(segment_index, bool):
        raise IntakeMergeError(
            f"proposal {proposal_index} has invalid segment_index",
            error_code="intake_merge_invalid_input",
        )

    if evidence_kind == "direct_evidence" and not source_locator:
        raise IntakeMergeError(
            f"proposal {proposal_index} missing direct evidence locator",
            error_code="intake_merge_invalid_input",
        )

    try:
        source_artifact_id = uuid.UUID(str(entry["source_artifact_id"]))
        model_step_id = uuid.UUID(str(entry["model_step_id"]))
    except ValueError as exc:
        raise IntakeMergeError(
            f"proposal {proposal_index} has invalid UUID field",
            error_code="intake_merge_invalid_input",
        ) from exc

    confidence = entry.get("confidence")
    if confidence not in {"low", "medium", "high"}:
        raise IntakeMergeError(
            f"proposal {proposal_index} has invalid confidence",
            error_code="intake_merge_invalid_input",
        )

    if "proposed_value" not in entry:
        raise IntakeMergeError(
            f"proposal {proposal_index} missing proposed_value",
            error_code="intake_merge_invalid_input",
        )

    return IntakeMapProposal(
        target_pointer=target_pointer,
        proposed_value=entry["proposed_value"],
        evidence_kind=evidence_kind,
        source_artifact_id=source_artifact_id,
        source_sha256=source_sha256,
        source_locator=copy.deepcopy(source_locator),
        model_step_id=model_step_id,
        confidence=confidence,
        chunk_id=chunk_id if isinstance(chunk_id, str) else None,
        segment_index=segment_index if isinstance(segment_index, int) else None,
        step_key=step_key,
        step_id=step_id,
    )


def _merge_target_group(
    *,
    profile_id: str | None,
    target_pointer: str,
    entries: Sequence[IntakeMapProposal],
    document: dict[str, Any],
    provenance: dict[str, Any],
    provenance_supplements: dict[str, list[dict[str, Any]]],
) -> IntakeFieldConflict | None:
    if profile_id is not None:
        if not is_target_allowed(profile_id=profile_id, pointer=target_pointer):
            return IntakeFieldConflict(
                conflict_id=_conflict_id(target_pointer, entries),
                target_pointer=target_pointer,
                resolution="rejected",
                candidates=tuple(_candidate_from_proposal(entry) for entry in entries),
            )
        try:
            validate_proposed_value_for_target(
                profile_id=profile_id,
                pointer=target_pointer,
                proposed_value=entries[0].proposed_value,
                document_shell=document,
            )
        except Exception:
            return IntakeFieldConflict(
                conflict_id=_conflict_id(target_pointer, entries),
                target_pointer=target_pointer,
                resolution="rejected",
                candidates=tuple(_candidate_from_proposal(entry) for entry in entries),
            )

    grouped_values: dict[str, list[IntakeMapProposal]] = {}
    for entry in entries:
        normalized = stable_json_dumps(entry.proposed_value)
        grouped_values.setdefault(normalized, []).append(entry)

    if len(grouped_values) > 1:
        candidates = tuple(
            _candidate_from_proposal(entry)
            for normalized in sorted(grouped_values)
            for entry in sorted(
                grouped_values[normalized],
                key=_proposal_sort_key,
            )
        )
        return IntakeFieldConflict(
            conflict_id=_conflict_id(target_pointer, entries),
            target_pointer=target_pointer,
            resolution="unresolved",
            candidates=candidates,
        )

    canonical_key = sorted(grouped_values)[0]
    canonical_entries = grouped_values[canonical_key]
    proposed_value = canonical_entries[0].proposed_value
    try:
        existing_value = value_at_json_pointer(document, target_pointer)
    except (KeyError, IndexError, TypeError):
        existing_value = None

    if existing_value not in (None, "", [], {}) and stable_json_dumps(
        existing_value
    ) != stable_json_dumps(proposed_value):
        existing_candidate = _candidate_from_existing(
            target_pointer=target_pointer,
            value=existing_value,
            provenance=provenance.get(target_pointer),
        )
        map_candidates = tuple(
            _candidate_from_proposal(entry) for entry in canonical_entries
        )
        return IntakeFieldConflict(
            conflict_id=_conflict_id(target_pointer, canonical_entries),
            target_pointer=target_pointer,
            resolution="unresolved",
            candidates=(existing_candidate, *map_candidates),
        )

    provenance_entries = tuple(
        _provenance_from_proposal(entry) for entry in canonical_entries
    )
    primary, supplements = _select_primary_provenance(provenance_entries)
    if existing_value in (None, "", [], {}):
        set_json_pointer(document, target_pointer, proposed_value)
    provenance[target_pointer] = primary
    if supplements:
        provenance_supplements[target_pointer] = supplements
    return None


def _aggregate_metadata_suggestions(
    steps: Sequence[IntakeMapStepResult],
) -> tuple[dict[str, Any], list[IntakeFieldConflict]]:
    collected: dict[str, list[tuple[str, IntakeMapStepResult]]] = {
        "profile_id": [],
        "certification_class": [],
        "impact_level": [],
    }
    for step in steps:
        for field_name, value in step.metadata_suggestions.items():
            if field_name not in collected:
                continue
            collected[field_name].append((stable_json_dumps(value), step))

    suggestions: dict[str, Any] = {}
    conflicts: list[IntakeFieldConflict] = []
    for field_name in sorted(collected):
        entries = collected[field_name]
        if not entries:
            continue
        unique_values = sorted({normalized for normalized, _ in entries})
        if len(unique_values) > 1:
            target_pointer = f"{METADATA_CONFLICT_PREFIX}/{field_name}"
            candidates = tuple(
                {
                    "value": json.loads(normalized),
                    "source_step_key": step.step_key,
                    "source_step_id": str(step.step_id).lower(),
                    "resolution": "unresolved",
                }
                for normalized, step in sorted(entries, key=lambda item: item[1].step_key)
            )
            conflicts.append(
                IntakeFieldConflict(
                    conflict_id=_metadata_conflict_id(field_name, entries),
                    target_pointer=target_pointer,
                    resolution="unresolved",
                    candidates=candidates,
                )
            )
            continue
        suggestions[field_name] = json.loads(unique_values[0])
    return suggestions, conflicts


def _validate_metadata_suggestions(metadata: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, value in metadata.items():
        if key in {"data_origin", "sensitivity"}:
            raise IntakeMergeError(
                "metadata_suggestions must not include data_origin or sensitivity",
                error_code="intake_merge_invalid_input",
            )
        if key == "profile_id":
            if not isinstance(value, str) or not value.strip():
                raise IntakeMergeError(
                    "profile_id suggestion must be a non-empty string",
                    error_code="intake_merge_invalid_input",
                )
            validated[key] = value.strip()
            continue
        if key == "certification_class":
            if value not in {"A", "B", "C"}:
                raise IntakeMergeError(
                    "certification_class suggestion is invalid",
                    error_code="intake_merge_invalid_input",
                )
            validated[key] = value
            continue
        if key == "impact_level":
            if value not in {"low", "moderate", "high"}:
                raise IntakeMergeError(
                    "impact_level suggestion is invalid",
                    error_code="intake_merge_invalid_input",
                )
            validated[key] = value
            continue
        raise IntakeMergeError(
            f"unsupported metadata suggestion field: {key}",
            error_code="intake_merge_invalid_input",
        )
    return validated


def _reject_human_only_or_prohibited_target(pointer: str) -> None:
    lowered = pointer.casefold()
    for marker in _HUMAN_ONLY_TARGET_MARKERS:
        if marker in lowered:
            raise IntakeMergeError(
                f"human-only target is prohibited: {pointer}",
                error_code="intake_merge_invalid_input",
            )
    if is_prohibited_target(pointer):
        raise IntakeMergeError(
            f"prohibited target prefix: {pointer}",
            error_code="intake_merge_invalid_input",
        )
    for prefix in PROHIBITED_TARGET_PREFIXES:
        if pointer == prefix or pointer.startswith(prefix + "/"):
            raise IntakeMergeError(
                f"prohibited target prefix: {pointer}",
                error_code="intake_merge_invalid_input",
            )


def _provenance_from_proposal(proposal: IntakeMapProposal) -> dict[str, Any]:
    locator = copy.deepcopy(proposal.source_locator)
    locator.setdefault("evidence_kind", proposal.evidence_kind)
    if proposal.chunk_id is not None:
        locator.setdefault("chunk_id", proposal.chunk_id)
    if proposal.segment_index is not None:
        locator.setdefault("segment_index", proposal.segment_index)
    return {
        "source_artifact_id": str(proposal.source_artifact_id).lower(),
        "source_sha256": proposal.source_sha256,
        "source_locator": locator,
        "extraction_method": "llm_normalize",
        "model_step_id": str(proposal.model_step_id).lower(),
    }


def _candidate_from_proposal(proposal: IntakeMapProposal) -> dict[str, Any]:
    candidate = {
        "value": proposal.proposed_value,
        "source_artifact_id": str(proposal.source_artifact_id).lower(),
        "source_sha256": proposal.source_sha256,
        "source_locator": copy.deepcopy(proposal.source_locator),
        "model_step_id": str(proposal.model_step_id).lower(),
        "step_key": proposal.step_key,
        "evidence_kind": proposal.evidence_kind,
        "confidence": proposal.confidence,
    }
    if proposal.chunk_id is not None:
        candidate["chunk_id"] = proposal.chunk_id
    if proposal.segment_index is not None:
        candidate["segment_index"] = proposal.segment_index
    return candidate


def _candidate_from_existing(
    *,
    target_pointer: str,
    value: Any,
    provenance: Any,
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "value": value,
        "source_kind": "deterministic_base",
        "target_pointer": target_pointer,
    }
    if isinstance(provenance, dict):
        for key in (
            "source_artifact_id",
            "source_sha256",
            "source_locator",
            "extraction_method",
            "model_step_id",
        ):
            if key in provenance:
                candidate[key] = provenance[key]
    return candidate


def _select_primary_provenance(
    entries: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ordered = sorted(entries, key=_provenance_sort_key)
    primary = copy.deepcopy(ordered[0])
    supplements = [copy.deepcopy(entry) for entry in ordered[1:]]
    return primary, supplements


def _sorted_proposals(steps: Sequence[IntakeMapStepResult]) -> tuple[IntakeMapProposal, ...]:
    proposals = [proposal for step in steps for proposal in step.proposals]
    return tuple(sorted(proposals, key=_proposal_sort_key))


def _proposal_sort_key(proposal: IntakeMapProposal) -> tuple[str, ...]:
    return (
        proposal.target_pointer,
        proposal.step_key,
        str(proposal.source_artifact_id).lower(),
        proposal.chunk_id or "",
        "" if proposal.segment_index is None else f"{proposal.segment_index:09d}",
        str(proposal.model_step_id).lower(),
        stable_json_dumps(proposal.proposed_value),
    )


def _provenance_sort_key(entry: dict[str, Any]) -> tuple[str, ...]:
    locator = entry.get("source_locator")
    chunk_id = ""
    segment_index = ""
    if isinstance(locator, dict):
        if isinstance(locator.get("chunk_id"), str):
            chunk_id = locator["chunk_id"]
        if isinstance(locator.get("segment_index"), int):
            segment_index = f"{locator['segment_index']:09d}"
    model_step_id = entry.get("model_step_id")
    return (
        str(entry.get("source_artifact_id", "")).lower(),
        str(model_step_id or "").lower(),
        chunk_id,
        segment_index,
    )


def _proposal_identity(proposal: IntakeMapProposal) -> str:
    return "|".join(_proposal_sort_key(proposal))


def _conflict_id(target_pointer: str, entries: Sequence[IntakeMapProposal]) -> str:
    material = stable_json_dumps(
        {
            "target_pointer": target_pointer,
            "candidates": [_proposal_identity(entry) for entry in entries],
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _metadata_conflict_id(
    field_name: str,
    entries: Sequence[tuple[str, IntakeMapStepResult]],
) -> str:
    material = stable_json_dumps(
        {
            "field_name": field_name,
            "steps": [step.step_key for _, step in entries],
            "values": sorted({normalized for normalized, _ in entries}),
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _sorted_conflicts(conflicts: Sequence[IntakeFieldConflict]) -> tuple[IntakeFieldConflict, ...]:
    return tuple(sorted(conflicts, key=lambda item: (item.target_pointer, item.conflict_id)))


def _conflict_to_dict(conflict: IntakeFieldConflict) -> dict[str, Any]:
    return {
        "conflict_id": conflict.conflict_id,
        "target_pointer": conflict.target_pointer,
        "resolution": conflict.resolution,
        "candidates": list(conflict.candidates),
    }


def _gap_to_dict(gap: IntakeGap) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target_pointer": gap.target_pointer,
        "reason": gap.reason,
    }
    if gap.step_key is not None:
        payload["step_key"] = gap.step_key
    return payload


def conflicts_by_target(conflicts: Sequence[IntakeFieldConflict]) -> set[str]:
    return {conflict.target_pointer for conflict in conflicts}


@cache
def _map_step_schema_path() -> Path:
    return Path(__file__).with_name("intake_merge_contract.schema.json")


@cache
def _map_step_validator() -> Draft202012Validator:
    schema = json.loads(_map_step_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    validator.check_schema(schema)
    return validator


__all__ = [
    "AdaptedMapReduceInput",
    "DOCUMENT_SCHEMA_VERSION",
    "IntakeFieldConflict",
    "IntakeGap",
    "IntakeMapProposal",
    "IntakeMapStepResult",
    "IntakeMergeError",
    "IntakeMergeResult",
    "MAP_RESULT_SCHEMA_VERSION",
    "adapt_orchestrated_map_steps_for_reduce",
    "finalize_intake_merge_result",
    "intake_reduce_audit_metadata",
    "merge_result_digest",
    "reduce_intake_map_results",
    "target_pointer_for_map_fact_key",
    "validate_intake_map_step_result",
]
