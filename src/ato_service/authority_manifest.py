"""Deterministic verification for the pinned authority manifest contract."""

from __future__ import annotations

from functools import cache
import hashlib
import json
from pathlib import Path
import stat
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

_HASH_BLOCK_SIZE = 1024 * 1024
_FORMAT_CHECKER = FormatChecker()


class AuthorityManifestError(ValueError):
    """Base error for authority manifest verification."""


class AuthorityManifestVerificationError(AuthorityManifestError):
    """Raised when the authority manifest or referenced bytes fail verification."""


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise AuthorityManifestError("Could not locate project root (pyproject.toml not found)")


@cache
def _load_schema(schema_path: Path) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _authority_manifest_validator(
    *,
    schema_path: Path | None,
    project_root: Path,
) -> Draft202012Validator:
    resolved_schema_path = schema_path
    if resolved_schema_path is None:
        resolved_schema_path = (
            project_root / "docs" / "contracts" / "authority-manifest.schema.json"
        )
        if not resolved_schema_path.is_file():
            resolved_schema_path = (
                _find_project_root()
                / "docs"
                / "contracts"
                / "authority-manifest.schema.json"
            )

    try:
        schema = _load_schema(resolved_schema_path.resolve())
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise AuthorityManifestVerificationError(
            "authority manifest schema is invalid or unreadable"
        ) from exc

    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as blob_file:
        while True:
            chunk = blob_file.read(_HASH_BLOCK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_authority_manifest(
    manifest_path: Path,
    *,
    project_root: Path | None = None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Validate manifest schema and verify every referenced local artifact digest."""
    root = (project_root or _find_project_root(manifest_path.parent)).resolve()
    resolved_manifest_path = manifest_path.resolve()

    try:
        manifest = json.loads(resolved_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityManifestVerificationError(
            "authority manifest is missing or unreadable"
        ) from exc

    if not isinstance(manifest, dict):
        raise AuthorityManifestVerificationError(
            "authority manifest must be a JSON object"
        )

    validator = _authority_manifest_validator(
        schema_path=schema_path,
        project_root=root,
    )
    validation_error = next(validator.iter_errors(manifest), None)
    if validation_error is not None:
        raise AuthorityManifestVerificationError(
            _format_schema_error(validation_error)
        ) from validation_error

    authority_ids = [source["authority_id"] for source in manifest["sources"]]
    if len(authority_ids) != len(set(authority_ids)):
        raise AuthorityManifestVerificationError("duplicate authority_id values")

    for source in manifest["sources"]:
        local_path = source["local_path"]
        if not isinstance(local_path, str):
            raise AuthorityManifestVerificationError(
                f"{source['authority_id']} must declare local_path"
            )

        authority_path = (root / local_path).resolve()
        try:
            authority_path.relative_to(root)
        except ValueError as error:
            raise AuthorityManifestVerificationError(
                f"{source['authority_id']} local_path escapes repository"
            ) from error

        try:
            artifact_stat = authority_path.stat()
        except OSError as error:
            raise AuthorityManifestVerificationError(
                f"missing authority file for {source['authority_id']}"
            ) from error

        if not stat.S_ISREG(artifact_stat.st_mode):
            raise AuthorityManifestVerificationError(
                f"authority artifact is not a regular file for {source['authority_id']}"
            )

        if artifact_stat.st_size != source["size_bytes"]:
            raise AuthorityManifestVerificationError(
                f"{source['authority_id']} size_bytes does not match local artifact"
            )

        try:
            actual_digest = _hash_file(authority_path)
        except OSError as error:
            raise AuthorityManifestVerificationError(
                f"authority artifact is unreadable for {source['authority_id']}"
            ) from error

        if actual_digest != source["sha256"]:
            raise AuthorityManifestVerificationError(
                f"{source['authority_id']} sha256 does not match local artifact"
            )

    return manifest


def _format_schema_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"authority manifest failed schema validation at {path}: {error.message}"
    return f"authority manifest failed schema validation: {error.message}"
