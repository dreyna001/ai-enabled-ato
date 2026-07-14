"""Apply deterministic status ceilings before matrix row persistence."""

from __future__ import annotations

from typing import Any

from ato_service.status_ceiling_evaluator import (
    StatusCeilingViolatedError,
    apply_status_ceiling_to_row_payload,
)


def apply_ceilings_to_matrix_row_payloads(
    payloads: list[dict[str, Any]],
    *,
    status_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return ceiling-adjusted matrix-row payloads or raise on hostile model output."""
    adjusted: list[dict[str, Any]] = []
    for payload in payloads:
        adjusted.append(
            apply_status_ceiling_to_row_payload(
                payload,
                status_policy=status_policy,
            )
        )
    return adjusted


__all__ = [
    "StatusCeilingViolatedError",
    "apply_ceilings_to_matrix_row_payloads",
]
