"""Operator CLI helpers for AI evaluation record validation and persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ato_service.ai_evaluation.persistence import (
    EvaluationRecordConflictError,
    EvaluationRecordPersistenceError,
    write_evaluation_record,
)
from ato_service.ai_evaluation.record import (
    EvaluationRecordValidationError,
    validate_evaluation_record,
)
from ato_service.ai_evaluation.types import DigestVerificationTarget


def load_record_document(record_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationRecordValidationError(
            f"record file is not valid JSON: {record_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise EvaluationRecordValidationError("evaluation record must be a JSON object")
    return payload


def build_digest_targets(
    *,
    digest_root: Path,
    guide_sha256: str | None = None,
    holdout_manifest_sha256: str | None = None,
) -> tuple[DigestVerificationTarget, ...]:
    targets: list[DigestVerificationTarget] = []
    if guide_sha256 is not None:
        guide_path = digest_root / "docs" / "AI_EVALUATION_GUIDE.md"
        targets.append(
            DigestVerificationTarget(
                field_path="guide_sha256",
                expected_sha256=guide_sha256,
                source_path=str(guide_path),
            )
        )
    if holdout_manifest_sha256 is not None:
        holdout_path = digest_root / "data" / "qualification" / "holdout-manifest.json"
        targets.append(
            DigestVerificationTarget(
                field_path="dataset.holdout_manifest_sha256",
                expected_sha256=holdout_manifest_sha256,
                source_path=str(holdout_path),
            )
        )
    return tuple(targets)


def command_validate_evaluation_record(
    *,
    record_path: Path,
    project_root: Path,
    digest_root: Path | None = None,
    verify_digests: bool = False,
    emit_json: bool = False,
) -> int:
    document = load_record_document(record_path)
    digest_targets = ()
    if verify_digests:
        digest_targets = build_digest_targets(
            digest_root=digest_root or project_root,
            guide_sha256=document.get("guide_sha256"),
            holdout_manifest_sha256=document.get("dataset", {}).get(
                "holdout_manifest_sha256"
            ),
        )
    report = validate_evaluation_record(
        document,
        project_root=project_root,
        digest_targets=digest_targets,
    )
    if emit_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif report.valid:
        print(f"evaluation record valid: {report.evaluation_id} ({report.outcome})")
    else:
        combined = report.schema_errors + report.semantic_errors + report.digest_errors
        print("evaluation record invalid:")
        for item in combined:
            print(f"  - {item}")
    return 0 if report.valid else 1


def command_write_evaluation_record(
    *,
    record_path: Path,
    records_root: Path,
    project_root: Path,
    digest_root: Path | None = None,
    verify_digests: bool = False,
    emit_json: bool = False,
) -> int:
    document = load_record_document(record_path)
    digest_targets = ()
    if verify_digests:
        digest_targets = build_digest_targets(
            digest_root=digest_root or project_root,
            guide_sha256=document.get("guide_sha256"),
            holdout_manifest_sha256=document.get("dataset", {}).get(
                "holdout_manifest_sha256"
            ),
        )
    try:
        stored = write_evaluation_record(
            document,
            records_root=records_root,
            project_root=project_root,
            digest_targets=digest_targets,
        )
    except EvaluationRecordValidationError as exc:
        print(f"validation error: {exc}", file=__import__("sys").stderr)
        return 1
    except EvaluationRecordConflictError as exc:
        print(f"conflict: {exc}", file=__import__("sys").stderr)
        return 2
    except EvaluationRecordPersistenceError as exc:
        print(f"persistence error: {exc}", file=__import__("sys").stderr)
        return 2

    payload = {
        "evaluation_id": stored.evaluation_id,
        "storage_path": stored.storage_path,
        "sha256": stored.sha256,
        "size_bytes": stored.size_bytes,
        "outcome": stored.document.get("outcome"),
    }
    if emit_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "evaluation record written "
            f"id={stored.evaluation_id} path={stored.storage_path}"
        )
    return 0
