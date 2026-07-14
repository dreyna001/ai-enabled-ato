"""Tests for deterministic matrix status ceiling evaluation."""

from __future__ import annotations

import pytest

from ato_service.status_ceiling_evaluator import (
    MatrixRowCeilingInput,
    StatusCeilingViolatedError,
    apply_status_ceiling_to_row_payload,
    compute_status_ceiling,
    evaluate_system_status,
)


def test_no_usable_evidence_forces_insufficient_evidence_ceiling() -> None:
    assert (
        compute_status_ceiling(
            context_complete=False,
            citations=(),
            all_evidence_stale=False,
        )
        == "insufficient_evidence"
    )


def test_incomplete_context_caps_supported_to_partial() -> None:
    ceiling = compute_status_ceiling(
        context_complete=False,
        citations=({"source_id": "artifact-1"},),
        all_evidence_stale=False,
    )
    assert ceiling == "partial"


def test_hostile_supported_without_evidence_raises_violation() -> None:
    with pytest.raises(StatusCeilingViolatedError) as exc_info:
        evaluate_system_status(
            MatrixRowCeilingInput(
                assessment_item_id="AC-1",
                model_proposed_status="supported",
                context_complete=False,
                citations=(),
            )
        )

    assert exc_info.value.error_code == "status_ceiling_violated"
    assert exc_info.value.ceiling_status == "insufficient_evidence"


def test_hostile_supported_with_incomplete_context_raises_violation() -> None:
    with pytest.raises(StatusCeilingViolatedError) as exc_info:
        evaluate_system_status(
            MatrixRowCeilingInput(
                assessment_item_id="AC-2",
                model_proposed_status="supported",
                context_complete=False,
                citations=({"source_id": "artifact-1"},),
            )
        )

    assert exc_info.value.error_code == "status_ceiling_violated"
    assert exc_info.value.ceiling_status == "partial"


def test_partial_within_ceiling_is_published() -> None:
    assert (
        evaluate_system_status(
            MatrixRowCeilingInput(
                assessment_item_id="AC-2",
                model_proposed_status="partial",
                context_complete=False,
                citations=({"source_id": "artifact-1"},),
            )
        )
        == "partial"
    )


def test_apply_status_ceiling_updates_system_status_on_row_payload() -> None:
    payload = {
        "assessment_item_id": "AC-1",
        "model_proposed_status": "partial",
        "system_status": "supported",
        "context_complete": True,
        "citations": [{"source_id": "artifact-1"}],
    }
    adjusted = apply_status_ceiling_to_row_payload(payload)
    assert adjusted["system_status"] == "partial"
