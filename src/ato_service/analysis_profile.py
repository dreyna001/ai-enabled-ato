"""Pinned deterministic analysis profile loading and digest verification."""

from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from ato_service.idempotency import canonical_json_bytes

_PROFILE_FILENAME = "analysis-profile.valid.fisma-synthetic.json"
_SCHEMA_RELATIVE_PATH = Path("docs/contracts/analysis-profile.schema.json")
_FIXTURE_RELATIVE_PATH = Path("docs/contracts/fixtures") / _PROFILE_FILENAME
_FORMAT_CHECKER = FormatChecker()


class AnalysisProfileError(ValueError):
    """Raised when the pinned analysis profile cannot be loaded or validated."""


@cache
def _analysis_profile_validator(*, project_root: Path) -> Draft202012Validator:
    schema_path = (project_root / _SCHEMA_RELATIVE_PATH).resolve()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def default_fisma_synthetic_profile_path(*, project_root: Path) -> Path:
    """Return the pinned FISMA synthetic analysis profile fixture path."""
    return (project_root / _FIXTURE_RELATIVE_PATH).resolve()


def load_pinned_fisma_synthetic_profile(*, project_root: Path) -> dict[str, Any]:
    """Load and schema-validate the pinned FISMA synthetic analysis profile."""
    profile_path = default_fisma_synthetic_profile_path(project_root=project_root)
    try:
        document = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisProfileError("pinned analysis profile is unreadable") from exc

    validator = _analysis_profile_validator(project_root=project_root)
    error = next(validator.iter_errors(document), None)
    if error is not None:
        raise AnalysisProfileError(f"pinned analysis profile failed schema validation: {error.message}")

    return document


def analysis_profile_sha256(profile: dict[str, Any]) -> str:
    """Return the SHA-256 digest of canonical profile JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(profile)).hexdigest()


def expected_assessment_item_ids(profile: dict[str, Any]) -> tuple[str, ...]:
    """Return sorted unique assessment item identifiers from a profile."""
    items = profile.get("assessment_items")
    if not isinstance(items, list):
        raise AnalysisProfileError("assessment_items must be a list")
    identifiers: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise AnalysisProfileError("assessment item must be an object")
        identifier = item.get("assessment_item_id")
        if not isinstance(identifier, str) or not identifier:
            raise AnalysisProfileError("assessment_item_id must be a nonempty string")
        identifiers.append(identifier)
    return tuple(sorted(set(identifiers)))


def assessment_item_type_for_id(profile: dict[str, Any], assessment_item_id: str) -> str:
    """Return the assessment item type for one profile identifier."""
    for item in profile["assessment_items"]:
        if item["assessment_item_id"] == assessment_item_id:
            assessment_item_type = item["assessment_item_type"]
            if not isinstance(assessment_item_type, str):
                raise AnalysisProfileError("assessment_item_type must be a string")
            return assessment_item_type
    raise AnalysisProfileError(f"unknown assessment item id: {assessment_item_id}")
