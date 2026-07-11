"""Focused tests for session_scope and stale staging reconciliation."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ato_service.db.session import session_scope
from ato_service.storage_reconciliation import (
    StoragePathError,
    blob_staging_path,
    cleanup_stale_staging_files,
    ensure_storage_directory,
    is_recognized_staging_filename,
    manifest_staging_path,
    prepare_storage_file_path,
)


async def _run_session_scope(session_factory, *, should_fail: bool = False) -> None:
    async with session_scope(session_factory):
        if should_fail:
            raise RuntimeError("boom")


def test_session_scope_commits_and_closes_on_success() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session_factory = MagicMock(return_value=session)

    asyncio.run(_run_session_scope(session_factory))

    session_factory.assert_called_once_with()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once_with()


def test_session_scope_rolls_back_reraises_and_closes_on_error() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session_factory = MagicMock(return_value=session)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_run_session_scope(session_factory, should_fail=True))

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
    session.close.assert_awaited_once_with()


def _touch(path: Path, *, timestamp: float) -> None:
    path.write_bytes(b"x")
    os.utime(path, (timestamp, timestamp))


def test_cleanup_deletes_only_stale_recognized_staging_files(tmp_path: Path) -> None:
    temp_dir = tmp_path / "_tmp"
    temp_dir.mkdir()
    now = time.time()
    stale_ts = now - 3600
    fresh_ts = now - 10

    stale_blob = blob_staging_path(temp_dir)
    stale_manifest = manifest_staging_path(temp_dir)
    fresh_blob = blob_staging_path(temp_dir)
    readiness_probe = temp_dir / "readiness-deadbeef"
    unknown_file = temp_dir / "mystery.tmp"
    staging_dir = blob_staging_path(temp_dir)

    _touch(stale_blob, timestamp=stale_ts)
    _touch(stale_manifest, timestamp=stale_ts)
    _touch(fresh_blob, timestamp=fresh_ts)
    _touch(readiness_probe, timestamp=stale_ts)
    _touch(unknown_file, timestamp=stale_ts)
    staging_dir.mkdir()

    deleted = cleanup_stale_staging_files(tmp_path, cutoff_timestamp=now - 60)

    assert deleted == 2
    assert not stale_blob.exists()
    assert not stale_manifest.exists()
    assert fresh_blob.is_file()
    assert readiness_probe.is_file()
    assert unknown_file.is_file()
    assert staging_dir.is_dir()


@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation requires elevated privilege on Windows"
)
def test_cleanup_preserves_symlink_and_outside_tmp(tmp_path: Path) -> None:
    temp_dir = tmp_path / "_tmp"
    temp_dir.mkdir()
    outside = tmp_path / "outside-target"
    outside.write_bytes(b"keep")
    stale_ts = time.time() - 3600

    link_name = blob_staging_path(temp_dir).name
    symlink_path = temp_dir / link_name
    symlink_path.symlink_to(outside)
    os.utime(symlink_path, (stale_ts, stale_ts), follow_symlinks=False)

    deleted = cleanup_stale_staging_files(tmp_path, cutoff_timestamp=time.time() - 60)

    assert deleted == 0
    assert symlink_path.is_symlink()
    assert outside.read_bytes() == b"keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_cleanup_preserves_symlink_to_staging_name(tmp_path: Path) -> None:
    temp_dir = tmp_path / "_tmp"
    temp_dir.mkdir()
    stale_ts = time.time() - 3600
    target = temp_dir / "real-staging-target"
    _touch(target, timestamp=stale_ts)

    link_path = blob_staging_path(temp_dir)
    if link_path.exists():
        link_path.unlink()
    link_path.symlink_to(target.name)
    os.utime(link_path, (stale_ts, stale_ts), follow_symlinks=False)

    deleted = cleanup_stale_staging_files(tmp_path, cutoff_timestamp=time.time() - 60)

    assert deleted == 0
    assert target.is_file()


def test_recognized_staging_filename_distinguishes_blob_and_manifest() -> None:
    temp_dir = Path("/tmp/example")
    blob_name = blob_staging_path(temp_dir).name
    manifest_name = manifest_staging_path(temp_dir).name

    assert is_recognized_staging_filename(blob_name)
    assert is_recognized_staging_filename(manifest_name)
    assert blob_name.startswith("blob-staging-")
    assert manifest_name.startswith("manifest-staging-")
    assert not is_recognized_staging_filename("readiness-deadbeef")
    assert not is_recognized_staging_filename("blob-staging-nothex")


@pytest.mark.parametrize("failure_stage", ["stat", "unlink"])
def test_cleanup_surfaces_recognized_staging_file_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    temp_dir = tmp_path / "_tmp"
    temp_dir.mkdir()
    stale_blob = blob_staging_path(temp_dir)
    _touch(stale_blob, timestamp=time.time() - 3600)

    if failure_stage == "stat":
        original_stat = Path.stat

        def _fail_stat(self, *, follow_symlinks=True):
            if self == stale_blob and follow_symlinks is False:
                raise OSError("simulated staging stat failure")
            return original_stat(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "stat", _fail_stat)
    else:
        original_unlink = Path.unlink

        def _fail_unlink(self, *args, **kwargs):
            if self == stale_blob:
                raise OSError("simulated staging unlink failure")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", _fail_unlink)

    with pytest.raises(OSError, match=f"simulated staging {failure_stage} failure"):
        cleanup_stale_staging_files(
            tmp_path,
            cutoff_timestamp=time.time() - 60,
        )

    assert stale_blob.exists()


def test_cleanup_tolerates_concurrent_prior_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "_tmp"
    temp_dir.mkdir()
    stale_blob = blob_staging_path(temp_dir)
    _touch(stale_blob, timestamp=time.time() - 3600)
    original_unlink = Path.unlink

    def _concurrent_unlink(self, *args, **kwargs):
        if self == stale_blob:
            original_unlink(self)
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _concurrent_unlink)

    deleted = cleanup_stale_staging_files(
        tmp_path,
        cutoff_timestamp=time.time() - 60,
    )

    assert deleted == 0
    assert not stale_blob.exists()


def test_storage_directory_helper_rejects_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    temp_dir = storage_root / "_tmp"
    temp_dir.mkdir(parents=True)
    original_is_junction = Path.is_junction

    def _is_junction(self):
        if self == temp_dir:
            return True
        return original_is_junction(self)

    monkeypatch.setattr(Path, "is_junction", _is_junction)

    with pytest.raises(StoragePathError, match="junction"):
        ensure_storage_directory(storage_root, "_tmp")


@pytest.mark.parametrize("part", ["", ".", "..", "../outside", r"..\outside"])
def test_storage_path_helpers_reject_invalid_parts(
    tmp_path: Path,
    part: str,
) -> None:
    with pytest.raises(StoragePathError, match="path part is invalid"):
        prepare_storage_file_path(tmp_path, part, "file")


def test_cleanup_is_idempotent(tmp_path: Path) -> None:
    temp_dir = tmp_path / "_tmp"
    temp_dir.mkdir()
    stale_ts = time.time() - 3600
    stale_blob = blob_staging_path(temp_dir)
    _touch(stale_blob, timestamp=stale_ts)
    cutoff = time.time() - 60

    assert cleanup_stale_staging_files(tmp_path, cutoff_timestamp=cutoff) == 1
    assert cleanup_stale_staging_files(tmp_path, cutoff_timestamp=cutoff) == 0
