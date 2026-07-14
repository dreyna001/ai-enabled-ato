"""Response parsing and contract validation for sufficiency_matrix."""

from __future__ import annotations

import json
from functools import cache
from typing import Sequence

from jsonschema import Draft202012Validator

from ato_service.matrix_coverage import require_exact_matrix_coverage
from ato_service.normalize_proposal.json_utils import NormalizeJsonError, parse_response_json
from ato_service.sufficiency_matrix.constants import RESPONSE_SCHEMA_VERSION, response_schema_path
from ato_service.sufficiency_matrix.row_validation import validate_protected_row_fields
from ato_service.sufficiency_matrix.types import ParsedMatrixRow, ParsedResponse, ResponseValidationError


@cache
def _response_validator() -> Draft202012Validator:
    schema = json.loads(response_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    return validator


def validate_and_parse_response(
    *,
    raw_text: str,
    expected_assessment_item_ids: Sequence[str],
) -> ParsedResponse:
    """Parse strict JSON and validate the sufficiency_matrix response contract."""
    try:
        payload = parse_response_json(raw_text)
    except NormalizeJsonError as exc:
        raise ResponseValidationError(
            failure_kind="parse",
            detail=str(exc),
            repairable=True,
        ) from exc

    if not isinstance(payload, dict):
        raise ResponseValidationError(
            failure_kind="schema",
            detail="response must be a JSON object",
            repairable=True,
        )

    schema_errors = sorted(
        _response_validator().iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if schema_errors:
        raise ResponseValidationError(
            failure_kind="schema",
            detail=schema_errors[0].message,
            repairable=True,
        )

    if payload.get("schema_version") != RESPONSE_SCHEMA_VERSION:
        raise ResponseValidationError(
            failure_kind="schema",
            detail="unsupported schema_version",
            repairable=True,
        )

    rows_raw = payload.get("rows")
    if not isinstance(rows_raw, list):
        raise ResponseValidationError(
            failure_kind="schema",
            detail="rows must be an array",
            repairable=True,
        )

    parsed_rows: list[ParsedMatrixRow] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(rows_raw):
        if not isinstance(entry, dict):
            raise ResponseValidationError(
                failure_kind="schema",
                detail=f"row {index} must be an object",
                repairable=True,
            )
        assessment_item_id = entry.get("assessment_item_id")
        if not isinstance(assessment_item_id, str) or not assessment_item_id:
            raise ResponseValidationError(
                failure_kind="schema",
                detail=f"row {index} assessment_item_id is invalid",
                repairable=True,
            )
        if assessment_item_id in seen_ids:
            raise ResponseValidationError(
                failure_kind="coverage",
                detail=f"duplicate assessment_item_id {assessment_item_id}",
                repairable=False,
            )
        seen_ids.add(assessment_item_id)
        validate_protected_row_fields(entry)
        parsed_rows.append(
            ParsedMatrixRow(
                assessment_item_id=assessment_item_id,
                model_proposed_status=str(entry["model_proposed_status"]),
                finding_summary=str(entry["finding_summary"]),
                gaps=tuple(str(item) for item in entry.get("gaps") or []),
                assessor_questions=tuple(
                    str(item) for item in entry.get("assessor_questions") or []
                ),
                citations=tuple(dict(item) for item in entry.get("citations") or []),
                context_complete=bool(entry.get("context_complete")),
            )
        )

    try:
        require_exact_matrix_coverage(
            expected_assessment_item_ids,
            [row.assessment_item_id for row in parsed_rows],
        )
    except Exception as exc:
        raise ResponseValidationError(
            failure_kind="coverage",
            detail=str(exc),
            repairable=False,
        ) from exc

    return ParsedResponse(rows=tuple(parsed_rows))
