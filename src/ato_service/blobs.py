"""Content-addressed blob storage with durable write ordering."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
from typing import BinaryIO

from ato_service.storage_reconciliation import (
    blob_staging_path,
    ensure_storage_directory,
    prepare_storage_file_path,
    require_storage_regular_file,
)

_HASH_BLOCK_SIZE = 1024 * 1024
_TEMP_DIR_NAME = "_tmp"


class BlobStoreError(OSError):
    """Base error for blob storage operations."""


class EmptyBlobError(BlobStoreError, ValueError):
    """Raised when source input contains no bytes."""


class BlobTooLargeError(BlobStoreError, ValueError):
    """Raised when source input exceeds the configured maximum size."""


@dataclass(frozen=True, slots=True)
class StoredBlob:
    storage_key: str
    sha256: str
    size_bytes: int


class BlobStore:
    """Write source bytes to content-addressed storage under a configured root."""

    def __init__(self, storage_root: Path) -> None:
        self._storage_root = storage_root.resolve()

    @property
    def storage_root(self) -> Path:
        return self._storage_root

    def store_stream(
        self,
        source: BinaryIO | Iterable[bytes],
        *,
        max_bytes: int,
    ) -> StoredBlob:
        """Persist bytes using Section 20.1 durable write ordering."""
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise ValueError("max_bytes must be a positive integer")
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")

        temp_dir = ensure_storage_directory(self._storage_root, _TEMP_DIR_NAME)
        generated_temp_path = blob_staging_path(temp_dir)
        temp_path = prepare_storage_file_path(
            self._storage_root,
            _TEMP_DIR_NAME,
            generated_temp_path.name,
        )
        hasher = hashlib.sha256()
        total_bytes = 0

        try:
            with temp_path.open("xb") as temp_file:
                for chunk in _iter_source_chunks(source):
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise BlobTooLargeError(
                            f"blob exceeds configured maximum of {max_bytes} bytes"
                        )
                    hasher.update(chunk)
                    temp_file.write(chunk)

                if total_bytes == 0:
                    raise EmptyBlobError("blob input must not be empty")

                temp_file.flush()
                os.fsync(temp_file.fileno())

            digest = hasher.hexdigest()
            storage_key = f"{digest[:2]}/{digest}"
            final_path = prepare_storage_file_path(
                self._storage_root,
                "blobs",
                digest[:2],
                digest,
            )

            if final_path.exists():
                existing_digest = _hash_file(final_path)
                if existing_digest != digest:
                    raise BlobStoreError(
                        "existing blob digest does not match content digest"
                    )
                existing_size = final_path.stat().st_size
                return StoredBlob(
                    storage_key=storage_key,
                    sha256=digest,
                    size_bytes=existing_size,
                )

            os.replace(temp_path, final_path)
            temp_path = None
            _fsync_directory(final_path.parent)

            return StoredBlob(
                storage_key=storage_key,
                sha256=digest,
                size_bytes=total_bytes,
            )
        finally:
            _cleanup_staging_path(self._storage_root, temp_path)


def _iter_source_chunks(source: BinaryIO | Iterable[bytes]) -> Iterable[bytes]:
    if hasattr(source, "read"):
        while True:
            chunk = source.read(_HASH_BLOCK_SIZE)
            if not isinstance(chunk, bytes):
                raise TypeError("stream read() must return bytes")
            if chunk == b"":
                break
            yield chunk
        return

    for chunk in source:
        if not isinstance(chunk, bytes):
            raise TypeError("byte iterables must yield bytes")
        if chunk == b"":
            raise ValueError("byte iterables must not yield empty chunks")
        yield chunk


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as blob_file:
        while True:
            chunk = blob_file.read(_HASH_BLOCK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


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
        active_error.add_note("temporary blob staging cleanup also failed")


def _fsync_directory(path: Path) -> None:
    """Persist a renamed directory entry on the Linux production target."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
