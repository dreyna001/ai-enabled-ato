"""Append-only atomic filesystem persistence for evaluation records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from ato_service.ai_evaluation.record import (
    EvaluationRecordError,
    EvaluationRecordValidationError,
    canonical_record_bytes,
    record_content_sha256,
    require_valid_evaluation_record,
)
from ato_service.ai_evaluation.types import DigestVerificationTarget
from ato_service.storage_reconciliation import (
    StoragePathError,
    ensure_storage_directory,
    manifest_staging_path,
    prepare_storage_file_path,
    require_storage_regular_file,
)

_RECORDS_DIR = "evaluations"
_TEMP_DIR_NAME = "_tmp"
_RECORD_FILENAME_SUFFIX = ".json"


class EvaluationRecordPersistenceError(EvaluationRecordError, OSError):
    """Raised when durable evaluation record persistence fails."""


class EvaluationRecordConflictError(EvaluationRecordPersistenceError):
    """Raised when an evaluation record already exists with different content."""


@dataclass(frozen=True, slots=True)
class StoredEvaluationRecord:
    """One immutable evaluation record persisted under the safe records root."""

    evaluation_id: str
    storage_path: str
    sha256: str
    size_bytes: int
    document: dict[str, Any]


def write_evaluation_record(
    document: Mapping[str, Any],
    *,
    records_root: Path,
    project_root: Path | None = None,
    digest_targets: Sequence[DigestVerificationTarget] = (),
    require_hs006_unresolved: bool = True,
) -> StoredEvaluationRecord:
    """Validate and persist one evaluation record with write-once semantics."""
    materialized = json.loads(json.dumps(document))
    report = require_valid_evaluation_record(
        materialized,
        project_root=project_root,
        digest_targets=digest_targets,
        require_hs006_unresolved=require_hs006_unresolved,
    )
    evaluation_id = report.evaluation_id
    if evaluation_id is None:
        raise EvaluationRecordValidationError("evaluation_id is required")

    resolved_root = records_root.resolve()
    record_bytes = canonical_record_bytes(materialized)
    record_digest = record_content_sha256(materialized)
    filename = f"{evaluation_id}{_RECORD_FILENAME_SUFFIX}"

    try:
        final_path = prepare_storage_file_path(
            resolved_root,
            _RECORDS_DIR,
            filename,
        )
    except StoragePathError as exc:
        raise EvaluationRecordPersistenceError(
            "evaluation record storage path is unsafe"
        ) from exc

    if final_path.exists():
        existing_bytes = final_path.read_bytes()
        if existing_bytes == record_bytes:
            return StoredEvaluationRecord(
                evaluation_id=evaluation_id,
                storage_path=str(final_path),
                sha256=record_digest,
                size_bytes=len(existing_bytes),
                document=materialized,
            )
        raise EvaluationRecordConflictError(
            f"evaluation record already exists for {evaluation_id}"
        )

    temp_path: Path | None = None
    try:
        temp_dir = ensure_storage_directory(resolved_root, _TEMP_DIR_NAME)
        generated_temp_path = manifest_staging_path(temp_dir)
        temp_path = prepare_storage_file_path(
            resolved_root,
            _TEMP_DIR_NAME,
            generated_temp_path.name,
        )
        with temp_path.open("xb") as temp_file:
            temp_file.write(record_bytes)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, final_path)
        temp_path = None
        _fsync_directory(final_path.parent)
    except OSError as exc:
        raise EvaluationRecordPersistenceError(
            "evaluation record could not be durably committed"
        ) from exc
    finally:
        _cleanup_staging_path(resolved_root, temp_path)

    return StoredEvaluationRecord(
        evaluation_id=evaluation_id,
        storage_path=str(final_path),
        sha256=record_digest,
        size_bytes=len(record_bytes),
        document=materialized,
    )


def load_evaluation_record(
    evaluation_id: str,
    *,
    records_root: Path,
) -> StoredEvaluationRecord:
    filename = f"{evaluation_id}{_RECORD_FILENAME_SUFFIX}"
    try:
        path = require_storage_regular_file(
            records_root.resolve(),
            _RECORDS_DIR,
            filename,
        )
    except (StoragePathError, FileNotFoundError) as exc:
        raise EvaluationRecordPersistenceError(
            f"evaluation record not found for {evaluation_id}"
        ) from exc

    document = json.loads(path.read_text(encoding="utf-8"))
    record_bytes = path.read_bytes()
    return StoredEvaluationRecord(
        evaluation_id=evaluation_id,
        storage_path=str(path),
        sha256=record_content_sha256(document),
        size_bytes=len(record_bytes),
        document=document,
    )


def _cleanup_staging_path(records_root: Path, temp_path: Path | None) -> None:
    if temp_path is None:
        return
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        return
    try:
        temp_dir = records_root / _TEMP_DIR_NAME
        if temp_dir.is_dir() and not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except OSError:
        return


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
