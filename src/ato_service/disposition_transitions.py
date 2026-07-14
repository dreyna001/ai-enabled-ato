"""Disposition decision validation for review revisions."""

from __future__ import annotations

from dataclasses import dataclass

_DISPOSITION_DECISIONS = frozenset(
    {
        "pending",
        "accepted",
        "edited",
        "rejected",
        "evidence_requested",
        "weakness_confirmed",
    }
)


@dataclass(frozen=True, slots=True)
class DispositionTransitionError(ValueError):
    """Raised when a disposition decision is illegal for the review state."""

    error_code: str
    message: str


def require_review_disposition_mutable(*, review_status: str) -> None:
    if review_status != "draft":
        raise DispositionTransitionError(
            error_code="illegal_state_transition",
            message="review revision is not in draft status",
        )


def require_disposition_decision(decision: str) -> str:
    if decision not in _DISPOSITION_DECISIONS:
        raise DispositionTransitionError(
            error_code="request_schema_invalid",
            message="disposition decision is not supported",
        )
    return decision


def require_edited_summary_for_edited(
    *,
    decision: str,
    edited_summary: str | None,
) -> None:
    if decision == "edited" and not edited_summary:
        raise DispositionTransitionError(
            error_code="request_schema_invalid",
            message="edited disposition requires edited_summary",
        )


def require_decision_compatible_with_matrix_status(
    *,
    decision: str,
    system_status: str,
) -> None:
    """Enforce POA&M routing preconditions at disposition mutation time."""
    if decision == "evidence_requested" and system_status != "insufficient_evidence":
        raise DispositionTransitionError(
            error_code="request_schema_invalid",
            message=(
                "evidence_requested is allowed only for insufficient_evidence rows"
            ),
        )
    if decision == "weakness_confirmed" and system_status not in {
        "partial",
        "unsupported",
    }:
        raise DispositionTransitionError(
            error_code="request_schema_invalid",
            message=(
                "weakness_confirmed is allowed only for partial or unsupported rows"
            ),
        )
