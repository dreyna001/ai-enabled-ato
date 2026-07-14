"""Tests for disposition transition and POA&M routing preconditions."""

from __future__ import annotations

import pytest

from ato_service.disposition_transitions import (
    DispositionTransitionError,
    require_decision_compatible_with_matrix_status,
    require_edited_summary_for_edited,
)


def test_evidence_requested_requires_insufficient_evidence_status() -> None:
    with pytest.raises(DispositionTransitionError) as exc_info:
        require_decision_compatible_with_matrix_status(
            decision="evidence_requested",
            system_status="partial",
        )
    assert exc_info.value.error_code == "request_schema_invalid"


def test_weakness_confirmed_requires_partial_or_unsupported() -> None:
    with pytest.raises(DispositionTransitionError):
        require_decision_compatible_with_matrix_status(
            decision="weakness_confirmed",
            system_status="insufficient_evidence",
        )


def test_weakness_confirmed_allows_partial_status() -> None:
    require_decision_compatible_with_matrix_status(
        decision="weakness_confirmed",
        system_status="partial",
    )


def test_edited_requires_summary() -> None:
    with pytest.raises(DispositionTransitionError):
        require_edited_summary_for_edited(decision="edited", edited_summary=None)
