"""Deterministic matrix status ceiling evaluation for analyzer persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_STATUS_CEILING_VIOLATED = "status_ceiling_violated"

_MATRIX_STATUSES = (
    "supported",
    "partial",
    "unsupported",
    "insufficient_evidence",
)

# Claim-favorable ordering used for ceiling comparison (higher = more favorable).
_STATUS_RANK = {
    "supported": 4,
    "partial": 3,
    "insufficient_evidence": 2,
    "unsupported": 1,
}


@dataclass(frozen=True, slots=True)
class StatusCeilingViolatedError(Exception):
    """Raised when model-proposed status exceeds the deterministic ceiling."""

    error_code: str
    assessment_item_id: str
    model_proposed_status: str
    ceiling_status: str

    def __post_init__(self) -> None:
        if self.error_code != _STATUS_CEILING_VIOLATED:
            raise ValueError(f"error_code must be {_STATUS_CEILING_VIOLATED!r}")

    def __str__(self) -> str:
        return (
            f"status ceiling violated for {self.assessment_item_id}: "
            f"model_proposed={self.model_proposed_status!r} "
            f"ceiling={self.ceiling_status!r}"
        )


@dataclass(frozen=True, slots=True)
class MatrixRowCeilingInput:
    """Evidence and model inputs used to derive one matrix row system status."""

    assessment_item_id: str
    model_proposed_status: str
    context_complete: bool
    citations: tuple[Any, ...]
    all_evidence_stale: bool = False


def _require_matrix_status(status: str, *, field_name: str) -> str:
    if status not in _MATRIX_STATUSES:
        raise ValueError(f"{field_name} must be one of {_MATRIX_STATUSES}")
    return status


def has_usable_evidence(*, citations: tuple[Any, ...]) -> bool:
    """Return whether linked citations provide usable direct evidence."""
    return bool(citations)


def compute_status_ceiling(
    *,
    context_complete: bool,
    citations: tuple[Any, ...],
    all_evidence_stale: bool,
    status_policy: dict[str, Any] | None = None,
) -> str:
    """Return the most favorable status allowed by deterministic ceilings."""
    policy = status_policy or {}
    no_evidence_status = policy.get("no_evidence_status", "insufficient_evidence")
    incomplete_context_ceiling = policy.get(
        "incomplete_context_ceiling",
        "partial",
    )
    all_stale_ceiling = policy.get("all_stale_ceiling", "partial")

    _require_matrix_status(no_evidence_status, field_name="no_evidence_status")
    _require_matrix_status(
        incomplete_context_ceiling,
        field_name="incomplete_context_ceiling",
    )
    _require_matrix_status(all_stale_ceiling, field_name="all_stale_ceiling")

    if not has_usable_evidence(citations=citations):
        return no_evidence_status

    ceiling = "supported"
    if not context_complete:
        ceiling = _min_favorable_status(ceiling, incomplete_context_ceiling)
    if all_evidence_stale:
        ceiling = _min_favorable_status(ceiling, all_stale_ceiling)
    return ceiling


def evaluate_system_status(
    row: MatrixRowCeilingInput,
    *,
    status_policy: dict[str, Any] | None = None,
) -> str:
    """Return the published system_status or raise when the model is too favorable."""
    model_status = _require_matrix_status(
        row.model_proposed_status,
        field_name="model_proposed_status",
    )
    ceiling = compute_status_ceiling(
        context_complete=row.context_complete,
        citations=row.citations,
        all_evidence_stale=row.all_evidence_stale,
        status_policy=status_policy,
    )
    if _STATUS_RANK[model_status] > _STATUS_RANK[ceiling]:
        raise StatusCeilingViolatedError(
            error_code=_STATUS_CEILING_VIOLATED,
            assessment_item_id=row.assessment_item_id,
            model_proposed_status=model_status,
            ceiling_status=ceiling,
        )
    return model_status


def apply_status_ceiling_to_row_payload(
    payload: dict[str, Any],
    *,
    status_policy: dict[str, Any] | None = None,
    all_evidence_stale: bool = False,
) -> dict[str, Any]:
    """Apply deterministic ceilings to one matrix-row payload before persistence."""
    citations = payload.get("citations")
    if not isinstance(citations, list):
        citations = []
    system_status = evaluate_system_status(
        MatrixRowCeilingInput(
            assessment_item_id=str(payload["assessment_item_id"]),
            model_proposed_status=str(payload["model_proposed_status"]),
            context_complete=bool(payload.get("context_complete")),
            citations=tuple(citations),
            all_evidence_stale=all_evidence_stale,
        ),
        status_policy=status_policy,
    )
    updated = dict(payload)
    updated["system_status"] = system_status
    return updated


def _min_favorable_status(left: str, right: str) -> str:
    left = _require_matrix_status(left, field_name="status")
    right = _require_matrix_status(right, field_name="status")
    return left if _STATUS_RANK[left] <= _STATUS_RANK[right] else right
