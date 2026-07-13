"""Shared types for bounded normalize_proposal processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
import uuid

ValidationOutcome = Literal[
    "accepted",
    "rejected_routing",
    "rejected_context_limit",
    "rejected_parse",
    "rejected_policy",
    "rejected_source_binding",
    "rejected_value",
    "rejected_merge",
    "skipped_no_targets",
    "skipped_no_segments",
    "repair_succeeded",
    "repair_exhausted",
    "model_not_configured",
    "model_call_failed",
]

FailureKind = Literal[
    "none",
    "routing",
    "context_limit",
    "parse",
    "schema",
    "allowlist",
    "prohibited_prefix",
    "duplicate_target",
    "source_binding",
    "value_support",
    "merge_conflict",
    "model_not_configured",
    "model_call_failed",
]


@dataclass(frozen=True, slots=True)
class SegmentFact:
    """One extracted segment included in a fact bundle."""

    segment_index: int
    text: str
    locator: dict[str, Any]
    extraction_method: str
    text_truncated: bool = False


@dataclass(frozen=True, slots=True)
class ArtifactFacts:
    """Deterministic artifact metadata and extracted segments."""

    artifact_id: uuid.UUID
    sha256: str
    filename: str | None
    detected_format: str
    segments: tuple[SegmentFact, ...]


@dataclass(frozen=True, slots=True)
class FactBundle:
    """Immutable fact bundle sent to the model."""

    profile_id: str
    empty_targets: tuple[str, ...]
    artifacts: tuple[ArtifactFacts, ...]
    omitted_segment_ids: tuple[str, ...]
    fact_bundle_sha256: str
    context_complete: bool
    prompt_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParsedProposal:
    """One validated model proposal before merge."""

    target: str
    proposed_value: Any
    source_artifact_id: uuid.UUID
    segment_index: int
    source_sha256: str
    source_locator: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    """Validated normalize_proposal model response."""

    proposals: tuple[ParsedProposal, ...]


@dataclass(frozen=True, slots=True)
class ModelCallMetadata:
    """Metadata for one model invocation attempt."""

    attempt: int
    raw_response: str
    response_sha256: str
    failure_kind: FailureKind = "none"
    failure_detail: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizeProposalResult:
    """Pure normalize_proposal outcome for a persistence layer."""

    document: dict[str, Any]
    field_provenance: dict[str, Any]
    validation_outcome: ValidationOutcome
    llm_call_count: int
    merged_targets: tuple[str, ...]
    rejected_proposals: tuple[str, ...]
    omitted_segment_ids: tuple[str, ...]
    context_complete: bool
    fact_bundle_sha256: str | None
    prompt_version: str | None
    prompt_sha256: str | None
    response_sha256: str | None
    model_calls: tuple[ModelCallMetadata, ...] = field(default_factory=tuple)
    error_code: str | None = None
