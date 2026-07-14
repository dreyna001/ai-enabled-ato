"""Semantic validation for immutable AI qualification evaluation records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ato_service.ai_evaluation.schema import (
    evaluation_record_validator,
    format_validation_error,
)
from ato_service.ai_evaluation.types import (
    DigestVerificationTarget,
    EvaluationRecordValidationReport,
)

_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "api_key",
        "bearer_token",
        "client_secret",
        "credential",
        "customer_payload",
        "id_token",
        "password",
        "private_key",
        "raw_response",
        "raw_prompt",
        "signature",
        "token",
    }
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?:^|[\s\"'=:])(?:sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,})",
    re.IGNORECASE,
)
_UUID_V4_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-"
    r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class EvaluationRecordError(Exception):
    """Base error for evaluation record operations."""


class EvaluationRecordValidationError(EvaluationRecordError, ValueError):
    """Raised when an evaluation record fails schema or semantic validation."""


def validate_evaluation_record(
    document: Mapping[str, Any],
    *,
    project_root: Path | None = None,
    digest_targets: Sequence[DigestVerificationTarget] = (),
    require_hs006_unresolved: bool = True,
) -> EvaluationRecordValidationReport:
    """Validate one evaluation record against schema and semantic guardrails."""
    validator = evaluation_record_validator(project_root=project_root)
    schema_errors = tuple(
        format_validation_error(error) for error in validator.iter_errors(document)
    )
    if schema_errors:
        return EvaluationRecordValidationReport(
            valid=False,
            evaluation_id=_safe_evaluation_id(document),
            outcome=_safe_outcome(document),
            schema_errors=schema_errors,
            semantic_errors=(),
            digest_errors=(),
        )

    semantic_errors = _collect_semantic_errors(
        document,
        require_hs006_unresolved=require_hs006_unresolved,
    )
    digest_errors = _verify_digest_targets(digest_targets)
    valid = not semantic_errors and not digest_errors
    return EvaluationRecordValidationReport(
        valid=valid,
        evaluation_id=str(document["evaluation_id"]),
        outcome=document["outcome"],
        schema_errors=(),
        semantic_errors=semantic_errors,
        digest_errors=digest_errors,
    )


def require_valid_evaluation_record(
    document: Mapping[str, Any],
    *,
    project_root: Path | None = None,
    digest_targets: Sequence[DigestVerificationTarget] = (),
    require_hs006_unresolved: bool = True,
) -> EvaluationRecordValidationReport:
    report = validate_evaluation_record(
        document,
        project_root=project_root,
        digest_targets=digest_targets,
        require_hs006_unresolved=require_hs006_unresolved,
    )
    if report.valid:
        return report
    message = _format_report_failure(report)
    raise EvaluationRecordValidationError(message)


def canonical_record_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def record_content_sha256(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_record_bytes(document)).hexdigest()


def _collect_semantic_errors(
    document: Mapping[str, Any],
    *,
    require_hs006_unresolved: bool,
) -> tuple[str, ...]:
    errors: list[str] = []

    evaluation_id = str(document["evaluation_id"])
    if _UUID_V4_PATTERN.fullmatch(evaluation_id) is None:
        errors.append("evaluation_id must be a UUID v4")

    errors.extend(_scan_forbidden_fields(document, prefix=""))

    dataset = document["dataset"]
    holdout_identity = dataset["holdout_identity"]
    if dataset["holdout_manifest_sha256"] != holdout_identity["holdout_manifest_sha256"]:
        errors.append(
            "dataset.holdout_manifest_sha256 must equal "
            "dataset.holdout_identity.holdout_manifest_sha256"
        )

    adjudicators = document["adjudicators"]
    adjudicator_ids = [entry["adjudicator_id"] for entry in adjudicators]
    if len(adjudicator_ids) != len(set(adjudicator_ids)):
        errors.append("adjudicators must contain unique adjudicator_id values")

    roles = {entry["role"] for entry in adjudicators}
    if "holdout_labeler" not in roles:
        errors.append("adjudicators must include at least one holdout_labeler")
    if "holdout_custodian" not in roles:
        errors.append("adjudicators must include one holdout_custodian")

    hs006_entries = [
        entry
        for entry in document["hard_stop_status"]
        if entry["hard_stop_id"] == "HS-006"
    ]
    if not hs006_entries:
        errors.append("hard_stop_status must include HS-006")
    else:
        hs006 = hs006_entries[0]
        if require_hs006_unresolved and hs006["status"] != "unresolved":
            errors.append("HS-006 must remain unresolved until live qualification closes it")
        if document["outcome"] == "passed":
            if require_hs006_unresolved:
                errors.append(
                    "outcome=passed is prohibited while HS-006 remains unresolved"
                )
            elif hs006["status"] != "resolved":
                errors.append("outcome=passed requires HS-006 status=resolved")

    if document["outcome"] == "failed" and not document["blockers"]:
        errors.append("failed evaluations must record at least one blocker")
    if document["outcome"] == "invalid" and not document["blockers"]:
        errors.append("invalid evaluations must record at least one blocker")

    return tuple(errors)


def _scan_forbidden_fields(value: Any, *, prefix: str) -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            field_path = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in _FORBIDDEN_FIELD_NAMES:
                errors.append(f"forbidden field present: {field_path}")
            errors.extend(_scan_forbidden_fields(nested, prefix=field_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            errors.extend(_scan_forbidden_fields(nested, prefix=f"{prefix}[{index}]"))
    elif isinstance(value, str):
        if _SECRET_VALUE_PATTERN.search(value):
            errors.append(f"suspected secret material at {prefix or '<root>'}")
    return errors


def _verify_digest_targets(
    digest_targets: Sequence[DigestVerificationTarget],
) -> tuple[str, ...]:
    errors: list[str] = []
    for target in digest_targets:
        source_path = Path(target.source_path).resolve()
        if not source_path.is_file():
            errors.append(f"{target.field_path}: missing digest source {source_path}")
            continue
        actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if actual != target.expected_sha256:
            errors.append(
                f"{target.field_path}: digest mismatch for {source_path.name}"
            )
    return tuple(errors)


def _safe_evaluation_id(document: Mapping[str, Any]) -> str | None:
    evaluation_id = document.get("evaluation_id")
    return str(evaluation_id) if isinstance(evaluation_id, str) else None


def _safe_outcome(document: Mapping[str, Any]) -> str | None:
    outcome = document.get("outcome")
    return str(outcome) if isinstance(outcome, str) else None


def _format_report_failure(report: EvaluationRecordValidationReport) -> str:
    parts = list(report.schema_errors)
    parts.extend(report.semantic_errors)
    parts.extend(report.digest_errors)
    return "; ".join(parts) if parts else "evaluation record validation failed"
