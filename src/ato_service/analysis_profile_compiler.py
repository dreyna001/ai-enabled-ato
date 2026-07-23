"""Deterministic draft analysis profile compiler."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from ato_service.analysis_profile_validation import (
    AnalysisProfileSemanticError,
    validate_analysis_profile_semantics,
)
from ato_service.authority_catalog import AuthorityCatalogError
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)

_SCHEMA_RELATIVE_PATH = Path("docs/contracts/analysis-profile.schema.json")
_SCHEMA_VERSION = "2.0.0"
_QUALIFICATION_STATUS = "draft"
_FORMAT_CHECKER = FormatChecker()


class AnalysisProfileCompileError(ValueError):
    """Raised when draft analysis profile compilation fails."""


@dataclass(frozen=True)
class ProfileIdentity:
    profile_id: str
    profile_version: str
    certification_class: str | None
    impact_level: str | None


@cache
def _analysis_profile_validator(*, project_root: Path) -> Draft202012Validator:
    schema_path = (project_root / _SCHEMA_RELATIVE_PATH).resolve()
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise AnalysisProfileCompileError(
            "analysis profile schema is invalid or unreadable"
        ) from exc
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def compile_draft_analysis_profile(
    *,
    identity: ProfileIdentity,
    generated_at: datetime,
    assessment_items: Sequence[dict[str, Any]],
    artifact_requirements: Sequence[dict[str, Any]],
    cadence_rules: Sequence[dict[str, Any]],
    status_policy: dict[str, Any],
    manifest_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    """Compile and validate a deterministic draft analysis profile."""
    root = project_root.resolve()
    resolved_manifest_path = manifest_path.resolve()

    try:
        manifest = verify_authority_manifest(
            resolved_manifest_path,
            project_root=root,
        )
    except AuthorityManifestVerificationError as exc:
        raise AnalysisProfileCompileError(str(exc)) from exc
    except AuthorityCatalogError as exc:
        raise AnalysisProfileCompileError(str(exc)) from exc

    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id:
        raise AnalysisProfileCompileError(
            "verified authority manifest must declare manifest_id"
        )

    normalized_generated_at = _normalize_generated_at(generated_at)

    copied_assessment_items = _sorted_copy(
        assessment_items,
        array_name="assessment_items",
        id_field="assessment_item_id",
    )
    copied_artifact_requirements = _sorted_copy(
        artifact_requirements,
        array_name="artifact_requirements",
        id_field="artifact_id",
    )
    copied_cadence_rules = _sorted_copy(
        cadence_rules,
        array_name="cadence_rules",
        id_field="cadence_rule_id",
    )
    copied_status_policy = copy.deepcopy(status_policy)

    profile: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "profile_id": identity.profile_id,
        "profile_version": identity.profile_version,
        "authority_manifest_id": manifest_id,
        "generated_at": normalized_generated_at,
        "qualification_status": _QUALIFICATION_STATUS,
        "certification_class": identity.certification_class,
        "impact_level": identity.impact_level,
        "assessment_items": copied_assessment_items,
        "artifact_requirements": copied_artifact_requirements,
        "cadence_rules": copied_cadence_rules,
        "status_policy": copied_status_policy,
    }

    validator = _analysis_profile_validator(project_root=root)
    validation_error = next(validator.iter_errors(profile), None)
    if validation_error is not None:
        raise AnalysisProfileCompileError(
            _format_schema_error(validation_error)
        ) from validation_error

    try:
        validate_analysis_profile_semantics(
            profile,
            manifest_path=resolved_manifest_path,
            project_root=root,
        )
    except AnalysisProfileSemanticError as exc:
        raise AnalysisProfileCompileError(str(exc)) from exc

    return profile


def _normalize_generated_at(value: datetime) -> str:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise AnalysisProfileCompileError("generated_at must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sorted_copy(
    items: Sequence[dict[str, Any]],
    *,
    array_name: str,
    id_field: str,
) -> list[dict[str, Any]]:
    copied = copy.deepcopy(list(items))
    for index, item in enumerate(copied):
        if not isinstance(item, dict):
            raise AnalysisProfileCompileError(
                f"{array_name} entry at index {index} must be an object"
            )
        item_id = item.get(id_field)
        if not isinstance(item_id, str) or not item_id:
            raise AnalysisProfileCompileError(
                f"{array_name} entry at index {index} must declare {id_field}"
            )

    copied.sort(key=lambda item: item[id_field])
    return copied


def _format_schema_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"analysis profile failed schema validation at {path}: {error.message}"
    return f"analysis profile failed schema validation: {error.message}"
