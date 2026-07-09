"""Audit record writer for package processing runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ato_analysis.config import Settings
from ato_analysis.models.report_schema import AuditRecord, AuditStatus, ReportPaths


def write_audit_record(
    *,
    package_id: str,
    settings: Settings,
    input_hash: str,
    llm_call_count: int,
    preflight_score: float,
    status: AuditStatus,
    report_paths: ReportPaths | None = None,
    run_id: str | None = None,
) -> tuple[Path, AuditRecord]:
    """Write an audit JSON sidecar and return its path and model."""
    resolved_run_id = run_id or str(uuid4())
    record = AuditRecord(
        package_id=package_id,
        run_id=resolved_run_id,
        timestamp=datetime.now(tz=UTC),
        runtime_profile=settings.runtime_profile,
        model=settings.openai_model,
        input_hash=input_hash,
        report_paths=report_paths
        or ReportPaths(json_path="", markdown_path=""),
        llm_call_count=llm_call_count,
        preflight_score=preflight_score,
        status=status,
    )

    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = settings.audit_dir / f"{package_id}-{resolved_run_id}.json"
    audit_path.write_text(
        json.dumps(record.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    return audit_path, record
