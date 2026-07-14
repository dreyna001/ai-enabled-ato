"""Shared types for bounded sufficiency_matrix processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ValidationOutcome = Literal[
    "accepted",
    "rejected_routing",
    "rejected_context_limit",
    "rejected_parse",
    "rejected_policy",
    "rejected_citation",
    "rejected_coverage",
    "rejected_status_ceiling",
    "repair_succeeded",
    "repair_exhausted",
    "model_not_configured",
    "model_call_failed",
    "model_timeout",
]


@dataclass(frozen=True, slots=True)
class ResponseValidationError(Exception):
    failure_kind: str
    detail: str
    repairable: bool

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    source_id: str
    source_sha256: str
    text: str
    control_id: str | None = None


@dataclass(frozen=True, slots=True)
class FactBundle:
    profile_id: str
    assessment_item_ids: tuple[str, ...]
    assessment_items: tuple[dict[str, Any], ...]
    evidence_sources: tuple[EvidenceSource, ...]
    omitted_source_ids: tuple[str, ...]
    fact_bundle_sha256: str
    context_complete: bool
    prompt_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParsedMatrixRow:
    assessment_item_id: str
    model_proposed_status: str
    finding_summary: str
    gaps: tuple[str, ...]
    assessor_questions: tuple[str, ...]
    citations: tuple[dict[str, Any], ...]
    context_complete: bool


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    rows: tuple[ParsedMatrixRow, ...]


@dataclass(frozen=True, slots=True)
class ModelCallMetadata:
    attempt: int
    raw_response: str
    response_sha256: str
    latency_ms: int | None
    model_reported: str
    provider_request_id: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class BatchValidationError(Exception):
    cause: ResponseValidationError
    model_calls: tuple[ModelCallMetadata, ...]
    llm_call_count: int
    response_sha256: str | None

    def __str__(self) -> str:
        return str(self.cause)


@dataclass(frozen=True, slots=True)
class SufficiencyMatrixResult:
    row_payloads: tuple[dict[str, Any], ...]
    validation_outcome: ValidationOutcome
    llm_call_count: int
    model_calls: tuple[ModelCallMetadata, ...]
    fact_bundle_sha256: str | None
    prompt_version: str
    prompt_sha256: str
    response_sha256: str | None
    error_code: str | None = None
    retryable: bool = False
