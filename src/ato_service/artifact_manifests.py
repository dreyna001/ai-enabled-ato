"""Durable, schema-validated run artifact manifest writer."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from ato_service.domain_mapping import format_utc_datetime
from ato_service.storage_reconciliation import (
    StoragePathError,
    ensure_storage_directory,
    manifest_staging_path,
    prepare_storage_file_path,
    require_storage_regular_file,
)

__all__ = (
    "ArtifactManifestCommitError",
    "ArtifactManifestError",
    "ArtifactManifestValidationError",
    "GeneratedRunFile",
    "StoredArtifactManifest",
    "load_run_artifact_manifest",
    "write_artifact_manifest",
    "write_run_output_file",
)

_HASH_BLOCK_SIZE = 1024 * 1024
_SCHEMA_VERSION = "1.0.0"
_TEMP_DIR_NAME = "_tmp"
_MANIFEST_FILENAME = "artifact-manifest.json"
_FORMAT_CHECKER = FormatChecker()
_UUID_V4_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ArtifactManifestError(OSError):
    """Base error for run artifact manifest operations."""


class ArtifactManifestValidationError(ArtifactManifestError, ValueError):
    """Raised when manifest inputs fail validation."""


class ArtifactManifestCommitError(ArtifactManifestError):
    """Raised when durable manifest commit fails after validation."""


@dataclass(frozen=True, slots=True)
class GeneratedRunFile:
    """One generated run output file prior to manifest commit."""

    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StoredArtifactManifest:
    """Durable run artifact manifest written under the configured storage root."""

    manifest_storage_key: str
    sha256: str
    size_bytes: int
    document: dict[str, Any]


def write_run_output_file(
    *,
    storage_root: Path,
    run_id: str,
    relative_path: str,
    payload: bytes,
) -> GeneratedRunFile:
    """Persist one generated run output file with fsync before returning metadata."""
    _validate_run_id(run_id)
    _validate_generated_path(relative_path)
    if not payload:
        raise ArtifactManifestValidationError("generated file payload must be nonempty")

    digest = hashlib.sha256(payload).hexdigest()
    resolved_root = storage_root.resolve()
    try:
        final_path = prepare_storage_file_path(
            resolved_root,
            "runs",
            run_id,
            *relative_path.split("/"),
        )
    except StoragePathError as exc:
        raise ArtifactManifestError("run output storage path is unsafe") from exc

    if final_path.exists():
        existing = final_path.read_bytes()
        if existing != payload:
            raise ArtifactManifestCommitError(
                "a different run output file already exists at the target path"
            )
        return GeneratedRunFile(path=relative_path, sha256=digest, size_bytes=len(payload))

    try:
        temp_dir = ensure_storage_directory(resolved_root, _TEMP_DIR_NAME)
        generated_temp_path = manifest_staging_path(temp_dir)
        temp_path = prepare_storage_file_path(
            resolved_root,
            _TEMP_DIR_NAME,
            generated_temp_path.name,
        )
    except StoragePathError as exc:
        raise ArtifactManifestCommitError("run output could not be durably committed") from exc

    try:
        try:
            with temp_path.open("xb") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, final_path)
            temp_path = None
            _fsync_directory(final_path.parent)
        except OSError as exc:
            raise ArtifactManifestCommitError("run output could not be durably committed") from exc

        return GeneratedRunFile(path=relative_path, sha256=digest, size_bytes=len(payload))
    finally:
        _cleanup_staging_path(resolved_root, temp_path)


def write_artifact_manifest(
    *,
    run_id: str,
    package_revision_id: str,
    authority_manifest_id: str,
    analysis_profile_sha256: str,
    config_fingerprint: str,
    prompt_bundle_sha256: str,
    completed_at: Any,
    generated_files: Iterable[GeneratedRunFile] | Sequence[GeneratedRunFile],
    storage_root: Path,
    schema_path: Path | None = None,
    project_root: Path | None = None,
) -> StoredArtifactManifest:
    """Validate generated files and persist ``artifact-manifest.json`` last."""
    validated_run_id = _validate_run_id(run_id)
    validated_revision_id = _validate_package_revision_id(package_revision_id)
    files = _order_generated_files(generated_files)
    document = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": validated_run_id,
        "package_revision_id": validated_revision_id,
        "authority_manifest_id": authority_manifest_id,
        "analysis_profile_sha256": _validate_sha256(analysis_profile_sha256),
        "config_fingerprint": _validate_sha256(config_fingerprint),
        "prompt_bundle_sha256": _validate_sha256(prompt_bundle_sha256),
        "completed_at": format_utc_datetime(completed_at),
        "files": [
            {
                "path": item.path,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in files
        ],
    }

    validator = _artifact_manifest_validator(
        schema_path=schema_path,
        project_root=project_root,
    )
    validation_error = next(validator.iter_errors(document), None)
    if validation_error is not None:
        raise ArtifactManifestValidationError(_format_schema_error(validation_error))

    manifest_bytes = _canonical_manifest_bytes(document)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_storage_key = f"runs/{validated_run_id}/{_MANIFEST_FILENAME}"
    resolved_root = storage_root.resolve()
    try:
        final_path = prepare_storage_file_path(
            resolved_root,
            "runs",
            validated_run_id,
            _MANIFEST_FILENAME,
        )
    except StoragePathError as exc:
        raise ArtifactManifestError("artifact manifest storage path is unsafe") from exc

    if final_path.exists():
        existing_bytes = final_path.read_bytes()
        if existing_bytes == manifest_bytes:
            return StoredArtifactManifest(
                manifest_storage_key=manifest_storage_key,
                sha256=manifest_digest,
                size_bytes=len(manifest_bytes),
                document=document,
            )
        raise ArtifactManifestCommitError(
            "a different artifact manifest already exists for this run"
        )

    try:
        temp_dir = ensure_storage_directory(resolved_root, _TEMP_DIR_NAME)
        generated_temp_path = manifest_staging_path(temp_dir)
        temp_path = prepare_storage_file_path(
            resolved_root,
            _TEMP_DIR_NAME,
            generated_temp_path.name,
        )
    except StoragePathError as exc:
        raise ArtifactManifestCommitError(
            "artifact manifest could not be durably committed"
        ) from exc

    try:
        try:
            with temp_path.open("xb") as temp_file:
                temp_file.write(manifest_bytes)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, final_path)
            temp_path = None
            _fsync_directory(final_path.parent)
        except OSError as exc:
            raise ArtifactManifestCommitError(
                "artifact manifest could not be durably committed"
            ) from exc

        return StoredArtifactManifest(
            manifest_storage_key=manifest_storage_key,
            sha256=manifest_digest,
            size_bytes=len(manifest_bytes),
            document=document,
        )
    finally:
        _cleanup_staging_path(resolved_root, temp_path)


def load_run_artifact_manifest(
    *,
    storage_root: Path,
    run_id: str,
    expected_sha256: str | None = None,
    schema_path: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Load and schema-validate one durable run artifact manifest from storage."""
    validated_run_id = _validate_run_id(run_id)
    resolved_root = storage_root.resolve()
    try:
        manifest_path = require_storage_regular_file(
            resolved_root,
            "runs",
            validated_run_id,
            _MANIFEST_FILENAME,
        )
    except (OSError, StoragePathError) as exc:
        raise ArtifactManifestError("artifact manifest is missing") from exc

    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise ArtifactManifestError("artifact manifest could not be read") from exc

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactManifestValidationError("artifact manifest is invalid") from exc

    if not isinstance(document, dict):
        raise ArtifactManifestValidationError("artifact manifest is invalid")

    validator = _artifact_manifest_validator(
        schema_path=schema_path,
        project_root=project_root,
    )
    validation_error = next(validator.iter_errors(document), None)
    if validation_error is not None:
        raise ArtifactManifestValidationError(_format_schema_error(validation_error))

    if document.get("run_id") != validated_run_id:
        raise ArtifactManifestValidationError("artifact manifest run_id mismatch")

    manifest_digest = hashlib.sha256(_canonical_manifest_bytes(document)).hexdigest()
    if expected_sha256 is not None and manifest_digest != expected_sha256:
        raise ArtifactManifestValidationError("artifact manifest digest mismatch")

    return document


def _order_generated_files(
    generated_files: Iterable[GeneratedRunFile] | Sequence[GeneratedRunFile],
) -> tuple[GeneratedRunFile, ...]:
    materialized = tuple(generated_files)
    paths = [item.path for item in materialized]
    if len(paths) != len(set(paths)):
        raise ArtifactManifestValidationError("duplicate generated file path")
    return tuple(sorted(materialized, key=lambda item: item.path))


@cache
def _artifact_manifest_validator(
    *,
    schema_path: Path | None,
    project_root: Path | None,
) -> Draft202012Validator:
    if schema_path is not None:
        resolved = schema_path.resolve()
    else:
        root = project_root or _default_project_root()
        resolved = (root / "docs/contracts/artifact-manifest.schema.json").resolve()
    schema = json.loads(resolved.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def _default_project_root() -> Path:
    candidate = Path(__file__).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise ArtifactManifestError("could not locate project root")


def _canonical_manifest_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _format_schema_error(error: ValidationError) -> str:
    return error.message


def _validate_run_id(value: str) -> str:
    normalized = value.strip().lower()
    if _UUID_V4_PATTERN.fullmatch(normalized) is None:
        raise ArtifactManifestValidationError("run_id must be a UUID v4")
    return normalized


def _validate_package_revision_id(value: str) -> str:
    normalized = value.strip().lower()
    if _UUID_V4_PATTERN.fullmatch(normalized) is None:
        raise ArtifactManifestValidationError("package_revision_id must be a UUID v4")
    return normalized


def _validate_sha256(value: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ArtifactManifestValidationError("sha256 must be lowercase hex")
    return value


def _validate_generated_path(value: str) -> str:
    if not value or value.startswith("/") or ".." in value.split("/"):
        raise ArtifactManifestValidationError("generated file path is unsafe")
    if not re.fullmatch(
        r"(?:human|machine|provenance|validation)/(?:[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,502}[A-Za-z0-9])?)",
        value,
    ):
        raise ArtifactManifestValidationError("generated file path is invalid")
    return value


def _fsync_directory(path: Path) -> None:
    if sys.platform == "win32":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _cleanup_staging_path(storage_root: Path, temp_path: Path | None) -> None:
    if temp_path is None:
        return
    try:
        safe_temp_path = require_storage_regular_file(
            storage_root,
            _TEMP_DIR_NAME,
            temp_path.name,
        )
        safe_temp_path.unlink()
    except (OSError, StoragePathError):
        return
