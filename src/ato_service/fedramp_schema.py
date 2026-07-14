"""Vendored FedRAMP official schema validation within hard-stop boundaries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

_COMMON_DEFS_ID = "https://fedramp.gov/schemas/fedramp-common-definitions-schema-2026-06-24.json"
_COMMON_DEFS_FRAGMENT_RE = re.compile(
    re.escape(_COMMON_DEFS_ID) + r"/\$defs/([A-Za-z0-9_]+)"
)

FEDRAMP_OFFICIAL_SCHEMA_BINDINGS: dict[str, dict[str, str]] = {
    "cpo": {
        "glob": "fedramp-certification-package-overview-schema-*.json",
        "authority_id": "fedramp-schema-cpo-2026-06-24",
    },
    "sdr": {
        "glob": "fedramp-security-decision-record-schema-*.json",
        "authority_id": "fedramp-schema-sdr-2026-06-24",
    },
    "ocr": {
        "glob": "fedramp-ongoing-certification-report-schema-*.json",
        "authority_id": "fedramp-schema-ocr-2026-06-24",
    },
}


@dataclass(frozen=True, slots=True)
class SchemaValidationResult:
    artifact_key: str
    schema_available: bool
    payload_present: bool
    structurally_valid: bool
    authority_id: str | None
    errors: tuple[str, ...]


def fedramp_schema_dir(project_root: Path) -> Path:
    return project_root / "reference" / "authorities" / "fedramp"


def first_schema_path(*, directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    return matches[0] if matches else None


def validate_fedramp_official_payload(
    *,
    artifact_key: str,
    payload: Any,
    project_root: Path,
) -> SchemaValidationResult:
    """Validate one official-shaped payload against a vendored reviewed schema."""
    binding = FEDRAMP_OFFICIAL_SCHEMA_BINDINGS.get(artifact_key)
    if binding is None:
        return SchemaValidationResult(
            artifact_key=artifact_key,
            schema_available=False,
            payload_present=payload is not None,
            structurally_valid=False,
            authority_id=None,
            errors=("unsupported_artifact_key",),
        )

    schema_dir = fedramp_schema_dir(project_root)
    if not schema_dir.is_dir():
        return SchemaValidationResult(
            artifact_key=artifact_key,
            schema_available=False,
            payload_present=payload is not None,
            structurally_valid=False,
            authority_id=None,
            errors=("authority_schemas_unavailable",),
        )

    schema_path = first_schema_path(directory=schema_dir, pattern=binding["glob"])
    if schema_path is None:
        return SchemaValidationResult(
            artifact_key=artifact_key,
            schema_available=False,
            payload_present=payload is not None,
            structurally_valid=False,
            authority_id=None,
            errors=(f"schema_missing_for_{artifact_key}",),
        )

    if payload is None:
        return SchemaValidationResult(
            artifact_key=artifact_key,
            schema_available=True,
            payload_present=False,
            structurally_valid=False,
            authority_id=binding["authority_id"],
            errors=(f"missing_{artifact_key}",),
        )

    if not isinstance(payload, dict):
        return SchemaValidationResult(
            artifact_key=artifact_key,
            schema_available=True,
            payload_present=True,
            structurally_valid=False,
            authority_id=binding["authority_id"],
            errors=(f"structural_invalid_{artifact_key}",),
        )

    validator = _validator_for_schema_path(schema_path=schema_path, schema_dir=schema_dir)
    errors = sorted(error.message for error in validator.iter_errors(payload))
    return SchemaValidationResult(
        artifact_key=artifact_key,
        schema_available=True,
        payload_present=True,
        structurally_valid=not errors,
        authority_id=binding["authority_id"],
        errors=tuple(errors),
    )


def evaluate_schema_purity(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    project_root: Path,
) -> list[SchemaValidationResult]:
    """Return deterministic schema-purity results for profile-bound official payloads."""
    if profile_id == "fedramp_20x_program":
        section = sealed_document.get("fedramp_20x")
        if not isinstance(section, dict):
            return [
                validate_fedramp_official_payload(
                    artifact_key=key,
                    payload=None,
                    project_root=project_root,
                )
                for key in FEDRAMP_OFFICIAL_SCHEMA_BINDINGS
            ]
        return [
            validate_fedramp_official_payload(
                artifact_key=key,
                payload=section.get(key),
                project_root=project_root,
            )
            for key in ("cpo", "sdr", "ocr")
        ]
    return []


@lru_cache(maxsize=16)
def _validator_for_schema_path(*, schema_path: str, schema_dir: str) -> Draft202012Validator:
    prepared = _prepare_schema(
        schema=json.loads(Path(schema_path).read_text(encoding="utf-8")),
        schema_dir=Path(schema_dir),
    )
    return Draft202012Validator(prepared, format_checker=FormatChecker())


def _prepare_schema(*, schema: dict[str, Any], schema_dir: Path) -> dict[str, Any]:
    common_path = schema_dir / "fedramp-common-definitions-schema-2026-06-24.json"
    prepared = dict(schema)
    defs: dict[str, Any] = dict(prepared.get("$defs") or {})
    if common_path.is_file():
        common = json.loads(common_path.read_text(encoding="utf-8"))
        defs.update(common.get("$defs") or {})
    if defs:
        prepared["$defs"] = defs
    return _rewrite_common_definition_refs(prepared)


def _rewrite_common_definition_refs(node: Any) -> Any:
    if isinstance(node, dict):
        rewritten: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                match = _COMMON_DEFS_FRAGMENT_RE.fullmatch(value)
                if match is not None:
                    rewritten[key] = f"#/$defs/{match.group(1)}"
                    continue
            rewritten[key] = _rewrite_common_definition_refs(value)
        return rewritten
    if isinstance(node, list):
        return [_rewrite_common_definition_refs(item) for item in node]
    return node
