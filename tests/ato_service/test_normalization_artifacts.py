"""Focused tests for revision-scoped normalization protected artifact storage."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from ato_service.normalization_artifacts import (
    NormalizationArtifactCommitError,
    NormalizationArtifactValidationError,
    write_normalization_protected_artifact,
)
from ato_service.storage_reconciliation import require_storage_regular_file

REVISION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
STEP_ID = "a1b2c3d4-e5f6-4789-a012-3456789abcde"


def test_writes_prompt_artifact_with_storage_key_and_sha256(tmp_path: Path) -> None:
    payload = b'{"prompt":"normalize"}'

    stored = write_normalization_protected_artifact(
        storage_root=tmp_path,
        package_revision_id=REVISION_ID,
        step_id=STEP_ID,
        artifact_kind="prompt",
        payload=payload,
        max_bytes=1024,
    )

    expected_key = (
        f"revisions/{REVISION_ID}/normalization/{STEP_ID}/prompt.json"
    )
    assert stored.storage_key == expected_key
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.size_bytes == len(payload)
    artifact_path = require_storage_regular_file(
        tmp_path,
        "revisions",
        REVISION_ID,
        "normalization",
        STEP_ID,
        "prompt.json",
    )
    assert artifact_path.read_bytes() == payload


def test_rejects_empty_payload(tmp_path: Path) -> None:
    with pytest.raises(NormalizationArtifactValidationError, match="must not be empty"):
        write_normalization_protected_artifact(
            storage_root=tmp_path,
            package_revision_id=REVISION_ID,
            step_id=STEP_ID,
            artifact_kind="fact_bundle",
            payload=b"",
            max_bytes=1024,
        )


def test_rejects_oversized_payload(tmp_path: Path) -> None:
    with pytest.raises(NormalizationArtifactValidationError, match="exceeds configured maximum"):
        write_normalization_protected_artifact(
            storage_root=tmp_path,
            package_revision_id=REVISION_ID,
            step_id=STEP_ID,
            artifact_kind="response",
            payload=b"x" * 8,
            max_bytes=4,
        )


def test_rejects_path_traversal_revision_id(tmp_path: Path) -> None:
    with pytest.raises(NormalizationArtifactValidationError, match="package_revision_id"):
        write_normalization_protected_artifact(
            storage_root=tmp_path,
            package_revision_id="../escape",
            step_id=STEP_ID,
            artifact_kind="prompt",
            payload=b"{}",
            max_bytes=1024,
        )


def test_rejects_different_payload_when_target_already_exists(tmp_path: Path) -> None:
    write_normalization_protected_artifact(
        storage_root=tmp_path,
        package_revision_id=REVISION_ID,
        step_id=STEP_ID,
        artifact_kind="prompt",
        payload=b"first",
        max_bytes=1024,
    )

    with pytest.raises(NormalizationArtifactCommitError, match="already exists"):
        write_normalization_protected_artifact(
            storage_root=tmp_path,
            package_revision_id=REVISION_ID,
            step_id=STEP_ID,
            artifact_kind="prompt",
            payload=b"second",
            max_bytes=1024,
        )


def test_idempotent_replay_returns_same_metadata(tmp_path: Path) -> None:
    payload = b'{"bundle":true}'

    first = write_normalization_protected_artifact(
        storage_root=tmp_path,
        package_revision_id=REVISION_ID,
        step_id=STEP_ID,
        artifact_kind="fact_bundle",
        payload=payload,
        max_bytes=1024,
    )
    second = write_normalization_protected_artifact(
        storage_root=tmp_path,
        package_revision_id=REVISION_ID,
        step_id=STEP_ID,
        artifact_kind="fact_bundle",
        payload=payload,
        max_bytes=1024,
    )

    assert first == second


def test_rejects_oversized_existing_file(tmp_path: Path) -> None:
    final_path = (
        tmp_path
        / "revisions"
        / REVISION_ID
        / "normalization"
        / STEP_ID
        / "prompt.json"
    )
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"x" * 16)

    with pytest.raises(NormalizationArtifactCommitError, match="exceeds configured maximum"):
        write_normalization_protected_artifact(
            storage_root=tmp_path,
            package_revision_id=REVISION_ID,
            step_id=STEP_ID,
            artifact_kind="prompt",
            payload=b"small",
            max_bytes=8,
        )


def test_concurrent_identical_payload_commit_is_idempotent(tmp_path: Path) -> None:
    payload = b'{"race":"winner"}'
    results: list[Exception | object] = []

    def _write() -> None:
        try:
            results.append(
                write_normalization_protected_artifact(
                    storage_root=tmp_path,
                    package_revision_id=REVISION_ID,
                    step_id=STEP_ID,
                    artifact_kind="response",
                    payload=payload,
                    max_bytes=1024,
                )
            )
        except Exception as exc:  # noqa: BLE001 - collect thread failures
            results.append(exc)

    import threading

    threads = [threading.Thread(target=_write) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 4
    assert all(not isinstance(item, Exception) for item in results)
    assert len({item.sha256 for item in results}) == 1
    assert list((tmp_path / "_tmp").iterdir()) == []


def test_link_race_with_different_existing_payload_fails(tmp_path: Path, monkeypatch) -> None:
    payload = b"new-bytes"
    final_path = (
        tmp_path
        / "revisions"
        / REVISION_ID
        / "normalization"
        / STEP_ID
        / "response.json"
    )
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"existing-bytes")

    def _raise_exists(src: str | bytes, dst: str | bytes) -> None:
        raise FileExistsError(dst)

    monkeypatch.setattr(os, "link", _raise_exists)

    with pytest.raises(NormalizationArtifactCommitError, match="already exists"):
        write_normalization_protected_artifact(
            storage_root=tmp_path,
            package_revision_id=REVISION_ID,
            step_id=STEP_ID,
            artifact_kind="response",
            payload=payload,
            max_bytes=1024,
        )
