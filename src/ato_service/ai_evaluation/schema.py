"""JSON Schema loading and validation for AI evaluation records."""

from __future__ import annotations

from functools import cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


_SCHEMA_VERSION = "1.0.0"
_FORMAT_CHECKER = FormatChecker()


class EvaluationSchemaError(ValueError):
    """Raised when the evaluation record schema cannot be loaded or used."""


def evaluation_record_schema_path(*, project_root: Path | None = None) -> Path:
    root = project_root or _default_project_root()
    return root / "docs" / "contracts" / "ai-evaluation-record.schema.json"


def evaluation_record_schema_version() -> str:
    return _SCHEMA_VERSION


@cache
def _default_project_root() -> Path:
    candidate = Path(__file__).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise EvaluationSchemaError("Could not locate project root (pyproject.toml not found)")


@cache
def evaluation_record_validator(*, project_root: Path | None = None) -> Draft202012Validator:
    schema_path = evaluation_record_schema_path(project_root=project_root)
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise EvaluationSchemaError(
            f"evaluation record schema is invalid: {schema_path}"
        ) from exc
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def format_validation_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"
