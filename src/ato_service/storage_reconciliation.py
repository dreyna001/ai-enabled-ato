"""Safe storage paths and stale staging cleanup under storage ``_tmp``."""

from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path

_TEMP_DIR_NAME = "_tmp"
_BLOB_STAGING_PREFIX = "blob-staging-"
_MANIFEST_STAGING_PREFIX = "manifest-staging-"
_STAGING_TOKEN_BYTES = 16
_RECOGNIZED_STAGING_PATTERN = re.compile(
    rf"^(?:blob-staging-|manifest-staging-)[0-9a-f]{{{_STAGING_TOKEN_BYTES * 2}}}$"
)


class StoragePathError(OSError):
    """Raised when an owned storage path is unsafe or has an invalid type."""


def ensure_storage_directory(storage_root: Path, *parts: str) -> Path:
    """Create and validate a non-linked directory below the resolved root."""
    root = _resolve_storage_root(storage_root)
    _create_and_validate_directory(root, storage_root=root)

    current = root
    for part in _validate_storage_parts(parts):
        current = current / part
        _create_and_validate_directory(current, storage_root=root)
    return current


def require_storage_directory(storage_root: Path, *parts: str) -> Path:
    """Return an existing non-linked directory below the resolved root."""
    root = _resolve_storage_root(storage_root)
    _validate_directory(root, storage_root=root)

    current = root
    for part in _validate_storage_parts(parts):
        current = current / part
        _validate_directory(current, storage_root=root)
    return current


def prepare_storage_file_path(storage_root: Path, *parts: str) -> Path:
    """Create safe parents and return a missing or regular non-linked file path."""
    validated_parts = _validate_storage_parts(parts)
    if not validated_parts:
        raise StoragePathError("storage file path must contain a filename")

    parent = ensure_storage_directory(storage_root, *validated_parts[:-1])
    candidate = parent / validated_parts[-1]
    _validate_regular_file(
        candidate, storage_root=_resolve_storage_root(storage_root), allow_missing=True
    )
    return candidate


def require_storage_regular_file(storage_root: Path, *parts: str) -> Path:
    """Return an existing regular non-linked file below the resolved root."""
    validated_parts = _validate_storage_parts(parts)
    if not validated_parts:
        raise StoragePathError("storage file path must contain a filename")

    parent = require_storage_directory(storage_root, *validated_parts[:-1])
    candidate = parent / validated_parts[-1]
    _validate_regular_file(
        candidate,
        storage_root=_resolve_storage_root(storage_root),
        allow_missing=False,
    )
    return candidate


def blob_staging_path(temp_dir: Path) -> Path:
    """Return a distinguishable blob write staging path under ``temp_dir``."""
    return _generated_staging_path(temp_dir, prefix=_BLOB_STAGING_PREFIX)


def manifest_staging_path(temp_dir: Path) -> Path:
    """Return a distinguishable manifest write staging path under ``temp_dir``."""
    return _generated_staging_path(temp_dir, prefix=_MANIFEST_STAGING_PREFIX)


def is_recognized_staging_filename(name: str) -> bool:
    """Return whether ``name`` matches a process-owned blob or manifest staging file."""
    return _RECOGNIZED_STAGING_PATTERN.fullmatch(name) is not None


def cleanup_stale_staging_files(
    storage_root: Path,
    *,
    cutoff_timestamp: float,
) -> int:
    """Delete recognized regular staging files older than the explicit cutoff."""
    if cutoff_timestamp < 0:
        raise ValueError("cutoff_timestamp must not be negative")

    try:
        temp_dir = require_storage_directory(storage_root, _TEMP_DIR_NAME)
    except FileNotFoundError:
        return 0

    deleted = 0
    for entry in temp_dir.iterdir():
        if not is_recognized_staging_filename(entry.name):
            continue
        entry_stat = entry.stat(follow_symlinks=False)
        if not stat.S_ISREG(entry_stat.st_mode):
            continue
        if entry_stat.st_mtime >= cutoff_timestamp:
            continue

        try:
            entry.unlink()
        except FileNotFoundError:
            continue
        deleted += 1

    return deleted


def _resolve_storage_root(storage_root: Path) -> Path:
    try:
        return storage_root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise StoragePathError("storage root could not be resolved") from exc


def _validate_storage_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_validate_storage_part(part) for part in parts)


def _validate_storage_part(part: str) -> str:
    if (
        not isinstance(part, str)
        or not part
        or part in {".", ".."}
        or "/" in part
        or "\\" in part
        or "\x00" in part
        or Path(part).is_absolute()
        or Path(part).name != part
    ):
        raise StoragePathError("storage path part is invalid")
    return part


def _generated_staging_path(temp_dir: Path, *, prefix: str) -> Path:
    name = f"{prefix}{secrets.token_hex(_STAGING_TOKEN_BYTES)}"
    validated_name = _validate_storage_part(name)
    candidate = temp_dir / validated_name
    if candidate.parent != temp_dir:
        raise StoragePathError("generated staging path is invalid")
    return candidate


def _create_and_validate_directory(path: Path, *, storage_root: Path) -> None:
    try:
        path.mkdir(parents=path == storage_root, exist_ok=True)
    except OSError as exc:
        raise StoragePathError("storage directory could not be created") from exc
    _validate_directory(path, storage_root=storage_root)


def _validate_directory(path: Path, *, storage_root: Path) -> None:
    metadata = _lstat(path)
    _reject_link_or_junction(path, metadata)
    if not stat.S_ISDIR(metadata.st_mode):
        raise StoragePathError("storage path must be a directory")
    _validate_resolved_containment(path, storage_root=storage_root)


def _validate_regular_file(
    path: Path,
    *,
    storage_root: Path,
    allow_missing: bool,
) -> None:
    try:
        metadata = _lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return
        raise

    _reject_link_or_junction(path, metadata)
    if not stat.S_ISREG(metadata.st_mode):
        raise StoragePathError("storage path must be a regular file")
    _validate_resolved_containment(path, storage_root=storage_root)


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.stat(follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StoragePathError("storage path metadata could not be read") from exc


def _reject_link_or_junction(path: Path, metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise StoragePathError("storage path must not be a symbolic link")

    is_junction = getattr(path, "is_junction", None)
    if is_junction is None:
        return
    try:
        junction = is_junction()
    except OSError as exc:
        raise StoragePathError("storage junction status could not be read") from exc
    if junction:
        raise StoragePathError("storage path must not be a junction")


def _validate_resolved_containment(path: Path, *, storage_root: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise StoragePathError("storage path could not be resolved") from exc
    if resolved != storage_root and not resolved.is_relative_to(storage_root):
        raise StoragePathError("storage path resolves outside storage root")
