"""LLM-based normalization from arbitrary customer shapes to canonical model."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ato_analysis.config import Settings
from ato_analysis.llm.client import LLMClient
from ato_analysis.llm.prompts import NORMALIZE_SYSTEM, NORMALIZE_USER, REPAIR_SYSTEM
from ato_analysis.llm.structured_output import validate_normalized_package
from ato_analysis.models.package_schema import PackageModel

_PACKAGE_SCHEMA_HINT = """Canonical package JSON object fields:
- package_id (string, must match filename stem)
- authorization_path: "fisma_agency"
- baseline: "NIST-SP-800-53-R5"
- impact_level, data_classification, system_name, authorization_boundary (strings)
- assessment_date (ISO date YYYY-MM-DD)
- freshness_threshold_days (optional integer, default 365)
- controls: array of { control_id, control_title, control_requirement,
  implementation_statement, linked_evidence_ids[] }
- evidence_items: array of { evidence_id, title, source_type, source_owner,
  collected_at (ISO date), text }
Control IDs must match ^[A-Z]{2,3}-\\d+(\\(\\d+\\))?$ (e.g. AC-2, AC-2(1)).
"""


def normalize_to_canonical(
    raw_parsed: dict[str, Any] | str,
    package_id: str,
    client: LLMClient,
    settings: Settings,
) -> PackageModel:
    """Normalize customer input to the canonical package model."""
    _ = settings

    existing = _try_existing_canonical(raw_parsed, package_id)
    if existing is not None:
        return existing

    raw_content = (
        raw_parsed
        if isinstance(raw_parsed, str)
        else json.dumps(raw_parsed, indent=2, default=str)
    )
    user = NORMALIZE_USER.format(
        package_id=package_id,
        raw_content=raw_content,
        schema_hint=_PACKAGE_SCHEMA_HINT,
    )
    parsed = client.complete_json(
        system=NORMALIZE_SYSTEM,
        user=user,
        schema_hint="",
    )

    try:
        return validate_normalized_package(parsed)
    except ValidationError as exc:
        repaired = _repair_normalize(
            client,
            package_id=package_id,
            raw_content=raw_content,
            invalid_output=parsed,
            errors=[str(exc)],
        )
        return validate_normalized_package(repaired)


def _try_existing_canonical(
    raw_parsed: dict[str, Any] | str,
    package_id: str,
) -> PackageModel | None:
    if not isinstance(raw_parsed, dict):
        return None
    try:
        package = PackageModel.model_validate(raw_parsed)
    except ValidationError:
        return None
    if package.package_id != package_id:
        return None
    return package


def _repair_normalize(
    client: LLMClient,
    *,
    package_id: str,
    raw_content: str,
    invalid_output: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    error_lines = "\n".join(f"- {error}" for error in errors) or "- unknown validation error"
    user = (
        f"Package ID (must match filename stem): {package_id}\n\n"
        "The prior normalize JSON failed schema validation.\n\n"
        f"Validation errors:\n{error_lines}\n\n"
        "Original raw customer input:\n"
        f"```\n{raw_content}\n```\n\n"
        "Invalid model output to repair:\n"
        f"```json\n{json.dumps(invalid_output, indent=2, default=str)}\n```\n\n"
        "Return one corrected JSON object matching the canonical package schema.\n\n"
        f"{_PACKAGE_SCHEMA_HINT}"
    )
    return client.complete_json(system=REPAIR_SYSTEM, user=user, schema_hint="")
