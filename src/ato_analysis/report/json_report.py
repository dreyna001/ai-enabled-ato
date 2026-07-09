"""JSON report builder and writer."""

from __future__ import annotations

import json
from pathlib import Path

from ato_analysis.models.package_schema import PackageModel
from ato_analysis.models.report_schema import (
    EvidenceMatrixRow,
    PackageMetadata,
    PreflightResult,
    ReportModel,
)
from ato_analysis.validate.preflight import PreflightOutcome


def build_report(
    package: PackageModel,
    preflight: PreflightOutcome,
    matrix_rows: list[EvidenceMatrixRow],
    validation_warnings: list[str],
) -> ReportModel:
    """Assemble a report model from deterministic and LLM-derived outputs."""
    summary = _build_summary(matrix_rows)
    return ReportModel(
        summary=summary,
        preflight=PreflightResult(
            score=preflight.score,
            blocked=preflight.blocked,
            metadata_complete=preflight.metadata_complete,
            controls_non_empty=preflight.controls_non_empty,
            all_controls_have_evidence=preflight.all_controls_have_evidence,
            no_broken_links=preflight.no_broken_links,
            warnings=preflight.warnings,
        ),
        evidence_matrix=matrix_rows,
        validation_warnings=list(validation_warnings),
        package_metadata=PackageMetadata(
            package_id=package.package_id,
            authorization_path=package.authorization_path,
            baseline=package.baseline,
            impact_level=package.impact_level,
            data_classification=package.data_classification,
            system_name=package.system_name,
            authorization_boundary=package.authorization_boundary,
            assessment_date=package.assessment_date.isoformat(),
            control_count=len(package.controls),
            evidence_count=len(package.evidence_items),
        ),
    )


def write_json_report(path: Path, report: ReportModel) -> None:
    """Write report JSON to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


def _build_summary(matrix_rows: list[EvidenceMatrixRow]) -> str:
    if not matrix_rows:
        return "No sufficiency matrix rows were produced."

    counts: dict[str, int] = {}
    for row in matrix_rows:
        counts[row.sufficiency_status] = counts.get(row.sufficiency_status, 0) + 1

    parts = [f"{count} {status}" for status, count in sorted(counts.items())]
    return (
        f"Evidence sufficiency matrix completed for {len(matrix_rows)} control(s): "
        f"{', '.join(parts)}. All status labels are draft inference for human review."
    )
