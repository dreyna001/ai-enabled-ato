"""Deterministic intake readiness report assembly for Phase 3D."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.domain_mapping import (
    DOMAIN_SCHEMA_VERSION,
    format_uuid,
    format_utc_datetime,
    map_source_artifact_to_domain,
)
from ato_service.lifecycle_transitions import PackageRevisionStatus
from ato_service.package_rbac import require_package_role
from ato_service.package_revisions import (
    PackageRevisionNotFoundError,
    revision_metadata_is_complete,
)
from ato_service.route_role_matrix import ROLE_VIEWER

IntakeStage = Literal[
    "no_artifacts",
    "upload_open",
    "malware_scan",
    "extract",
    "intake_map",
    "intake_reduce",
    "awaiting_human_review",
    "confirmed",
    "blocked",
    "archived",
]

AttestationPresence = Literal["present", "missing"]

_INTAKE_MAP_STEP_KEY_PREFIX = "imap_"
_ACTIVE_NORMALIZATION_STATUSES = frozenset({"reserved", "running"})
_BLOCKED_NORMALIZATION_STATUSES = frozenset(
    {"failed", "policy_blocked", "reconciliation_required"}
)
_INCOMPLETE_MAP_OUTCOMES = frozenset(
    {
        "model_call_failed",
        "model_not_configured",
        "repair_exhausted",
    }
)
_INTAKE_EXTENSION_KEYS = frozenset(
    {
        "intake_conflicts",
        "intake_gaps",
        "intake_omitted_chunks",
        "intake_context_complete",
    }
)
_HUMAN_ONLY_FIELDS = frozenset({"data_origin", "sensitivity"})
_SUGGESTION_FIELDS = (
    "profile_id",
    "certification_class",
    "impact_level",
)
_SUGGESTION_VALUES: dict[str, frozenset[str]] = {
    "profile_id": frozenset(
        {
            "fedramp_20x_program",
            "fedramp_rev5_transition",
            "fisma_agency_security",
        }
    ),
    "certification_class": frozenset({"B", "C"}),
    "impact_level": frozenset({"low", "moderate", "high"}),
}
_CANDIDATE_ALLOWED_FIELDS = (
    "candidate_id",
    "value",
    "proposed_value",
    "source_artifact_id",
    "source_sha256",
    "chunk_id",
    "step_key",
    "model_step_id",
)
_PROHIBITED_NESTED_KEYS = frozenset(
    {
        "credential",
        "password",
        "prompt",
        "prompt_payload",
        "raw_model_response",
        "raw_response",
        "secret",
        "storage_key",
        "token",
    }
)
_STEP_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_JSON_POINTER_PATTERN = re.compile(r"^(/([^~/]|~[01])*)*$")
_MAX_CONFLICTS = 200
_MAX_CANDIDATES = 20
_MAX_GAPS = 100
_MAX_OMITTED_CHUNKS = 5000
_MAX_POINTER_LENGTH = 1000
_MAX_REASON_LENGTH = 1000
_MAX_JSON_VALUE_BYTES = 8000


class IntakeReportStateError(Exception):
    """Raised when persisted intake artifacts disagree with revision state."""

    error_code = "state_artifact_inconsistent"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IntakeFieldConflict:
    """One merge conflict surfaced for human resolution."""

    field: str
    values: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class OmittedChunkRef:
    """Reference to source material omitted from bounded model context."""

    artifact_id: uuid.UUID
    segment_id: str


@dataclass(frozen=True, slots=True)
class IntakeGap:
    """Deterministic readiness gap."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class IntakeMergeSnapshot:
    """Adapter seam for concurrent MAP/REDUCE merge outputs."""

    conflicts: tuple[IntakeFieldConflict, ...] = ()
    omitted_chunk_refs: tuple[OmittedChunkRef, ...] = ()
    context_complete: bool | None = None
    extra_gaps: tuple[IntakeGap, ...] = ()


class IntakeMergeAdapter(Protocol):
    """Optional adapter that supplies MAP/REDUCE merge metadata."""

    def load_merge_snapshot(
        self,
        *,
        package_revision_id: uuid.UUID,
        draft_document: dict[str, Any] | None,
        field_provenance: dict[str, Any] | None,
    ) -> IntakeMergeSnapshot:
        """Return merge-derived readiness metadata for one revision."""


class DefaultIntakeMergeAdapter:
    """Read validated MAP/REDUCE results from package-draft extensions."""

    def load_merge_snapshot(
        self,
        *,
        package_revision_id: uuid.UUID,
        draft_document: dict[str, Any] | None,
        field_provenance: dict[str, Any] | None,
    ) -> IntakeMergeSnapshot:
        del package_revision_id, field_provenance
        if draft_document is None or "extensions" not in draft_document:
            return IntakeMergeSnapshot()
        extensions = draft_document["extensions"]
        if not isinstance(extensions, dict):
            raise IntakeReportStateError(
                "package draft extensions must be an object"
            )
        if not (_INTAKE_EXTENSION_KEYS & extensions.keys()):
            return IntakeMergeSnapshot()
        for key in _INTAKE_EXTENSION_KEYS & extensions.keys():
            _reject_human_only_fields(extensions[key])
        return IntakeMergeSnapshot(
            conflicts=_parse_intake_conflicts(extensions),
            extra_gaps=_parse_intake_gaps(extensions),
            omitted_chunk_refs=_parse_intake_omitted_chunks(extensions),
            context_complete=_parse_intake_context_complete(extensions),
        )


@dataclass(frozen=True, slots=True)
class IntakeReportContext:
    """Loaded revision state used to build one intake report."""

    package_revision: Any
    system: Any
    source_artifacts: tuple[Any, ...]
    intake_work: tuple[Any, ...]
    normalization_steps: tuple[Any, ...]
    draft_document: dict[str, Any] | None
    field_provenance: dict[str, Any] | None
    pending_fact_proposals: bool


def _revision_not_found(*, package_revision_id: uuid.UUID) -> Exception:
    return PackageRevisionNotFoundError(package_revision_id=package_revision_id)


def _state_error(message: str) -> IntakeReportStateError:
    return IntakeReportStateError(message)


def _reject_human_only_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _HUMAN_ONLY_FIELDS:
                raise _state_error(
                    "intake extensions contain a human-only metadata field"
                )
            _reject_human_only_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_human_only_fields(child)


def _require_list(
    extensions: dict[str, Any],
    key: str,
    *,
    max_items: int,
) -> list[Any]:
    if key not in extensions:
        return []
    value = extensions[key]
    if not isinstance(value, list) or len(value) > max_items:
        raise _state_error(f"{key} must be a bounded array")
    return value


def _require_bounded_string(
    value: Any,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_length
        or any(ord(character) < 32 for character in value)
    ):
        raise _state_error(f"{field_name} must be a bounded string")
    return value


def _require_json_pointer(value: Any, *, field_name: str) -> str:
    pointer = _require_bounded_string(
        value,
        field_name=field_name,
        max_length=_MAX_POINTER_LENGTH,
    )
    if not _JSON_POINTER_PATTERN.fullmatch(pointer):
        raise _state_error(f"{field_name} must be a JSON pointer")
    if any(
        segment.replace("~1", "/").replace("~0", "~") in _HUMAN_ONLY_FIELDS
        for segment in pointer.lstrip("/").split("/")
    ):
        raise _state_error(f"{field_name} targets a human-only metadata field")
    return pointer


def _require_step_key(value: Any, *, field_name: str) -> str:
    step_key = _require_bounded_string(
        value,
        field_name=field_name,
        max_length=64,
    )
    if not _STEP_KEY_PATTERN.fullmatch(step_key):
        raise _state_error(f"{field_name} must be a valid step key")
    return step_key


def _require_uuid4(value: Any, *, field_name: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise _state_error(f"{field_name} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise _state_error(f"{field_name} must be a UUID") from exc
    if parsed.version != 4 or str(parsed).lower() != value.lower():
        raise _state_error(f"{field_name} must be a canonical UUIDv4")
    return parsed


def _canonical_json(value: Any, *, field_name: str) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise _state_error(f"{field_name} must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > _MAX_JSON_VALUE_BYTES:
        raise _state_error(f"{field_name} exceeds the bounded JSON size")
    return encoded


def _reject_prohibited_nested_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _PROHIBITED_NESTED_KEYS:
                raise _state_error(
                    "intake conflict candidate contains prohibited internal data"
                )
            _reject_prohibited_nested_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_prohibited_nested_keys(child)


def _sanitize_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _state_error("intake conflict candidates must be objects")
    _reject_human_only_fields(value)
    candidate = {
        key: value[key]
        for key in _CANDIDATE_ALLOWED_FIELDS
        if key in value
    }
    if not candidate or not ({"value", "proposed_value"} & candidate.keys()):
        raise _state_error(
            "intake conflict candidate must include a proposed value"
        )
    _reject_prohibited_nested_keys(candidate)
    if "source_artifact_id" in candidate:
        candidate["source_artifact_id"] = str(
            _require_uuid4(
                candidate["source_artifact_id"],
                field_name="candidate.source_artifact_id",
            )
        )
    if "model_step_id" in candidate:
        candidate["model_step_id"] = str(
            _require_uuid4(
                candidate["model_step_id"],
                field_name="candidate.model_step_id",
            )
        )
    if "source_sha256" in candidate and (
        not isinstance(candidate["source_sha256"], str)
        or not _SHA256_PATTERN.fullmatch(candidate["source_sha256"])
    ):
        raise _state_error("candidate.source_sha256 must be a SHA-256 digest")
    if "chunk_id" in candidate:
        candidate["chunk_id"] = _require_bounded_string(
            candidate["chunk_id"],
            field_name="candidate.chunk_id",
            max_length=255,
        )
    if "step_key" in candidate:
        candidate["step_key"] = _require_step_key(
            candidate["step_key"],
            field_name="candidate.step_key",
        )
    if "candidate_id" in candidate:
        candidate["candidate_id"] = _require_bounded_string(
            candidate["candidate_id"],
            field_name="candidate.candidate_id",
            max_length=128,
        )
    _canonical_json(candidate, field_name="intake conflict candidate")
    return candidate


def _parse_intake_conflicts(
    extensions: dict[str, Any],
) -> tuple[IntakeFieldConflict, ...]:
    conflicts: dict[str, IntakeFieldConflict] = {}
    for item in _require_list(
        extensions,
        "intake_conflicts",
        max_items=_MAX_CONFLICTS,
    ):
        if not isinstance(item, dict):
            raise _state_error("intake_conflicts entries must be objects")
        _require_bounded_string(
            item.get("conflict_id"),
            field_name="intake conflict_id",
            max_length=128,
        )
        field = _require_json_pointer(
            item.get("target_pointer"),
            field_name="intake conflict target_pointer",
        )
        _require_bounded_string(
            item.get("resolution"),
            field_name="intake conflict resolution",
            max_length=64,
        )
        raw_candidates = item.get("candidates")
        if (
            not isinstance(raw_candidates, list)
            or len(raw_candidates) < 2
            or len(raw_candidates) > _MAX_CANDIDATES
        ):
            raise _state_error(
                "intake conflict candidates must contain 2 to 20 entries"
            )
        deduped_candidates = {
            _canonical_json(
                candidate,
                field_name="intake conflict candidate",
            ): candidate
            for candidate in (
                _sanitize_candidate(raw_candidate)
                for raw_candidate in raw_candidates
            )
        }
        if len(deduped_candidates) < 2:
            raise _state_error(
                "intake conflict must contain two distinct candidates"
            )
        candidates = tuple(
            deduped_candidates[key] for key in sorted(deduped_candidates)
        )
        conflict = IntakeFieldConflict(field=field, values=candidates)
        conflict_key = _canonical_json(
            {"field": field, "values": candidates},
            field_name="intake conflict",
        )
        conflicts.setdefault(conflict_key, conflict)
    return tuple(conflicts[key] for key in sorted(conflicts))


def _parse_intake_gaps(extensions: dict[str, Any]) -> tuple[IntakeGap, ...]:
    gaps: dict[str, IntakeGap] = {}
    for item in _require_list(
        extensions,
        "intake_gaps",
        max_items=_MAX_GAPS,
    ):
        if not isinstance(item, dict):
            raise _state_error("intake_gaps entries must be objects")
        target = _require_json_pointer(
            item.get("target_pointer"),
            field_name="intake gap target_pointer",
        )
        reason = _require_bounded_string(
            item.get("reason"),
            field_name="intake gap reason",
            max_length=_MAX_REASON_LENGTH,
        )
        step_key = None
        if "step_key" in item:
            step_key = _require_step_key(
                item["step_key"],
                field_name="intake gap step_key",
            )
        identity = _canonical_json(
            {
                "reason": reason,
                "step_key": step_key,
                "target_pointer": target,
            },
            field_name="intake gap",
        )
        code = f"intake_gap_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        message = f"Intake gap at {target}"
        if step_key is not None:
            message += f" from MAP step {step_key}"
        gap = IntakeGap(code=code, message=f"{message}.")
        gaps.setdefault(identity, gap)
    return tuple(gaps[key] for key in sorted(gaps))


def _parse_intake_omitted_chunks(
    extensions: dict[str, Any],
) -> tuple[OmittedChunkRef, ...]:
    omitted: dict[tuple[str, str], OmittedChunkRef] = {}
    for item in _require_list(
        extensions,
        "intake_omitted_chunks",
        max_items=_MAX_OMITTED_CHUNKS,
    ):
        if not isinstance(item, dict):
            raise _state_error(
                "intake_omitted_chunks entries must be objects"
            )
        artifact_id = _require_uuid4(
            item.get("artifact_id"),
            field_name="omitted chunk artifact_id",
        )
        chunk_id = _require_bounded_string(
            item.get("chunk_id"),
            field_name="omitted chunk chunk_id",
            max_length=255,
        )
        _require_step_key(
            item.get("step_key"),
            field_name="omitted chunk step_key",
        )
        key = (str(artifact_id), chunk_id)
        omitted.setdefault(
            key,
            OmittedChunkRef(artifact_id=artifact_id, segment_id=chunk_id),
        )
    return tuple(omitted[key] for key in sorted(omitted))


def _parse_intake_context_complete(
    extensions: dict[str, Any],
) -> bool | None:
    if "intake_context_complete" not in extensions:
        return None
    value = extensions["intake_context_complete"]
    if not isinstance(value, bool):
        raise _state_error("intake_context_complete must be a boolean")
    return value


def _validate_metadata_suggestions(value: Any) -> dict[str, Any]:
    suggestions: dict[str, Any] = dict.fromkeys(_SUGGESTION_FIELDS)
    if value is None:
        return suggestions
    if not isinstance(value, dict):
        raise _state_error("intake_metadata_suggestions must be an object")
    unknown_fields = set(value) - set(_SUGGESTION_FIELDS)
    if unknown_fields:
        raise _state_error(
            "intake_metadata_suggestions contains unsupported fields"
        )
    for field_name, proposed_value in value.items():
        if (
            proposed_value is not None
            and (
                not isinstance(proposed_value, str)
                or proposed_value not in _SUGGESTION_VALUES[field_name]
            )
        ):
            raise _state_error(
                f"intake_metadata_suggestions.{field_name} is unsupported"
            )
        suggestions[field_name] = proposed_value
    return suggestions


def _empty_suggested_metadata() -> dict[str, Any]:
    return _validate_metadata_suggestions(None)


def _human_attestation(revision: Any) -> dict[str, AttestationPresence]:
    return {
        "data_origin": "present" if revision.data_origin is not None else "missing",
        "sensitivity": "present" if revision.sensitivity is not None else "missing",
    }


def _intake_work_by_phase(intake_work: tuple[Any, ...]) -> dict[str, Any]:
    return {row.work_phase: row for row in intake_work}


def _is_intake_map_step(step: Any) -> bool:
    return isinstance(step.step_key, str) and step.step_key.startswith(
        _INTAKE_MAP_STEP_KEY_PREFIX
    )


def _intake_map_steps(normalization_steps: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(step for step in normalization_steps if _is_intake_map_step(step))


def _has_reconciliation(*, intake_work: tuple[Any, ...], normalization_steps: tuple[Any, ...]) -> bool:
    if any(row.status == "reconciliation_required" for row in intake_work):
        return True
    return any(
        step.status == "reconciliation_required"
        for step in _intake_map_steps(normalization_steps)
    )


def _active_normalization(normalization_steps: tuple[Any, ...]) -> bool:
    return any(
        step.status in _ACTIVE_NORMALIZATION_STATUSES
        for step in _intake_map_steps(normalization_steps)
    )


def _derive_intake_stage(
    *,
    revision: Any,
    source_artifacts: tuple[Any, ...],
    intake_work: tuple[Any, ...],
    normalization_steps: tuple[Any, ...],
    draft_document: dict[str, Any] | None,
    merge_snapshot: IntakeMergeSnapshot,
) -> IntakeStage:
    status = PackageRevisionStatus(revision.status)
    if status is PackageRevisionStatus.ARCHIVED:
        return "archived"
    if status in {PackageRevisionStatus.INVALID, PackageRevisionStatus.QUARANTINED}:
        return "blocked"
    if status is PackageRevisionStatus.READY:
        return "confirmed"
    if _has_reconciliation(intake_work=intake_work, normalization_steps=normalization_steps):
        return "blocked"
    if any(artifact.malware_scan_status == "infected" for artifact in source_artifacts):
        return "blocked"

    if status is PackageRevisionStatus.UPLOADING:
        if not source_artifacts:
            return "no_artifacts"
        return "upload_open"

    if status is PackageRevisionStatus.SCANNING:
        return "malware_scan"

    if status is PackageRevisionStatus.EXTRACTING:
        return "extract"

    if status is PackageRevisionStatus.AWAITING_CONFIRMATION:
        if _active_normalization(normalization_steps):
            return "intake_map"
        if merge_snapshot.conflicts or merge_snapshot.extra_gaps:
            return "intake_reduce"
        if draft_document is None:
            return "intake_reduce"
        return "awaiting_human_review"

    return "blocked"


def _build_inventory_files(source_artifacts: tuple[Any, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for artifact in source_artifacts:
        mapped = map_source_artifact_to_domain(artifact)
        items.append(
            {
                "artifact_id": mapped["artifact_id"],
                "display_filename": mapped["display_filename"],
                "sha256": mapped["sha256"],
                "size_bytes": mapped["size_bytes"],
                "artifact_kind": mapped["artifact_kind"],
                "malware_scan_status": mapped["malware_scan_status"],
                "extraction_status": mapped["extraction_status"],
                "uploaded_at": mapped["uploaded_at"],
            }
        )
    items.sort(key=lambda item: item["artifact_id"])
    return items


def _build_map_step_summaries(normalization_steps: tuple[Any, ...]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for step in _intake_map_steps(normalization_steps):
        summaries.append(
            {
                "step_id": format_uuid(step.step_id),
                "step_key": step.step_key,
                "status": step.status,
                "validation_outcome": step.validation_outcome,
                "fact_bundle_sha256": step.fact_bundle_sha256,
                "response_sha256": step.response_sha256,
                "prompt_sha256": step.prompt_sha256,
                "llm_call_count": step.llm_call_count,
                "error_code": step.error_code,
            }
        )
    summaries.sort(key=lambda item: (item["step_key"], item["step_id"]))
    return summaries


def _serialize_conflicts(conflicts: tuple[IntakeFieldConflict, ...]) -> list[dict[str, Any]]:
    payload = [
        {
            "field": conflict.field,
            "values": list(conflict.values),
        }
        for conflict in conflicts
    ]
    payload.sort(key=lambda item: item["field"])
    return payload


def _serialize_omitted_chunks(
    omitted_chunk_refs: tuple[OmittedChunkRef, ...],
) -> list[dict[str, Any]]:
    payload = [
        {
            "artifact_id": format_uuid(ref.artifact_id),
            "segment_id": ref.segment_id,
        }
        for ref in omitted_chunk_refs
    ]
    payload.sort(key=lambda item: (item["artifact_id"], item["segment_id"]))
    return payload


def _resolve_context_complete(
    *,
    merge_snapshot: IntakeMergeSnapshot,
    normalization_steps: tuple[Any, ...],
) -> bool:
    if merge_snapshot.context_complete is not True:
        return False
    if merge_snapshot.omitted_chunk_refs:
        return False
    for step in _intake_map_steps(normalization_steps):
        outcome = step.validation_outcome
        if step.status != "completed":
            return False
        if (
            outcome is None
            or outcome in _INCOMPLETE_MAP_OUTCOMES
            or outcome.startswith("rejected_")
            or outcome.startswith("skipped_")
        ):
            return False
    return True


def _collect_gaps(
    *,
    revision: Any,
    source_artifacts: tuple[Any, ...],
    intake_work: tuple[Any, ...],
    normalization_steps: tuple[Any, ...],
    draft_document: dict[str, Any] | None,
    pending_fact_proposals: bool,
    merge_snapshot: IntakeMergeSnapshot,
    intake_stage: IntakeStage,
    context_complete: bool,
    confirmation_blockers: tuple[str, ...],
) -> list[dict[str, str]]:
    gaps: list[IntakeGap] = list(merge_snapshot.extra_gaps)
    status = PackageRevisionStatus(revision.status)

    if intake_stage == "no_artifacts":
        gaps.append(IntakeGap(code="no_source_artifacts", message="Upload at least one source artifact."))
    if status is PackageRevisionStatus.UPLOADING and source_artifacts and revision.content_manifest_sha256 is None:
        gaps.append(
            IntakeGap(
                code="upload_not_finalized",
                message="Finalize upload before intake processing can complete.",
            )
        )

    work_by_phase = _intake_work_by_phase(intake_work)
    for phase, row in sorted(work_by_phase.items()):
        if row.status in {"available", "leased", "failed"}:
            gaps.append(
                IntakeGap(
                    code="intake_work_pending",
                    message=f"Intake work phase {phase} is {row.status}.",
                )
            )
        if row.status == "reconciliation_required":
            gaps.append(
                IntakeGap(
                    code="intake_reconciliation_required",
                    message=f"Intake work phase {phase} requires operator reconciliation.",
                )
            )

    for step in _intake_map_steps(normalization_steps):
        if step.status in _ACTIVE_NORMALIZATION_STATUSES:
            gaps.append(
                IntakeGap(
                    code="normalization_in_progress",
                    message=f"Normalization step {step.step_key} is {step.status}.",
                )
            )
        elif step.status in _BLOCKED_NORMALIZATION_STATUSES:
            gaps.append(
                IntakeGap(
                    code="normalization_blocked",
                    message=f"Normalization step {step.step_key} is {step.status}.",
                )
            )

    if status is PackageRevisionStatus.AWAITING_CONFIRMATION and draft_document is None:
        gaps.append(
            IntakeGap(
                code="draft_not_ready",
                message="Package editor draft is not yet available for review.",
            )
        )

    if not context_complete:
        gaps.append(
            IntakeGap(
                code="context_incomplete",
                message=(
                    "Bounded intake context was incomplete; downstream supported "
                    "findings must remain constrained."
                ),
            )
        )

    if merge_snapshot.conflicts:
        gaps.append(
            IntakeGap(
                code="merge_conflicts_present",
                message="Conflicting extracted values require human resolution.",
            )
        )

    attestation = _human_attestation(revision)
    if status is PackageRevisionStatus.AWAITING_CONFIRMATION:
        if attestation["data_origin"] == "missing":
            gaps.append(
                IntakeGap(
                    code="human_data_origin_missing",
                    message="Human attestation for data_origin is required before confirm.",
                )
            )
        if attestation["sensitivity"] == "missing":
            gaps.append(
                IntakeGap(
                    code="human_sensitivity_missing",
                    message="Human attestation for sensitivity is required before confirm.",
                )
            )

    if pending_fact_proposals:
        gaps.append(
            IntakeGap(
                code="pending_legacy_fact_proposals",
                message="Legacy fact proposals remain pending review.",
            )
        )

    for blocker in confirmation_blockers:
        gaps.append(IntakeGap(code=blocker, message=f"Confirm blocked: {blocker}."))

    deduped: dict[str, IntakeGap] = {}
    for gap in gaps:
        deduped.setdefault(gap.code, gap)
    ordered = sorted(deduped.values(), key=lambda gap: gap.code)
    return [{"code": gap.code, "message": gap.message} for gap in ordered]


def _evaluate_confirmation(
    *,
    revision: Any,
    pending_fact_proposals: bool,
    merge_snapshot: IntakeMergeSnapshot,
    project_root: Any | None,
    draft_document: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    blockers: list[str] = []
    status = PackageRevisionStatus(revision.status)
    if status is not PackageRevisionStatus.AWAITING_CONFIRMATION:
        blockers.append("revision_not_awaiting_confirmation")
    if merge_snapshot.conflicts:
        blockers.append("merge_conflicts_present")
    if pending_fact_proposals and draft_document is None:
        blockers.append("pending_legacy_fact_proposals")
    if not revision_metadata_is_complete(revision):
        blockers.append("revision_metadata_incomplete")

    attestation = _human_attestation(revision)
    if attestation["data_origin"] == "missing":
        blockers.append("human_data_origin_missing")
    if attestation["sensitivity"] == "missing":
        blockers.append("human_sensitivity_missing")

    if (
        draft_document is not None
        and project_root is not None
        and status is PackageRevisionStatus.AWAITING_CONFIRMATION
    ):
        from ato_service.export_readiness import (
            evaluate_export_readiness,
            portal_export_blocker_codes,
        )
        from ato_service.export_service import _optional_runtime_config_document

        readiness = evaluate_export_readiness(
            profile_id=revision.profile_id,
            sealed_document=draft_document,
            project_root=project_root,
            runtime_config_document=_optional_runtime_config_document(project_root),
        )
        blockers.extend(portal_export_blocker_codes(readiness.blockers))

    unique_blockers = tuple(sorted(set(blockers)))
    return not unique_blockers, unique_blockers


def _assert_state_consistency(
    *,
    revision: Any,
    draft_document: dict[str, Any] | None,
) -> None:
    status = PackageRevisionStatus(revision.status)
    if draft_document is not None and status in {
        PackageRevisionStatus.UPLOADING,
        PackageRevisionStatus.SCANNING,
    }:
        raise IntakeReportStateError(
            "package editor draft exists before intake reached awaiting_confirmation"
        )


def build_intake_report(
    context: IntakeReportContext,
    *,
    merge_snapshot: IntakeMergeSnapshot,
    generated_at: datetime,
    project_root: Any | None = None,
) -> dict[str, Any]:
    """Build the stable intake report payload from loaded context."""
    _assert_state_consistency(
        revision=context.package_revision,
        draft_document=context.draft_document,
    )

    intake_stage = _derive_intake_stage(
        revision=context.package_revision,
        source_artifacts=context.source_artifacts,
        intake_work=context.intake_work,
        normalization_steps=context.normalization_steps,
        draft_document=context.draft_document,
        merge_snapshot=merge_snapshot,
    )
    context_complete = _resolve_context_complete(
        merge_snapshot=merge_snapshot,
        normalization_steps=context.normalization_steps,
    )
    confirmation_allowed, confirmation_blockers = _evaluate_confirmation(
        revision=context.package_revision,
        pending_fact_proposals=context.pending_fact_proposals,
        merge_snapshot=merge_snapshot,
        project_root=project_root,
        draft_document=context.draft_document,
    )
    suggested_metadata = _empty_suggested_metadata()
    gaps = _collect_gaps(
        revision=context.package_revision,
        source_artifacts=context.source_artifacts,
        intake_work=context.intake_work,
        normalization_steps=context.normalization_steps,
        draft_document=context.draft_document,
        pending_fact_proposals=context.pending_fact_proposals,
        merge_snapshot=merge_snapshot,
        intake_stage=intake_stage,
        context_complete=context_complete,
        confirmation_blockers=confirmation_blockers,
    )

    return {
        "schema_version": DOMAIN_SCHEMA_VERSION,
        "object_type": "intake_report",
        "package_revision_id": format_uuid(context.package_revision.package_revision_id),
        "revision_version": context.package_revision.revision_version,
        "status": context.package_revision.status,
        "intake_stage": intake_stage,
        "files": _build_inventory_files(context.source_artifacts),
        "human_attestation": _human_attestation(context.package_revision),
        "suggested_metadata": suggested_metadata,
        "suggestion_sources": [],
        "gaps": gaps,
        "conflicts": _serialize_conflicts(merge_snapshot.conflicts),
        "omitted_chunks": _serialize_omitted_chunks(merge_snapshot.omitted_chunk_refs),
        "context_complete": context_complete,
        "map_steps": _build_map_step_summaries(context.normalization_steps),
        "confirmation": {
            "allowed": confirmation_allowed,
            "blockers": list(confirmation_blockers),
        },
        "generated_at": format_utc_datetime(generated_at),
    }


async def load_intake_report_context(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
) -> IntakeReportContext:
    """Load revision-scoped rows required to build an intake report."""
    from ato_service.db.models import (
        FactProposal,
        PackageNormalizationStep,
        PackageRevision,
        PackageRevisionDraft,
        PackageRevisionIntakeWork,
        SourceArtifact,
        System,
    )

    revision_result = await session.execute(
        select(PackageRevision, System)
        .join(System, System.system_id == PackageRevision.system_id)
        .where(PackageRevision.package_revision_id == package_revision_id)
    )
    row = revision_result.one_or_none()
    if row is None:
        raise _revision_not_found(package_revision_id=package_revision_id)
    package_revision, system = row

    artifacts = (
        await session.execute(
            select(SourceArtifact)
            .where(SourceArtifact.package_revision_id == package_revision_id)
            .order_by(SourceArtifact.artifact_id.asc())
        )
    ).scalars().all()
    intake_work = (
        await session.execute(
            select(PackageRevisionIntakeWork)
            .where(PackageRevisionIntakeWork.package_revision_id == package_revision_id)
            .order_by(PackageRevisionIntakeWork.work_phase.asc())
        )
    ).scalars().all()
    normalization_steps = (
        await session.execute(
            select(PackageNormalizationStep)
            .where(PackageNormalizationStep.package_revision_id == package_revision_id)
            .order_by(
                PackageNormalizationStep.step_key.asc(),
                PackageNormalizationStep.created_at.asc(),
            )
        )
    ).scalars().all()
    draft = (
        await session.execute(
            select(PackageRevisionDraft).where(
                PackageRevisionDraft.package_revision_id == package_revision_id
            )
        )
    ).scalar_one_or_none()
    pending_fact_proposals = bool(
        (
            await session.execute(
                select(
                    exists().where(
                        FactProposal.package_revision_id == package_revision_id,
                        FactProposal.review_status == "pending",
                    )
                )
            )
        ).scalar_one()
    )

    return IntakeReportContext(
        package_revision=package_revision,
        system=system,
        source_artifacts=tuple(artifacts),
        intake_work=tuple(intake_work),
        normalization_steps=tuple(normalization_steps),
        draft_document=None if draft is None else dict(draft.document),
        field_provenance=None if draft is None else dict(draft.field_provenance),
        pending_fact_proposals=pending_fact_proposals,
    )


async def get_intake_report(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    project_root: Any | None = None,
    merge_adapter: IntakeMergeAdapter | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load and authorize one intake readiness report."""
    context = await load_intake_report_context(
        session,
        package_revision_id=package_revision_id,
    )
    require_package_role(
        principal,
        system=context.system,
        revision=context.package_revision,
        role=ROLE_VIEWER,
    )
    adapter = merge_adapter or DefaultIntakeMergeAdapter()
    merge_snapshot = adapter.load_merge_snapshot(
        package_revision_id=package_revision_id,
        draft_document=context.draft_document,
        field_provenance=context.field_provenance,
    )
    generated_at = now if now is not None else datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return build_intake_report(
        context,
        merge_snapshot=merge_snapshot,
        generated_at=generated_at.astimezone(timezone.utc),
        project_root=project_root,
    )


__all__ = [
    "DefaultIntakeMergeAdapter",
    "IntakeFieldConflict",
    "IntakeGap",
    "IntakeMergeAdapter",
    "IntakeMergeSnapshot",
    "IntakeReportContext",
    "IntakeReportStateError",
    "OmittedChunkRef",
    "build_intake_report",
    "get_intake_report",
    "load_intake_report_context",
]
