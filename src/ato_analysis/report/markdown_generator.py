"""Markdown report renderer and writer."""

from __future__ import annotations

from pathlib import Path

from ato_analysis.models.report_schema import AI_DISCLOSURE, EvidenceMatrixRow, ReportModel


def render_markdown(report: ReportModel) -> str:
    """Render an ISSO-readable markdown report."""
    lines: list[str] = [
        "# ATO Evidence Sufficiency Report",
        "",
        "## Summary",
        "",
        report.summary,
        "",
        "## AI Disclosure",
        "",
        report.ai_disclosure or AI_DISCLOSURE,
        "",
        "## Package Metadata",
        "",
        f"- **Package ID:** {report.package_metadata.package_id}",
        f"- **System:** {report.package_metadata.system_name}",
        f"- **Authorization path:** {report.package_metadata.authorization_path}",
        f"- **Baseline:** {report.package_metadata.baseline}",
        f"- **Impact level:** {report.package_metadata.impact_level}",
        f"- **Data classification:** {report.package_metadata.data_classification}",
        f"- **Assessment date:** {report.package_metadata.assessment_date}",
        f"- **Authorization boundary:** {report.package_metadata.authorization_boundary}",
        f"- **Controls:** {report.package_metadata.control_count}",
        f"- **Evidence items:** {report.package_metadata.evidence_count}",
        "",
        "## Pre-flight",
        "",
        f"- **Score:** {report.preflight.score}",
        f"- **Blocked:** {'Yes' if report.preflight.blocked else 'No'}",
        f"- **Metadata complete:** {'Yes' if report.preflight.metadata_complete else 'No'}",
        f"- **Controls non-empty:** {'Yes' if report.preflight.controls_non_empty else 'No'}",
        f"- **All controls have evidence:** "
        f"{'Yes' if report.preflight.all_controls_have_evidence else 'No'}",
        f"- **No broken links:** {'Yes' if report.preflight.no_broken_links else 'No'}",
        "",
    ]

    if report.preflight.warnings:
        lines.extend(["### Pre-flight warnings", ""])
        for warning in report.preflight.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if report.validation_warnings:
        lines.extend(["## Validation warnings", ""])
        for warning in report.validation_warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend(["## Evidence sufficiency matrix", ""])
    if not report.evidence_matrix:
        lines.append("_No matrix rows produced._")
        lines.append("")
    else:
        for row in report.evidence_matrix:
            lines.extend(_render_matrix_row(row))

    return "\n".join(lines)


def write_markdown_report(path: Path, report: ReportModel) -> None:
    """Write markdown report to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def _render_matrix_row(row: EvidenceMatrixRow) -> list[str]:
    lines = [
        f"### {row.control_id} — {row.sufficiency_status}",
        "",
        row.finding_summary,
        "",
    ]
    if row.gaps:
        lines.extend(["**Gaps:**", ""])
        for gap in row.gaps:
            lines.append(f"- {gap}")
        lines.append("")
    if row.stale_evidence_ids:
        lines.extend(
            [
                "**Stale evidence:** "
                + ", ".join(row.stale_evidence_ids),
                "",
            ]
        )
    if row.citations:
        lines.extend(["**Citations:**", ""])
        for citation in row.citations:
            lines.append(
                f"- `{citation.evidence_id}`: \"{citation.excerpt}\""
            )
        lines.append("")
    if row.assessor_questions:
        lines.extend(["**Assessor questions:**", ""])
        for question in row.assessor_questions:
            lines.append(f"- {question}")
        lines.append("")
    return lines
