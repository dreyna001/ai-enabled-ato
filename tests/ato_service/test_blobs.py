"""Tests for content-addressed blob storage."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pytest

from ato_service.blobs import (
    BlobStore,
    BlobStoreError,
    BlobTooLargeError,
    EmptyBlobError,
)
from ato_service.storage_reconciliation import (
    StoragePathError,
    is_recognized_staging_filename,
)


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic links are unavailable: {type(exc).__name__}")


def test_store_stream_writes_digest_addressed_blob(tmp_path: Path) -> None:
    payload = b"package-source-bytes"
    store = BlobStore(tmp_path)

    stored = store.store_stream(io.BytesIO(payload), max_bytes=1024)

    digest = hashlib.sha256(payload).hexdigest()
    assert stored.sha256 == digest
    assert stored.size_bytes == len(payload)
    assert stored.storage_key == f"{digest[:2]}/{digest}"

    final_path = tmp_path / "blobs" / stored.storage_key
    assert final_path.is_file()
    assert final_path.read_bytes() == payload


def test_store_stream_is_idempotent_for_existing_blob(tmp_path: Path) -> None:
    payload = b"repeatable-content"
    store = BlobStore(tmp_path)
    first = store.store_stream(io.BytesIO(payload), max_bytes=1024)
    final_path = tmp_path / "blobs" / first.storage_key
    before_mtime_ns = final_path.stat().st_mtime_ns

    second = store.store_stream(io.BytesIO(payload), max_bytes=1024)

    assert second == first
    assert final_path.stat().st_mtime_ns == before_mtime_ns
    assert not any((tmp_path / "_tmp").glob("*"))


def test_rejects_empty_input(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)

    with pytest.raises(EmptyBlobError, match="must not be empty"):
        store.store_stream(io.BytesIO(b""), max_bytes=1024)


def test_rejects_over_limit_input(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)

    with pytest.raises(BlobTooLargeError, match="maximum"):
        store.store_stream(io.BytesIO(b"0123456789"), max_bytes=5)


@pytest.mark.parametrize("max_bytes", [True, False, 1.5, "1", None])
def test_rejects_non_integer_max_bytes(tmp_path: Path, max_bytes: object) -> None:
    store = BlobStore(tmp_path)

    with pytest.raises(ValueError, match="max_bytes must be a positive integer"):
        store.store_stream(io.BytesIO(b"x"), max_bytes=max_bytes)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_rejects_non_positive_max_bytes(tmp_path: Path, max_bytes: int) -> None:
    store = BlobStore(tmp_path)

    with pytest.raises(ValueError, match="max_bytes must be at least 1"):
        store.store_stream(io.BytesIO(b"x"), max_bytes=max_bytes)


def test_cleans_temp_file_after_stream_failure(tmp_path: Path) -> None:
    class FailingStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            chunk = super().read(size)
            if chunk:
                raise OSError("simulated stream failure")
            return chunk

    store = BlobStore(tmp_path)
    temp_dir = tmp_path / "_tmp"

    with pytest.raises(OSError, match="simulated stream failure"):
        store.store_stream(FailingStream(b"partial"), max_bytes=1024)

    assert not any(temp_dir.glob("*"))


def test_accepts_byte_iterable(tmp_path: Path) -> None:
    payload = b"chunked-input"
    store = BlobStore(tmp_path)

    stored = store.store_stream([payload[:5], payload[5:]], max_bytes=1024)

    assert stored.size_bytes == len(payload)
    assert (tmp_path / "blobs" / stored.storage_key).read_bytes() == payload


def test_rejects_empty_iterable_chunk_without_consuming_next(
    tmp_path: Path,
) -> None:
    class EmptyThenFail:
        def __init__(self) -> None:
            self.consumed = 0

        def __iter__(self):
            return self

        def __next__(self) -> bytes:
            self.consumed += 1
            if self.consumed == 1:
                return b""
            raise AssertionError("iterator consumed after empty chunk")

    source = EmptyThenFail()
    store = BlobStore(tmp_path)

    with pytest.raises(ValueError, match="must not yield empty chunks"):
        store.store_stream(source, max_bytes=1024)

    assert source.consumed == 1
    assert not any((tmp_path / "_tmp").iterdir())


def test_stream_empty_bytes_remains_eof(tmp_path: Path) -> None:
    class EofThenFail:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, size: int = -1) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"stream-payload"
            if self.calls == 2:
                return b""
            raise AssertionError("stream read after EOF")

    source = EofThenFail()
    store = BlobStore(tmp_path)

    stored = store.store_stream(source, max_bytes=1024)

    assert stored.size_bytes == len(b"stream-payload")
    assert source.calls == 2


def test_replay_rehashes_existing_blob_and_preserves_bytes(tmp_path: Path) -> None:
    payload = b"rehash-on-replay"
    store = BlobStore(tmp_path)
    first = store.store_stream(io.BytesIO(payload), max_bytes=1024)
    final_path = tmp_path / "blobs" / first.storage_key
    before_bytes = final_path.read_bytes()
    before_mtime_ns = final_path.stat().st_mtime_ns

    second = store.store_stream(io.BytesIO(payload), max_bytes=1024)

    assert second == first
    assert final_path.read_bytes() == before_bytes
    assert final_path.stat().st_mtime_ns == before_mtime_ns
    assert not any((tmp_path / "_tmp").glob("*"))


def test_rejects_same_size_corrupted_existing_blob(tmp_path: Path) -> None:
    payload = b"same-length-content"
    store = BlobStore(tmp_path)
    stored = store.store_stream(io.BytesIO(payload), max_bytes=1024)
    final_path = tmp_path / "blobs" / stored.storage_key
    before_bytes = final_path.read_bytes()
    corrupted = b"same-length-corrupt"
    assert len(corrupted) == len(before_bytes)
    final_path.write_bytes(corrupted)

    with pytest.raises(BlobStoreError, match="digest does not match"):
        store.store_stream(io.BytesIO(payload), max_bytes=1024)

    assert final_path.read_bytes() == corrupted
    assert not any((tmp_path / "_tmp").glob("*"))


def test_cleans_temp_file_after_write_failure(tmp_path: Path, monkeypatch) -> None:
    store = BlobStore(tmp_path)
    temp_dir = tmp_path / "_tmp"

    def _fail_write(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr("ato_service.blobs.Path.open", _fail_write)

    with pytest.raises(OSError, match="simulated write failure"):
        store.store_stream(io.BytesIO(b"partial-write"), max_bytes=1024)

    assert not any(temp_dir.glob("*"))


def test_cleans_temp_file_after_replace_failure(tmp_path: Path, monkeypatch) -> None:
    store = BlobStore(tmp_path)
    temp_dir = tmp_path / "_tmp"

    def _fail_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("ato_service.blobs.os.replace", _fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        store.store_stream(io.BytesIO(b"replace-failure"), max_bytes=1024)

    assert not any(
        path for path in temp_dir.glob("*") if is_recognized_staging_filename(path.name)
    )
    blob_files = (
        list((tmp_path / "blobs").rglob("*")) if (tmp_path / "blobs").exists() else []
    )
    assert all(path.is_dir() for path in blob_files)


def test_uses_recognized_blob_staging_filename(tmp_path: Path, monkeypatch) -> None:
    captured: list[str] = []

    original_open = Path.open

    def _capture_open(self, *args, **kwargs):
        if self.parent.name == "_tmp":
            captured.append(self.name)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _capture_open)

    store = BlobStore(tmp_path)
    store.store_stream(io.BytesIO(b"staging-name-check"), max_bytes=1024)

    assert len(captured) == 1
    assert is_recognized_staging_filename(captured[0])
    assert captured[0].startswith("blob-staging-")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symbolic links")
@pytest.mark.parametrize("redirected_path", ["_tmp", "blobs", "shard", "final"])
def test_rejects_symlinked_owned_storage_subtrees(
    tmp_path: Path,
    redirected_path: str,
) -> None:
    storage_root = tmp_path / "storage"
    outside = tmp_path / "outside"
    storage_root.mkdir()
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"outside-evidence")

    payload = b"symlink-boundary"
    digest = hashlib.sha256(payload).hexdigest()
    if redirected_path == "_tmp":
        _symlink_or_skip(
            storage_root / "_tmp",
            outside,
            target_is_directory=True,
        )
    elif redirected_path == "blobs":
        _symlink_or_skip(
            storage_root / "blobs",
            outside,
            target_is_directory=True,
        )
    elif redirected_path == "shard":
        (storage_root / "blobs").mkdir()
        _symlink_or_skip(
            storage_root / "blobs" / digest[:2],
            outside,
            target_is_directory=True,
        )
    else:
        shard = storage_root / "blobs" / digest[:2]
        shard.mkdir(parents=True)
        _symlink_or_skip(shard / digest, marker)

    store = BlobStore(storage_root)
    with pytest.raises(StoragePathError, match="symbolic link"):
        store.store_stream(io.BytesIO(payload), max_bytes=1024)

    assert marker.read_bytes() == b"outside-evidence"
    assert set(outside.iterdir()) == {marker}
