"""Revision-scoped protected artifact storage for normalization model steps."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from typing import Literal

from ato_service.storage_reconciliation import (
    StoragePathError,
    ensure_storage_directory,
    manifest_staging_path,
    prepare_storage_file_path,
    require_storage_regular_file,
)

_HASH_BLOCK_SIZE = 1024 * 1024
_TEMP_DIR_NAME = "_tmp"
_UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ARTIFACT_KINDS = frozenset({"prompt", "fact_bundle", "response"})
_ARTIFACT_FILENAMES = {
    "prompt": "prompt.json",
    "fact_bundle": "fact-bundle.json",
    "response": "response.json",
}
NormalizationArtifactKind = Literal["prompt", "fact_bundle", "response"]


class NormalizationArtifactError(OSError):
    """Base error for normalization protected artifact operations."""


class NormalizationArtifactValidationError(NormalizationArtifactError, ValueError):
    """Raised when normalization artifact inputs fail validation."""


class NormalizationArtifactCommitError(NormalizationArtifactError):
    """Raised when durable normalization artifact commit fails."""


@dataclass(frozen=True, slots=True)
class StoredNormalizationArtifact:
    """One protected normalization artifact written under the storage root."""

    storage_key: str
    sha256: str
    size_bytes: int


def write_normalization_protected_artifact(
    *,
    storage_root: Path,
    package_revision_id: str,
    step_id: str,
    artifact_kind: NormalizationArtifactKind,
    payload: bytes,
    max_bytes: int,
) -> StoredNormalizationArtifact:
    """Persist one protected normalization artifact with atomic create-new semantics."""
    validated_revision_id = _validate_uuid(package_revision_id, field_name="package_revision_id")
    validated_step_id = _validate_uuid(step_id, field_name="step_id")
    _validate_artifact_kind(artifact_kind)
    _validate_payload(payload, max_bytes=max_bytes)

    digest = hashlib.sha256(payload).hexdigest()
    storage_key = _storage_key(
        package_revision_id=validated_revision_id,
        step_id=validated_step_id,
        artifact_kind=artifact_kind,
    )
    resolved_root = storage_root.resolve()
    filename = _ARTIFACT_FILENAMES[artifact_kind]
    try:
        final_path = prepare_storage_file_path(
            resolved_root,
            "revisions",
            validated_revision_id,
            "normalization",
            validated_step_id,
            filename,
        )
    except StoragePathError as exc:
        raise NormalizationArtifactError("normalization artifact storage path is unsafe") from exc

    try:
        temp_dir = ensure_storage_directory(resolved_root, _TEMP_DIR_NAME)
        generated_temp_path = manifest_staging_path(temp_dir)
        temp_path = prepare_storage_file_path(
            resolved_root,
            _TEMP_DIR_NAME,
            generated_temp_path.name,
        )
    except StoragePathError as exc:
        raise NormalizationArtifactCommitError(
            "normalization artifact could not be durably committed"
        ) from exc

    try:
        try:
            with temp_path.open("xb") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            _commit_staged_file(
                storage_root=resolved_root,
                staging_path=temp_path,
                final_path=final_path,
                payload=payload,
                max_bytes=max_bytes,
            )
            temp_path = None
        except NormalizationArtifactCommitError:
            raise
        except OSError as exc:
            raise NormalizationArtifactCommitError(
                "normalization artifact could not be durably committed"
            ) from exc

        return StoredNormalizationArtifact(
            storage_key=storage_key,
            sha256=digest,
            size_bytes=len(payload),
        )
    finally:
        _cleanup_staging_path(resolved_root, temp_path)


def _commit_staged_file(
    *,
    storage_root: Path,
    staging_path: Path,
    final_path: Path,
    payload: bytes,
    max_bytes: int,
) -> None:
    try:
        os.link(staging_path, final_path)
    except FileExistsError:
        existing = _read_existing_payload_bounded(
            storage_root=storage_root,
            final_path=final_path,
            max_bytes=max_bytes,
        )
        if existing != payload:
            raise NormalizationArtifactCommitError(
                "a different normalization artifact already exists at the target path"
            )
    staging_path.unlink()
    _fsync_directory(final_path.parent)


def _read_existing_payload_bounded(
    *,
    storage_root: Path,
    final_path: Path,
    max_bytes: int,
) -> bytes:
    relative_parts = final_path.relative_to(storage_root.resolve()).parts
    safe_path = require_storage_regular_file(storage_root, *relative_parts)
    metadata = safe_path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise NormalizationArtifactCommitError(
            "existing normalization artifact path is not a regular file"
        )
    if metadata.st_size > max_bytes:
        raise NormalizationArtifactCommitError(
            "existing normalization artifact exceeds configured maximum size"
        )

    with safe_path.open("rb") as existing_file:
        payload = existing_file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise NormalizationArtifactCommitError(
            "existing normalization artifact exceeds configured maximum size"
        )
    return payload


def _storage_key(
    *,
    package_revision_id: str,
    step_id: str,
    artifact_kind: NormalizationArtifactKind,
) -> str:
    filename = _ARTIFACT_FILENAMES[artifact_kind]
    return (
        f"revisions/{package_revision_id}/normalization/{step_id}/{filename}"
    )


def _validate_uuid(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise NormalizationArtifactValidationError(f"{field_name} must be a string")
    normalized = value.strip().lower()
    if not _UUID_V4_PATTERN.fullmatch(normalized):
        raise NormalizationArtifactValidationError(
            f"{field_name} must be a lowercase UUID v4 string"
        )
    return normalized


def _validate_artifact_kind(artifact_kind: str) -> None:
    if artifact_kind not in _ARTIFACT_KINDS:
        raise NormalizationArtifactValidationError(
            "artifact_kind must be one of prompt, fact_bundle, or response"
        )


def _validate_payload(payload: bytes, *, max_bytes: int) -> None:
    if not isinstance(payload, (bytes, bytearray)):
        raise NormalizationArtifactValidationError("payload must be bytes")
    if not payload:
        raise NormalizationArtifactValidationError("payload must not be empty")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise NormalizationArtifactValidationError("max_bytes must be a positive integer")
    if len(payload) > max_bytes:
        raise NormalizationArtifactValidationError(
            f"payload exceeds configured maximum of {max_bytes} bytes"
        )


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
        active_error.add_note("normalization artifact staging cleanup also failed")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
