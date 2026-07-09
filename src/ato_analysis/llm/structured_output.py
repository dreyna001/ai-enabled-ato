"""Parse and validate structured LLM outputs for Block 1."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ato_analysis.llm.client import LLMClient
from ato_analysis.llm.prompts import REPAIR_SYSTEM
from ato_analysis.models.package_schema import PackageModel
from ato_analysis.models.report_schema import EvidenceMatrixRow

_JSON_FENCE_PATTERN = re.compile(
    r"^```(?:json)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def extract_json_from_text(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text, stripping markdown fences if present."""
    if not text or not text.strip():
        raise ValueError("Model response text is empty")

    candidate = text.strip()
    if candidate.startswith("\ufeff"):
        candidate = candidate[1:].strip()

    fence_match = _JSON_FENCE_PATTERN.match(candidate)
    if fence_match:
        candidate = fence_match.group(1).strip()

    if not candidate.startswith("{"):
        first_brace = candidate.find("{")
        if first_brace == -1:
            raise ValueError("Model response does not contain a JSON object")
        candidate = _extract_balanced_object(candidate[first_brace:]) or candidate[
            first_brace:
        ]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model response is not valid JSON: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Model response JSON must be an object")

    return parsed


def validate_normalized_package(data: dict[str, Any]) -> PackageModel:
    """Validate LLM normalize output against the canonical package schema."""
    return PackageModel.model_validate(data)


def validate_matrix_rows(
    rows: list[Any],
    package: PackageModel,
    stale_ids: set[str] | list[str],
) -> tuple[list[EvidenceMatrixRow], list[str]]:
    """Validate matrix rows with post-LLM deterministic checks.

    Returns validated rows and a list of error messages for invalid rows.
    """
    known_stale = set(stale_ids)
    evidence_by_id = {item.evidence_id: item for item in package.evidence_items}
    controls_by_id = {control.control_id: control for control in package.controls}
    linked_by_control = {
        control.control_id: set(control.linked_evidence_ids)
        for control in package.controls
    }

    validated: list[EvidenceMatrixRow] = []
    errors: list[str] = []

    for index, raw_row in enumerate(rows):
        row_label = f"rows[{index}]"
        try:
            row = (
                raw_row
                if isinstance(raw_row, EvidenceMatrixRow)
                else EvidenceMatrixRow.model_validate(raw_row)
            )
        except ValidationError as exc:
            errors.append(f"{row_label}: schema validation failed: {exc}")
            continue

        row_errors = _collect_row_errors(
            row=row,
            evidence_by_id=evidence_by_id,
            controls_by_id=controls_by_id,
            linked_by_control=linked_by_control,
            known_stale=known_stale,
        )
        if row_errors:
            errors.extend(row_errors)
        else:
            validated.append(row)

    return validated, errors


def repair_matrix_call(
    client: LLMClient,
    *,
    batch_facts_json: str,
    invalid_output: dict[str, Any],
    errors: list[str],
    schema_hint: str,
) -> dict[str, Any]:
    """Perform one LLM repair attempt for invalid matrix batch output."""
    error_lines = "\n".join(f"- {error}" for error in errors) or "- unknown validation error"
    user = (
        "The prior matrix JSON failed deterministic validation.\n\n"
        f"Validation errors:\n{error_lines}\n\n"
        "Original control and evidence fact records:\n"
        f"```json\n{batch_facts_json}\n```\n\n"
        "Invalid model output to repair:\n"
        f"```json\n{json.dumps(invalid_output, indent=2)}\n```\n\n"
        "Return corrected JSON with key \"rows\" containing the full repaired batch.\n\n"
        f"{schema_hint}"
    )
    return client.complete_json(system=REPAIR_SYSTEM, user=user, schema_hint="")


def _collect_row_errors(
    *,
    row: EvidenceMatrixRow,
    evidence_by_id: dict[str, Any],
    controls_by_id: dict[str, Any],
    linked_by_control: dict[str, set[str]],
    known_stale: set[str],
) -> list[str]:
    row_label = f"control {row.control_id}"
    row_errors: list[str] = []

    if row.control_id not in controls_by_id:
        return [f"{row_label}: unknown control_id"]

    linked_ids = linked_by_control[row.control_id]

    for stale_id in row.stale_evidence_ids:
        if stale_id not in evidence_by_id:
            row_errors.append(
                f"{row_label}: stale_evidence_id {stale_id!r} is not in package"
            )
        elif stale_id not in linked_ids:
            row_errors.append(
                f"{row_label}: stale_evidence_id {stale_id!r} is not linked to control"
            )
        elif stale_id not in known_stale:
            row_errors.append(
                f"{row_label}: stale_evidence_id {stale_id!r} was not flagged stale "
                f"deterministically"
            )

    if row.sufficiency_status == "supported" and not row.citations:
        row_errors.append(
            f"{row_label}: sufficiency_status 'supported' requires citations"
        )

    for citation_index, citation in enumerate(row.citations):
        evidence = evidence_by_id.get(citation.evidence_id)
        if evidence is None:
            row_errors.append(
                f"{row_label}: citations[{citation_index}] references unknown "
                f"evidence_id {citation.evidence_id!r}"
            )
            continue
        if citation.evidence_id not in linked_ids:
            row_errors.append(
                f"{row_label}: citations[{citation_index}] evidence_id "
                f"{citation.evidence_id!r} is not linked to control"
            )
        if not _excerpt_in_source(citation.excerpt, evidence.text):
            row_errors.append(
                f"{row_label}: citations[{citation_index}] excerpt not found in "
                f"source evidence text for {citation.evidence_id!r}"
            )

    return row_errors


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _excerpt_in_source(excerpt: str, source_text: str) -> bool:
    if not excerpt.strip():
        return False
    return _normalize_whitespace(excerpt) in _normalize_whitespace(source_text)


def _extract_balanced_object(text: str) -> str | None:
    if not text.startswith("{"):
        return None

    depth = 0
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[: index + 1]

    return None
