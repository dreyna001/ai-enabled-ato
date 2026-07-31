"""Pinned NIST OSCAL 1.2.2 SSP schema loading and fail-closed validation."""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from ato_service.authority_catalog import (
    AuthorityCatalogError,
    load_json_authority_archive_member,
)
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)

OSCAL_SSP_AUTHORITY_ID = "nist-oscal-1.2.2"
OSCAL_SSP_SCHEMA_MEMBER_SUFFIX = "json/schema/oscal_ssp_schema.json"
OSCAL_VERSION = "1.2.2"
_OFFICIAL_TOKEN_DATATYPE_PATTERN = r"^(\p{L}|_)(\p{L}|\p{N}|[.\-_])*$"
_TOKEN_DATATYPE_PYTHON_PATTERN = r"^([A-Za-z_])([A-Za-z0-9._-])*$"
_UNICODE_PROPERTY_PATTERN = re.compile(r"\\p\{")
_FORMAT_CHECKER = FormatChecker()


class OscalSspSchemaError(ValueError):
    """Raised when the OSCAL SSP schema or a document fails validation."""


def validate_oscal_ssp_document(
    document: dict[str, Any],
    *,
    project_root: Path,
) -> None:
    """Validate one OSCAL SSP root document against the pinned official schema."""
    if not isinstance(document, dict):
        raise OscalSspSchemaError("OSCAL SSP document must be a JSON object")
    if "system-security-plan" not in document:
        raise OscalSspSchemaError(
            "OSCAL SSP document must include system-security-plan"
        )

    validator = _oscal_ssp_validator(project_root=project_root.resolve())
    validation_error = next(validator.iter_errors(document), None)
    if validation_error is not None:
        raise OscalSspSchemaError(_format_validation_error(validation_error))


@lru_cache(maxsize=4)
def _oscal_ssp_validator(*, project_root: str) -> Draft7Validator:
    root = Path(project_root)
    manifest_path = root / "docs" / "contracts" / "authority-manifest.json"
    try:
        manifest = verify_authority_manifest(manifest_path, project_root=root)
        _, schema = load_json_authority_archive_member(
            manifest=manifest,
            authority_id=OSCAL_SSP_AUTHORITY_ID,
            project_root=root,
            member_suffix=OSCAL_SSP_SCHEMA_MEMBER_SUFFIX,
        )
    except (AuthorityManifestVerificationError, AuthorityCatalogError) as error:
        raise OscalSspSchemaError(str(error)) from error

    prepared = _prepare_schema_for_python_jsonschema(schema)
    try:
        Draft7Validator.check_schema(prepared)
    except SchemaError as error:
        raise OscalSspSchemaError(
            "pinned OSCAL SSP schema is not usable with jsonschema"
        ) from error
    return Draft7Validator(prepared, format_checker=_FORMAT_CHECKER)


def _prepare_schema_for_python_jsonschema(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite only the official OSCAL TokenDatatype pattern for Python ``re``.

    The pinned NIST schema uses ``\\p{…}`` property classes that CPython's
    ``re`` module cannot compile. Only the known TokenDatatype pattern is
    substituted with an equivalent ASCII NCName-style pattern. Any other
    unsupported Unicode property pattern fails closed.
    """
    unsupported_patterns: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            if isinstance(pattern, str) and _UNICODE_PROPERTY_PATTERN.search(pattern):
                unsupported_patterns.add(pattern)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(schema)
    if unsupported_patterns and unsupported_patterns != {_OFFICIAL_TOKEN_DATATYPE_PATTERN}:
        raise OscalSspSchemaError(
            "pinned OSCAL SSP schema declares unsupported Unicode property "
            f"regex patterns: {sorted(unsupported_patterns)!r}"
        )

    prepared = copy.deepcopy(schema)

    def rewrite(node: Any) -> None:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            if pattern == _OFFICIAL_TOKEN_DATATYPE_PATTERN:
                node["pattern"] = _TOKEN_DATATYPE_PYTHON_PATTERN
            for value in node.values():
                rewrite(value)
        elif isinstance(node, list):
            for item in node:
                rewrite(item)

    rewrite(prepared)
    return prepared


def _format_validation_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"OSCAL SSP document failed schema validation at {path}: {error.message}"
    return f"OSCAL SSP document failed schema validation: {error.message}"
