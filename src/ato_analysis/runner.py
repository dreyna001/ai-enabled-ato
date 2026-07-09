"""End-to-end orchestration for processing one evidence package."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ato_analysis.analysis.sufficiency_matrix import (
    MatrixValidationError,
    run_sufficiency_matrix,
)
from ato_analysis.audit.audit_log import write_audit_record
from ato_analysis.config import PROJECT_ROOT, Settings, load_settings
from ato_analysis.ingest.read_package import (
    PackageReadError,
    find_incoming_package,
    parse_raw_json_or_text,
    read_package_file,
)
from ato_analysis.llm.counting_client import CountingLLMClient
from ato_analysis.llm.openai_client import OpenAILLMClient
from ato_analysis.models.package_schema import PackageModel
from ato_analysis.models.report_schema import AuditStatus, ReportPaths
from ato_analysis.normalize.normalize_deterministic import try_deterministic_normalize
from ato_analysis.normalize.normalize_llm import normalize_to_canonical
from ato_analysis.report.json_report import build_report, write_json_report
from ato_analysis.report.markdown_generator import write_markdown_report
from ato_analysis.validate.package_validate import (
    check_sensitive_content,
    validate_package,
)
from ato_analysis.validate.preflight import compute_preflight
from ato_analysis.validate.quarantine import quarantine_package

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessOutcome:
    package_id: str
    status: AuditStatus
    audit_path: Path | None
    report_json_path: Path | None
    report_md_path: Path | None
    llm_call_count: int
    message: str


def process_one_package(
    package_id: str,
    *,
    fixture: str | None = None,
    dry_run: bool = False,
) -> ProcessOutcome:
    """Run the Block 1 pipeline for a single incoming package."""
    if dry_run:
        os.environ["DRY_RUN"] = "true"

    settings = load_settings()
    llm_call_count = 0
    input_hash = ""
    raw_path: Path | None = None
    preflight_score = 0.0

    try:
        if fixture:
            _copy_fixture(fixture, package_id, settings)

        raw_path = find_incoming_package(package_id, settings)
        raw_bytes, _suffix = read_package_file(raw_path, settings)
        input_hash = hashlib.sha256(raw_bytes).hexdigest()
        raw_text = raw_bytes.decode("utf-8")
        raw_parsed = parse_raw_json_or_text(raw_bytes)

        normalized_data, normalize_calls = _normalize_to_dict(
            raw_parsed, package_id, settings
        )
        llm_call_count += normalize_calls

        validation = validate_package(
            normalized_data,
            expected_package_id=package_id,
        )
        if not validation.valid or validation.package is None:
            return _quarantine(
                package_id=package_id,
                raw_path=raw_path,
                settings=settings,
                input_hash=input_hash,
                llm_call_count=llm_call_count,
                preflight_score=0.0,
                reason={
                    "stage": "validate",
                    "errors": validation.errors,
                },
                message="Package failed validation; quarantined.",
            )

        package = validation.package
        sensitive_errors = check_sensitive_content(raw_text, package, settings)
        if sensitive_errors:
            return _quarantine(
                package_id=package_id,
                raw_path=raw_path,
                settings=settings,
                input_hash=input_hash,
                llm_call_count=llm_call_count,
                preflight_score=0.0,
                reason={
                    "stage": "sensitive_check",
                    "errors": sensitive_errors,
                },
                message="Sensitive content blocked; quarantined.",
            )

        preflight = compute_preflight(package, validation.warnings, settings)
        preflight_score = preflight.score
        if preflight.blocked:
            return _quarantine(
                package_id=package_id,
                raw_path=raw_path,
                settings=settings,
                input_hash=input_hash,
                llm_call_count=llm_call_count,
                preflight_score=preflight_score,
                reason={
                    "stage": "preflight",
                    "score": preflight.score,
                    "threshold": settings.preflight_block_threshold,
                    "warnings": preflight.warnings,
                },
                message="Pre-flight score below threshold; quarantined.",
            )

        matrix_rows = []
        if settings.dry_run:
            summary_note = (
                "DRY_RUN enabled: sufficiency matrix LLM step skipped. "
                "No OpenAI calls were made for matrix analysis."
            )
        else:
            matrix_rows, matrix_calls = _run_matrix(
                package,
                validation.stale_evidence_ids,
                settings,
            )
            llm_call_count += matrix_calls

        report = build_report(
            package,
            preflight,
            matrix_rows,
            validation.warnings,
        )
        if settings.dry_run:
            report = report.model_copy(
                update={
                    "summary": summary_note,
                    "evidence_matrix": [],
                }
            )

        json_path = settings.report_dir / f"{package_id}.json"
        md_path = settings.report_dir / f"{package_id}.md"
        write_json_report(json_path, report)
        write_markdown_report(md_path, report)

        _finalize_success(
            package_id=package_id,
            raw_path=raw_path,
            package=package,
            settings=settings,
        )

        audit_path, _ = write_audit_record(
            package_id=package_id,
            settings=settings,
            input_hash=input_hash,
            llm_call_count=llm_call_count,
            preflight_score=preflight_score,
            status="completed",
            report_paths=ReportPaths(
                json_path=str(json_path.resolve()),
                markdown_path=str(md_path.resolve()),
            ),
        )

        return ProcessOutcome(
            package_id=package_id,
            status="completed",
            audit_path=audit_path,
            report_json_path=json_path,
            report_md_path=md_path,
            llm_call_count=llm_call_count,
            message="Package processed successfully.",
        )

    except (
        FileNotFoundError,
        PackageReadError,
        ValidationError,
        MatrixValidationError,
        RuntimeError,
        ValueError,
    ) as exc:
        logger.exception("Processing failed for package %s", package_id)
        if raw_path is not None and raw_path.is_file():
            audit_path, _ = write_audit_record(
                package_id=package_id,
                settings=settings,
                input_hash=input_hash or _hash_file(raw_path),
                llm_call_count=llm_call_count,
                preflight_score=preflight_score,
                status="quarantined",
            )
            quarantine_package(
                package_id,
                raw_path,
                reason={"stage": "error", "detail": str(exc)},
                settings=settings,
            )
            _remove_incoming(raw_path)
            return ProcessOutcome(
                package_id=package_id,
                status="quarantined",
                audit_path=audit_path,
                report_json_path=None,
                report_md_path=None,
                llm_call_count=llm_call_count,
                message=str(exc),
            )
        raise


def _normalize_to_dict(
    raw_parsed: dict[str, Any] | str,
    package_id: str,
    settings: Settings,
) -> tuple[dict[str, Any], int]:
    deterministic = try_deterministic_normalize(raw_parsed, package_id)
    if deterministic is not None:
        return deterministic.model_dump(mode="json"), 0

    if isinstance(raw_parsed, dict):
        if _is_canonical(raw_parsed, package_id):
            return PackageModel.model_validate(raw_parsed).model_dump(mode="json"), 0
        if _looks_like_canonical_attempt(raw_parsed):
            return raw_parsed, 0

    if settings.dry_run:
        raise RuntimeError(
            "DRY_RUN enabled: non-canonical input requires LLM normalize, which is blocked"
        )

    client = CountingLLMClient(OpenAILLMClient(settings))
    try:
        package = normalize_to_canonical(raw_parsed, package_id, client, settings)
        return package.model_dump(mode="json"), client.call_count
    finally:
        if isinstance(client._inner, OpenAILLMClient):
            client._inner.close()


def _looks_like_canonical_attempt(raw_parsed: dict[str, Any]) -> bool:
    return (
        "package_id" in raw_parsed
        and "controls" in raw_parsed
        and "evidence_items" in raw_parsed
    )


def _run_matrix(
    package: PackageModel,
    stale_ids: list[str],
    settings: Settings,
) -> tuple[list, int]:
    client = CountingLLMClient(OpenAILLMClient(settings))
    try:
        rows = run_sufficiency_matrix(package, stale_ids, client, settings)
        return rows, client.call_count
    finally:
        if isinstance(client._inner, OpenAILLMClient):
            client._inner.close()


def _is_canonical(raw_parsed: dict[str, Any] | str, package_id: str) -> bool:
    if not isinstance(raw_parsed, dict):
        return False
    try:
        package = PackageModel.model_validate(raw_parsed)
    except ValidationError:
        return False
    return package.package_id == package_id


def _copy_fixture(fixture: str, package_id: str, settings: Settings) -> None:
    fixtures_dir = PROJECT_ROOT / "data" / "fixtures"
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)

    for suffix in (".json", ".txt"):
        source = fixtures_dir / f"{fixture}{suffix}"
        if source.is_file():
            dest = settings.incoming_dir / f"{package_id}{suffix}"
            shutil.copy2(source, dest)
            return

    raise FileNotFoundError(
        f"Fixture {fixture!r} not found in {fixtures_dir} (.json or .txt)"
    )


def _quarantine(
    *,
    package_id: str,
    raw_path: Path,
    settings: Settings,
    input_hash: str,
    llm_call_count: int,
    preflight_score: float,
    reason: dict[str, Any] | str,
    message: str,
) -> ProcessOutcome:
    quarantine_package(package_id, raw_path, reason, settings)
    _remove_incoming(raw_path)
    audit_path, _ = write_audit_record(
        package_id=package_id,
        settings=settings,
        input_hash=input_hash,
        llm_call_count=llm_call_count,
        preflight_score=preflight_score,
        status="quarantined",
    )
    return ProcessOutcome(
        package_id=package_id,
        status="quarantined",
        audit_path=audit_path,
        report_json_path=None,
        report_md_path=None,
        llm_call_count=llm_call_count,
        message=message,
    )


def _finalize_success(
    *,
    package_id: str,
    raw_path: Path,
    package: PackageModel,
    settings: Settings,
) -> None:
    package_dir = settings.processed_dir / package_id
    raw_dir = package_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_dest = raw_dir / raw_path.name
    shutil.copy2(raw_path, raw_dest)

    canonical_path = package_dir / f"{package_id}.canonical.json"
    canonical_path.write_text(
        json.dumps(package.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )

    _remove_incoming(raw_path)


def _remove_incoming(raw_path: Path) -> None:
    if raw_path.is_file():
        raw_path.unlink()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
