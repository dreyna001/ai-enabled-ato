"""Durable, schema-validated package content manifest writer."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from ato_service.storage_reconciliation import (
    StoragePathError,
    ensure_storage_directory,
    manifest_staging_path,
    prepare_storage_file_path,
    require_storage_regular_file,
)

_HASH_BLOCK_SIZE = 1024 * 1024
_SCHEMA_VERSION = "1.0.0"
_TEMP_DIR_NAME = "_tmp"
_MANIFEST_FILENAME = "content-manifest.json"

MAX_ARTIFACTS = 500
MAX_ARTIFACT_BYTES = 104_857_600
MAX_PACKAGE_BYTES = 2_147_483_648

_UUID_V4_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_STORAGE_KEY_PATTERN = re.compile(r"^[a-f0-9]{2}/[a-f0-9]{64}$")
_FORMAT_CHECKER = FormatChecker()


class ContentManifestError(OSError):
    """Base error for content manifest operations."""


class ContentManifestValidationError(ContentManifestError, ValueError):
    """Raised when manifest inputs fail validation."""


class ContentManifestConflictError(ContentManifestError):
    """Raised when a different manifest already exists for the revision."""


class ContentManifestBlobError(ContentManifestError):
    """Raised when a referenced blob is missing or does not match claims."""


class ContentManifestCommitError(ContentManifestError):
    """Raised when durable manifest commit fails after validation."""


@dataclass(frozen=True, slots=True)
class ManifestSourceEntry:
    """One immutable source artifact entry for a package content manifest."""

    artifact_id: str
    storage_key: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StoredContentManifest:
    """Durable package content manifest written under the configured storage root."""

    manifest_storage_key: str
    sha256: str
    size_bytes: int
    document: dict[str, Any]


def write_content_manifest(
    package_revision_id: str,
    source_entries: Iterable[ManifestSourceEntry] | Sequence[ManifestSourceEntry],
    *,
    storage_root: Path,
    schema_path: Path | None = None,
    project_root: Path | None = None,
    max_artifacts: int = MAX_ARTIFACTS,
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES,
    max_package_bytes: int = MAX_PACKAGE_BYTES,
    replace_unreferenced_existing: bool = False,
) -> StoredContentManifest:
    """Validate source blobs and persist an immutable package content manifest."""
    resolved_root = storage_root.resolve()
    validated_limits = _validate_manifest_limits(
        max_artifacts=max_artifacts,
        max_artifact_bytes=max_artifact_bytes,
        max_package_bytes=max_package_bytes,
    )
    entries = _validate_source_entries(
        source_entries,
        max_artifacts=validated_limits[0],
        max_artifact_bytes=validated_limits[1],
        max_package_bytes=validated_limits[2],
    )
    revision_id = _validate_package_revision_id(package_revision_id)
    _verify_source_blobs(entries, storage_root=resolved_root)

    document = _build_manifest_document(revision_id, entries)
    validator = _content_manifest_validator(
        schema_path=schema_path,
        project_root=project_root,
    )
    validation_error = next(validator.iter_errors(document), None)
    if validation_error is not None:
        raise ContentManifestValidationError(
            _format_schema_error(validation_error)
        ) from validation_error

    manifest_bytes = _canonical_manifest_bytes(document)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_storage_key = f"manifests/packages/{revision_id}/{_MANIFEST_FILENAME}"
    try:
        final_path = prepare_storage_file_path(
            resolved_root,
            "manifests",
            "packages",
            revision_id,
            _MANIFEST_FILENAME,
        )
    except StoragePathError as exc:
        raise ContentManifestError("content manifest storage path is unsafe") from exc

    if final_path.exists():
        existing_bytes = final_path.read_bytes()
        if existing_bytes == manifest_bytes:
            return _stored_manifest_from_bytes(
                manifest_storage_key=manifest_storage_key,
                manifest_bytes=existing_bytes,
                document=document,
            )
        if not replace_unreferenced_existing:
            raise ContentManifestConflictError(
                "a different content manifest already exists for this package revision"
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
        raise ContentManifestCommitError(
            "content manifest could not be durably committed"
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
            raise ContentManifestCommitError(
                "content manifest could not be durably committed"
            ) from exc

        return StoredContentManifest(
            manifest_storage_key=manifest_storage_key,
            sha256=manifest_digest,
            size_bytes=len(manifest_bytes),
            document=document,
        )
    finally:
        _cleanup_staging_path(resolved_root, temp_path)


def _validate_manifest_limits(
    *,
    max_artifacts: int,
    max_artifact_bytes: int,
    max_package_bytes: int,
) -> tuple[int, int, int]:
    validated_max_artifacts = _validate_positive_limit(
        "max_artifacts",
        max_artifacts,
    )
    if validated_max_artifacts > MAX_ARTIFACTS:
        raise ContentManifestValidationError(
            f"max_artifacts must not exceed {MAX_ARTIFACTS}"
        )

    validated_max_artifact_bytes = _validate_positive_limit(
        "max_artifact_bytes",
        max_artifact_bytes,
    )
    if validated_max_artifact_bytes > MAX_ARTIFACT_BYTES:
        raise ContentManifestValidationError(
            f"max_artifact_bytes must not exceed {MAX_ARTIFACT_BYTES}"
        )

    validated_max_package_bytes = _validate_positive_limit(
        "max_package_bytes",
        max_package_bytes,
    )
    return (
        validated_max_artifacts,
        validated_max_artifact_bytes,
        validated_max_package_bytes,
    )


def _validate_positive_limit(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContentManifestValidationError(f"{name} must be a positive integer")
    if value < 1:
        raise ContentManifestValidationError(f"{name} must be at least 1")
    return value


def _validate_package_revision_id(package_revision_id: str) -> str:
    if not isinstance(package_revision_id, str):
        raise ContentManifestValidationError("package_revision_id must be a string")
    revision_id = package_revision_id.strip()
    if not revision_id:
        raise ContentManifestValidationError("package_revision_id must not be empty")
    if revision_id != package_revision_id:
        raise ContentManifestValidationError(
            "package_revision_id must not contain leading or trailing whitespace"
        )
    if not _UUID_V4_PATTERN.fullmatch(revision_id):
        raise ContentManifestValidationError(
            "package_revision_id must be a UUID v4 value"
        )
    if "/" in revision_id or "\\" in revision_id or ".." in revision_id:
        raise ContentManifestValidationError(
            "package_revision_id must not contain path separators"
        )
    return revision_id


def _validate_source_entries(
    source_entries: Iterable[ManifestSourceEntry] | Sequence[ManifestSourceEntry],
    *,
    max_artifacts: int,
    max_artifact_bytes: int,
    max_package_bytes: int,
) -> tuple[ManifestSourceEntry, ...]:
    if isinstance(source_entries, ManifestSourceEntry):
        raise ContentManifestValidationError(
            "source_entries must be a non-empty sequence of manifest source entries"
        )

    try:
        source_iterator = iter(source_entries)
    except TypeError as exc:
        raise ContentManifestValidationError(
            "source_entries must be an iterable of manifest source entries"
        ) from exc

    entries = tuple(islice(source_iterator, max_artifacts + 1))
    if not entries:
        raise ContentManifestValidationError("source_entries must not be empty")
    if len(entries) > max_artifacts:
        raise ContentManifestValidationError(
            f"source_entries must not exceed {max_artifacts} artifacts"
        )

    seen_artifact_ids: set[str] = set()
    seen_storage_keys: set[str] = set()
    validated: list[ManifestSourceEntry] = []
    total_bytes = 0

    for index, entry in enumerate(entries):
        if not isinstance(entry, ManifestSourceEntry):
            raise ContentManifestValidationError(
                f"source_entries[{index}] must be a ManifestSourceEntry"
            )

        artifact_id = _validate_uuid_v4(entry.artifact_id, field_name="artifact_id")
        storage_key = _validate_storage_key(entry.storage_key, entry.sha256)
        sha256 = _validate_sha256(entry.sha256)
        size_bytes = _validate_size_bytes(entry.size_bytes, max_artifact_bytes)

        if artifact_id in seen_artifact_ids:
            raise ContentManifestValidationError(
                "duplicate artifact_id values are not allowed"
            )
        if storage_key in seen_storage_keys:
            raise ContentManifestValidationError(
                "duplicate storage_key values are not allowed"
            )

        total_bytes += size_bytes
        if total_bytes > max_package_bytes:
            raise ContentManifestValidationError(
                f"aggregate artifact size must not exceed {max_package_bytes} bytes"
            )

        seen_artifact_ids.add(artifact_id)
        seen_storage_keys.add(storage_key)
        validated.append(
            ManifestSourceEntry(
                artifact_id=artifact_id,
                storage_key=storage_key,
                sha256=sha256,
                size_bytes=size_bytes,
            )
        )

    return tuple(validated)


def _validate_uuid_v4(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContentManifestValidationError(f"{field_name} must be a string")
    if value != value.strip():
        raise ContentManifestValidationError(
            f"{field_name} must not contain leading or trailing whitespace"
        )
    if not _UUID_V4_PATTERN.fullmatch(value):
        raise ContentManifestValidationError(f"{field_name} must be a UUID v4 value")
    return value


def _validate_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise ContentManifestValidationError("sha256 must be a string")
    if value != value.strip():
        raise ContentManifestValidationError(
            "sha256 must not contain leading or trailing whitespace"
        )
    if not _SHA256_PATTERN.fullmatch(value):
        raise ContentManifestValidationError(
            "sha256 must be a 64-character lowercase hex digest"
        )
    return value


def _validate_storage_key(storage_key: str, sha256: str) -> str:
    if not isinstance(storage_key, str):
        raise ContentManifestValidationError("storage_key must be a string")
    if storage_key != storage_key.strip():
        raise ContentManifestValidationError(
            "storage_key must not contain leading or trailing whitespace"
        )
    if not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
        raise ContentManifestValidationError(
            "storage_key must match the pattern {two-hex}/{sha256}"
        )
    if ".." in storage_key or "\\" in storage_key:
        raise ContentManifestValidationError(
            "storage_key must not contain path traversal"
        )

    prefix, suffix = storage_key.split("/", 1)
    digest = _validate_sha256(sha256)
    if prefix != digest[:2]:
        raise ContentManifestValidationError(
            "storage_key prefix must match the first two sha256 characters"
        )
    if suffix != digest:
        raise ContentManifestValidationError("storage_key suffix must equal sha256")
    return storage_key


def _validate_size_bytes(size_bytes: object, max_artifact_bytes: int) -> int:
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        raise ContentManifestValidationError("size_bytes must be a positive integer")
    if size_bytes < 1:
        raise ContentManifestValidationError("size_bytes must be at least 1")
    if size_bytes > max_artifact_bytes:
        raise ContentManifestValidationError(
            f"size_bytes must not exceed {max_artifact_bytes}"
        )
    return size_bytes


def _verify_source_blobs(
    entries: tuple[ManifestSourceEntry, ...],
    *,
    storage_root: Path,
) -> None:
    for entry in entries:
        storage_key_parts = entry.storage_key.split("/")
        try:
            blob_path = require_storage_regular_file(
                storage_root,
                "blobs",
                *storage_key_parts,
            )
        except FileNotFoundError:
            raise ContentManifestBlobError(
                f"referenced blob does not exist for storage_key {entry.storage_key}"
            )
        except StoragePathError as exc:
            raise ContentManifestBlobError(
                "referenced blob storage path is unsafe"
            ) from exc

        actual_size = blob_path.stat().st_size
        if actual_size != entry.size_bytes:
            raise ContentManifestBlobError(
                "referenced blob size does not match declared size_bytes"
            )

        actual_digest = _hash_file(blob_path)
        if actual_digest != entry.sha256:
            raise ContentManifestBlobError(
                "referenced blob digest does not match declared sha256"
            )


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as blob_file:
        while True:
            chunk = blob_file.read(_HASH_BLOCK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _build_manifest_document(
    package_revision_id: str,
    entries: tuple[ManifestSourceEntry, ...],
) -> dict[str, Any]:
    artifacts = [
        {
            "artifact_id": entry.artifact_id,
            "storage_key": entry.storage_key,
            "sha256": entry.sha256,
            "size_bytes": entry.size_bytes,
        }
        for entry in sorted(entries, key=lambda item: item.artifact_id.casefold())
    ]
    return {
        "schema_version": _SCHEMA_VERSION,
        "package_revision_id": package_revision_id,
        "artifacts": artifacts,
    }


def _canonical_manifest_bytes(document: dict[str, Any]) -> bytes:
    """Return canonical manifest bytes for digesting and durable storage.

    Canonical form uses UTF-8, recursively sorted object keys, compact
    separators (`,` and `:` with no extra whitespace), and no trailing
    newline byte.
    """
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stored_manifest_from_bytes(
    *,
    manifest_storage_key: str,
    manifest_bytes: bytes,
    document: dict[str, Any],
) -> StoredContentManifest:
    return StoredContentManifest(
        manifest_storage_key=manifest_storage_key,
        sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        size_bytes=len(manifest_bytes),
        document=document,
    )


def _format_schema_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"generated manifest failed schema validation at {path}: {error.message}"
    return f"generated manifest failed schema validation: {error.message}"


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise ContentManifestError(
        "Could not locate project root (pyproject.toml not found)"
    )


@cache
def _load_schema(schema_path: Path) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _content_manifest_validator(
    *,
    schema_path: Path | None,
    project_root: Path | None,
) -> Draft202012Validator:
    resolved_schema_path = schema_path
    if resolved_schema_path is None:
        root = project_root or _find_project_root()
        resolved_schema_path = (
            root / "docs" / "contracts" / "content-manifest.schema.json"
        )

    try:
        schema = _load_schema(resolved_schema_path.resolve())
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise ContentManifestError(
            "content manifest schema is invalid or unreadable"
        ) from exc

    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def _cleanup_staging_path(storage_root: Path, temp_path: Path | None) -> None:
    if temp_path is None:
        return

    active_error = sys.exception()
    try:
        safe_temp_path = require_storage_regular_file(
            storage_root,
            _TEMP_DIR_NAME,
            temp_path.name,
        )
        safe_temp_path.unlink(missing_ok=True)
    except FileNotFoundError:
        return
    except OSError:
        if active_error is None:
            raise
        active_error.add_note("temporary manifest staging cleanup also failed")


def _fsync_directory(path: Path) -> None:
    """Persist a renamed directory entry on the Linux production target."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
