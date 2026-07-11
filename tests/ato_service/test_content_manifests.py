"""Tests for durable package content manifest writing."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from ato_service.blobs import BlobStore
from ato_service.content_manifests import (
    ContentManifestBlobError,
    ContentManifestCommitError,
    ContentManifestConflictError,
    ContentManifestError,
    ContentManifestValidationError,
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    MAX_PACKAGE_BYTES,
    ManifestSourceEntry,
    StoredContentManifest,
    write_content_manifest,
)
from ato_service.storage_reconciliation import is_recognized_staging_filename

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "contracts" / "content-manifest.schema.json"
FORMAT_CHECKER = FormatChecker()

PACKAGE_REVISION_ID = "11111111-1111-4111-8111-111111111111"
ARTIFACT_ID_A = "22222222-2222-4222-8222-222222222222"
ARTIFACT_ID_B = "33333333-3333-4333-8333-333333333333"


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


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def _store_blob(store: BlobStore, payload: bytes):
    return store.store_stream(io.BytesIO(payload), max_bytes=104_857_600)


def _entry(blob, artifact_id: str) -> ManifestSourceEntry:
    return ManifestSourceEntry(
        artifact_id=artifact_id,
        storage_key=blob.storage_key,
        sha256=blob.sha256,
        size_bytes=blob.size_bytes,
    )


def _canonical_bytes(document: dict) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_writes_valid_multi_artifact_manifest(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob_a = _store_blob(store, b"first-artifact-bytes")
    blob_b = _store_blob(store, b"second-artifact-bytes")

    result = write_content_manifest(
        PACKAGE_REVISION_ID,
        [
            _entry(blob_a, ARTIFACT_ID_A),
            _entry(blob_b, ARTIFACT_ID_B),
        ],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )

    expected_document = {
        "schema_version": "1.0.0",
        "package_revision_id": PACKAGE_REVISION_ID,
        "artifacts": [
            {
                "artifact_id": ARTIFACT_ID_A,
                "storage_key": blob_a.storage_key,
                "sha256": blob_a.sha256,
                "size_bytes": blob_a.size_bytes,
            },
            {
                "artifact_id": ARTIFACT_ID_B,
                "storage_key": blob_b.storage_key,
                "sha256": blob_b.sha256,
                "size_bytes": blob_b.size_bytes,
            },
        ],
    }
    expected_bytes = _canonical_bytes(expected_document)

    assert result == StoredContentManifest(
        manifest_storage_key=(
            f"manifests/packages/{PACKAGE_REVISION_ID}/content-manifest.json"
        ),
        sha256=hashlib.sha256(expected_bytes).hexdigest(),
        size_bytes=len(expected_bytes),
        document=expected_document,
    )
    assert not list(_schema_validator().iter_errors(result.document))

    manifest_path = tmp_path / result.manifest_storage_key
    assert manifest_path.is_file()
    assert manifest_path.read_bytes() == expected_bytes


def test_orders_artifacts_deterministically_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    store = BlobStore(tmp_path)
    blob_a = _store_blob(store, b"alpha")
    blob_b = _store_blob(store, b"beta")
    entries = [
        _entry(blob_b, ARTIFACT_ID_B),
        _entry(blob_a, ARTIFACT_ID_A),
    ]

    first = write_content_manifest(
        PACKAGE_REVISION_ID,
        entries,
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )
    manifest_path = tmp_path / first.manifest_storage_key
    before_mtime_ns = manifest_path.stat().st_mtime_ns

    second = write_content_manifest(
        PACKAGE_REVISION_ID,
        list(reversed(entries)),
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )

    assert second == first
    assert first.document["artifacts"][0]["artifact_id"] == ARTIFACT_ID_A
    assert first.document["artifacts"][1]["artifact_id"] == ARTIFACT_ID_B
    assert manifest_path.stat().st_mtime_ns == before_mtime_ns
    assert not any((tmp_path / "_tmp").glob("*"))


def test_rejects_duplicate_artifact_ids(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"duplicate-id")

    with pytest.raises(ContentManifestValidationError, match="duplicate artifact_id"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                _entry(blob, ARTIFACT_ID_A),
                _entry(blob, ARTIFACT_ID_A),
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


def test_rejects_duplicate_storage_keys(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"duplicate-key")

    with pytest.raises(ContentManifestValidationError, match="duplicate storage_key"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                _entry(blob, ARTIFACT_ID_A),
                _entry(blob, ARTIFACT_ID_B),
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


def test_rejects_storage_key_digest_mismatch(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"mismatch")
    mismatched_key = f"ff/{blob.sha256}"

    with pytest.raises(ContentManifestValidationError, match="prefix"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                ManifestSourceEntry(
                    artifact_id=ARTIFACT_ID_A,
                    storage_key=mismatched_key,
                    sha256=blob.sha256,
                    size_bytes=blob.size_bytes,
                )
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


def test_rejects_missing_blob(tmp_path: Path) -> None:
    digest = hashlib.sha256(b"missing").hexdigest()
    storage_key = f"{digest[:2]}/{digest}"

    with pytest.raises(ContentManifestBlobError, match="does not exist"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                ManifestSourceEntry(
                    artifact_id=ARTIFACT_ID_A,
                    storage_key=storage_key,
                    sha256=digest,
                    size_bytes=len(b"missing"),
                )
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


def test_rejects_wrong_declared_size(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"wrong-size")

    with pytest.raises(ContentManifestBlobError, match="size does not match"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                ManifestSourceEntry(
                    artifact_id=ARTIFACT_ID_A,
                    storage_key=blob.storage_key,
                    sha256=blob.sha256,
                    size_bytes=blob.size_bytes + 1,
                )
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


def test_rejects_altered_blob_digest(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    payload = b"unaltered-bytes"
    blob = _store_blob(store, payload)
    blob_path = tmp_path / "blobs" / Path(*blob.storage_key.split("/"))
    blob_path.write_bytes(b"altered-bytes-x")

    with pytest.raises(ContentManifestBlobError, match="digest does not match"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symbolic links")
def test_rejects_symlinked_blobs_root_during_verification(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    outside = tmp_path / "outside"
    storage_root.mkdir()
    outside.mkdir()

    payload = b"outside-blob"
    digest = hashlib.sha256(payload).hexdigest()
    outside_blob = outside / digest[:2] / digest
    outside_blob.parent.mkdir()
    outside_blob.write_bytes(payload)
    _symlink_or_skip(
        storage_root / "blobs",
        outside,
        target_is_directory=True,
    )

    entry = ManifestSourceEntry(
        artifact_id=ARTIFACT_ID_A,
        storage_key=f"{digest[:2]}/{digest}",
        sha256=digest,
        size_bytes=len(payload),
    )
    with pytest.raises(ContentManifestBlobError, match="path is unsafe"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [entry],
            storage_root=storage_root,
            schema_path=SCHEMA_PATH,
        )

    assert outside_blob.read_bytes() == payload


def test_rejects_junctioned_blobs_root_during_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    blobs_root = storage_root / "blobs"
    payload = b"junction-blob"
    digest = hashlib.sha256(payload).hexdigest()
    blob_path = blobs_root / digest[:2] / digest
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(payload)
    original_is_junction = Path.is_junction

    def _is_junction(self):
        if self == blobs_root:
            return True
        return original_is_junction(self)

    monkeypatch.setattr(Path, "is_junction", _is_junction)
    entry = ManifestSourceEntry(
        artifact_id=ARTIFACT_ID_A,
        storage_key=f"{digest[:2]}/{digest}",
        sha256=digest,
        size_bytes=len(payload),
    )

    with pytest.raises(ContentManifestBlobError, match="path is unsafe"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [entry],
            storage_root=storage_root,
            schema_path=SCHEMA_PATH,
        )


def test_rejects_invalid_uuid_v4_package_revision_id(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"uuid-check")

    with pytest.raises(ContentManifestValidationError, match="package_revision_id"):
        write_content_manifest(
            "00000000-0000-0000-0000-000000000000",
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )

    with pytest.raises(ContentManifestValidationError, match="artifact_id"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                ManifestSourceEntry(
                    artifact_id="00000000-0000-0000-0000-000000000000",
                    storage_key=blob.storage_key,
                    sha256=blob.sha256,
                    size_bytes=blob.size_bytes,
                )
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


def test_rejects_existing_different_manifest(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob_a = _store_blob(store, b"first-manifest")
    blob_b = _store_blob(store, b"second-manifest")

    write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob_a, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )

    with pytest.raises(
        ContentManifestConflictError, match="different content manifest"
    ):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob_b, ARTIFACT_ID_B)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )

    manifest_path = (
        tmp_path
        / "manifests"
        / "packages"
        / PACKAGE_REVISION_ID
        / "content-manifest.json"
    )
    assert manifest_path.is_file()
    assert blob_a.sha256 in manifest_path.read_text(encoding="utf-8")
    assert blob_b.sha256 not in manifest_path.read_text(encoding="utf-8")


def test_replace_unreferenced_existing_updates_different_manifest(
    tmp_path: Path,
) -> None:
    store = BlobStore(tmp_path)
    blob_a = _store_blob(store, b"first-manifest")
    blob_b = _store_blob(store, b"second-manifest")

    first = write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob_a, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )
    manifest_path = tmp_path / first.manifest_storage_key
    before_mtime_ns = manifest_path.stat().st_mtime_ns

    second = write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob_b, ARTIFACT_ID_B)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
        replace_unreferenced_existing=True,
    )

    assert second.sha256 != first.sha256
    assert manifest_path.read_bytes() == _canonical_bytes(second.document)
    assert manifest_path.stat().st_mtime_ns >= before_mtime_ns
    assert blob_b.sha256 in manifest_path.read_text(encoding="utf-8")
    assert blob_a.sha256 not in manifest_path.read_text(encoding="utf-8")
    assert not any((tmp_path / "_tmp").glob("*"))


def test_replace_unreferenced_existing_identical_bytes_is_no_op(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"same-manifest")

    first = write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )
    manifest_path = tmp_path / first.manifest_storage_key
    before_mtime_ns = manifest_path.stat().st_mtime_ns

    second = write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
        replace_unreferenced_existing=True,
    )

    assert second == first
    assert manifest_path.stat().st_mtime_ns == before_mtime_ns


def test_replace_failure_preserves_existing_manifest_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = BlobStore(tmp_path)
    blob_a = _store_blob(store, b"first-manifest")
    blob_b = _store_blob(store, b"second-manifest")
    temp_dir = tmp_path / "_tmp"

    first = write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob_a, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )
    manifest_path = tmp_path / first.manifest_storage_key
    original_bytes = manifest_path.read_bytes()

    def _fail_replace(src, dst):
        raise OSError("simulated manifest replace failure")

    monkeypatch.setattr("ato_service.content_manifests.os.replace", _fail_replace)

    with pytest.raises(
        ContentManifestCommitError, match="could not be durably committed"
    ):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob_b, ARTIFACT_ID_B)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
            replace_unreferenced_existing=True,
        )

    assert manifest_path.read_bytes() == original_bytes
    assert blob_a.sha256 in manifest_path.read_text(encoding="utf-8")
    assert blob_b.sha256 not in manifest_path.read_text(encoding="utf-8")
    assert not any(
        path for path in temp_dir.glob("*") if is_recognized_staging_filename(path.name)
    )


def test_default_limits_match_runtime_contracts() -> None:
    assert MAX_ARTIFACTS == 500
    assert MAX_ARTIFACT_BYTES == 104_857_600
    assert MAX_PACKAGE_BYTES == 2_147_483_648


def test_accepts_exact_artifact_and_package_boundaries(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    payload = b"x" * 1024
    blob = store.store_stream(io.BytesIO(payload), max_bytes=1024)

    result = write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
        max_artifact_bytes=1024,
        max_package_bytes=1024,
    )

    assert result.document["artifacts"][0]["size_bytes"] == 1024


def test_rejects_artifact_count_above_limit(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob_a = _store_blob(store, b"one")
    blob_b = _store_blob(store, b"two")
    blob_c = _store_blob(store, b"three")

    with pytest.raises(ContentManifestValidationError, match="must not exceed 2"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                _entry(blob_a, ARTIFACT_ID_A),
                _entry(blob_b, ARTIFACT_ID_B),
                _entry(
                    blob_c,
                    "44444444-4444-4444-8444-444444444444",
                ),
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
            max_artifacts=2,
        )


def test_over_limit_generator_consumes_only_max_plus_one_entries(
    tmp_path: Path,
) -> None:
    consumed = 0

    def source_entries():
        nonlocal consumed
        for _ in range(100):
            consumed += 1
            yield object()

    with pytest.raises(ContentManifestValidationError, match="must not exceed 2"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            source_entries(),  # type: ignore[arg-type]
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
            max_artifacts=2,
        )

    assert consumed == 3


def test_rejects_per_file_size_above_limit(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    payload = b"x" * 2048
    blob = store.store_stream(io.BytesIO(payload), max_bytes=2048)

    with pytest.raises(ContentManifestValidationError, match="must not exceed 1024"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
            max_artifact_bytes=1024,
        )


def test_rejects_aggregate_size_above_limit(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob_a = _store_blob(store, b"aaa")
    blob_b = _store_blob(store, b"bbbb")

    with pytest.raises(ContentManifestValidationError, match="aggregate artifact size"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [
                _entry(blob_a, ARTIFACT_ID_A),
                _entry(blob_b, ARTIFACT_ID_B),
            ],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
            max_package_bytes=blob_a.size_bytes + blob_b.size_bytes - 1,
        )


def test_aggregate_limit_uses_overflow_safe_integer_sum(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"x")
    huge_limit = (1 << 400) + blob.size_bytes

    result = write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
        max_package_bytes=huge_limit,
    )

    assert result.document["artifacts"][0]["size_bytes"] == blob.size_bytes


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"max_artifacts": MAX_ARTIFACTS + 1},
            f"max_artifacts must not exceed {MAX_ARTIFACTS}",
        ),
        (
            {"max_artifact_bytes": MAX_ARTIFACT_BYTES + 1},
            f"max_artifact_bytes must not exceed {MAX_ARTIFACT_BYTES}",
        ),
    ],
)
def test_rejects_ceiling_expansion_before_consuming_entries(
    tmp_path: Path,
    kwargs: dict[str, int],
    match: str,
) -> None:
    consumed = 0

    def source_entries():
        nonlocal consumed
        consumed += 1
        yield object()

    with pytest.raises(ContentManifestValidationError, match=match):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            source_entries(),  # type: ignore[arg-type]
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
            **kwargs,
        )

    assert consumed == 0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_artifacts": 0}, "max_artifacts must be at least 1"),
        ({"max_artifact_bytes": -1}, "max_artifact_bytes must be at least 1"),
        ({"max_package_bytes": True}, "max_package_bytes must be a positive integer"),
    ],
)
def test_rejects_invalid_limit_parameters(
    tmp_path: Path,
    kwargs: dict,
    match: str,
) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"limits")

    with pytest.raises(ContentManifestValidationError, match=match):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
            **kwargs,
        )


def test_rejects_empty_source_entries(tmp_path: Path) -> None:
    with pytest.raises(ContentManifestValidationError, match="must not be empty"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )


def test_cleans_temp_after_manifest_write_failure(tmp_path: Path, monkeypatch) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"manifest-write-failure")
    temp_dir = tmp_path / "_tmp"
    original_open = Path.open

    def _fail_manifest_staging_open(self, mode="r", *args, **kwargs):
        if (
            self.parent.name == "_tmp"
            and self.name.startswith("manifest-staging-")
            and "b" in mode
        ):
            raise OSError("simulated manifest write failure")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _fail_manifest_staging_open)

    with pytest.raises(
        ContentManifestCommitError, match="could not be durably committed"
    ):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )

    assert not any(
        path for path in temp_dir.glob("*") if is_recognized_staging_filename(path.name)
    )
    manifest_path = (
        tmp_path
        / "manifests"
        / "packages"
        / PACKAGE_REVISION_ID
        / "content-manifest.json"
    )
    assert not manifest_path.is_file()


def test_cleans_temp_after_manifest_replace_failure(
    tmp_path: Path, monkeypatch
) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"manifest-replace-failure")
    temp_dir = tmp_path / "_tmp"

    def _fail_replace(src, dst):
        raise OSError("simulated manifest replace failure")

    monkeypatch.setattr("ato_service.content_manifests.os.replace", _fail_replace)

    with pytest.raises(
        ContentManifestCommitError, match="could not be durably committed"
    ):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )

    assert not any(
        path for path in temp_dir.glob("*") if is_recognized_staging_filename(path.name)
    )
    manifest_path = (
        tmp_path
        / "manifests"
        / "packages"
        / PACKAGE_REVISION_ID
        / "content-manifest.json"
    )
    assert not manifest_path.is_file()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symbolic links")
@pytest.mark.parametrize(
    ("redirected_path", "expected_error"),
    [
        ("_tmp", ContentManifestCommitError),
        ("manifests", ContentManifestError),
    ],
)
def test_manifest_writer_rejects_symlinked_owned_subtree(
    tmp_path: Path,
    redirected_path: str,
    expected_error: type[ContentManifestError],
) -> None:
    storage_root = tmp_path / "storage"
    outside = tmp_path / "outside"
    store = BlobStore(storage_root)
    blob = _store_blob(store, b"safe-source")
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"outside-evidence")

    redirected = storage_root / redirected_path
    if redirected.exists():
        redirected.rmdir()
    _symlink_or_skip(redirected, outside, target_is_directory=True)

    with pytest.raises(expected_error):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=storage_root,
            schema_path=SCHEMA_PATH,
        )

    assert marker.read_bytes() == b"outside-evidence"
    assert set(outside.iterdir()) == {marker}


def test_manifest_commit_error_does_not_leak_paths_or_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"secret-manifest-bytes")

    def _fail_replace(src, dst):
        raise OSError(f"failed writing {src} to {dst}")

    monkeypatch.setattr("ato_service.content_manifests.os.replace", _fail_replace)

    with pytest.raises(ContentManifestCommitError) as exc_info:
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )

    message = str(exc_info.value)
    assert str(tmp_path) not in message
    assert "secret-manifest-bytes" not in message
    assert exc_info.value.__cause__ is not None


def test_cleanup_failure_does_not_mask_manifest_commit_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"cleanup-failure")
    original_unlink = Path.unlink

    def _fail_replace(src, dst):
        raise OSError("simulated replace failure")

    def _fail_staging_unlink(self, *args, **kwargs):
        if self.name.startswith("manifest-staging-"):
            raise OSError("simulated cleanup failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr("ato_service.content_manifests.os.replace", _fail_replace)
    monkeypatch.setattr(Path, "unlink", _fail_staging_unlink)

    with pytest.raises(ContentManifestCommitError) as exc_info:
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [_entry(blob, ARTIFACT_ID_A)],
            storage_root=tmp_path,
            schema_path=SCHEMA_PATH,
        )

    assert str(exc_info.value.__cause__) == "simulated replace failure"
    assert any(
        "temporary manifest staging cleanup also failed" in note
        for note in exc_info.value.__notes__
    )


def test_uses_recognized_manifest_staging_filename(tmp_path: Path, monkeypatch) -> None:
    store = BlobStore(tmp_path)
    blob = _store_blob(store, b"manifest-staging-name")
    captured: list[str] = []

    original_open = Path.open

    def _capture_open(self, *args, **kwargs):
        if self.parent.name == "_tmp":
            captured.append(self.name)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _capture_open)

    write_content_manifest(
        PACKAGE_REVISION_ID,
        [_entry(blob, ARTIFACT_ID_A)],
        storage_root=tmp_path,
        schema_path=SCHEMA_PATH,
    )

    assert len(captured) == 1
    assert is_recognized_staging_filename(captured[0])
    assert captured[0].startswith("manifest-staging-")
