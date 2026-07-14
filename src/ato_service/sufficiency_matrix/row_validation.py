"""Protected assessor/AO field and prohibited-claim validation."""

from __future__ import annotations

from typing import Any

from ato_service.sufficiency_matrix.constants import PROHIBITED_TEXT_MARKERS
from ato_service.sufficiency_matrix.types import ResponseValidationError


def validate_protected_row_fields(row: dict[str, Any]) -> None:
    """Reject model attempts to populate assessor-owned or prohibited content."""
    for field_name in ("finding_summary",):
        value = row.get(field_name)
        if isinstance(value, str):
            _reject_prohibited_text(value, field_name=field_name)

    for field_name in ("gaps", "assessor_questions"):
        values = row.get(field_name)
        if not isinstance(values, list):
            continue
        for index, entry in enumerate(values):
            if isinstance(entry, str):
                _reject_prohibited_text(entry, field_name=f"{field_name}[{index}]")

    for key in row:
        if key.startswith("assessor_") and key != "assessor_questions":
            raise ResponseValidationError(
                failure_kind="policy",
                detail=f"prohibited field {key}",
                repairable=False,
            )


def _reject_prohibited_text(value: str, *, field_name: str) -> None:
    normalized = value.casefold()
    for marker in PROHIBITED_TEXT_MARKERS:
        if marker.casefold() in normalized:
            raise ResponseValidationError(
                failure_kind="policy",
                detail=f"{field_name} contains prohibited assessor or AO content",
                repairable=False,
            )
