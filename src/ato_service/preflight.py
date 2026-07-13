"""Deterministic preflight and export-readiness evaluation (Components A/B)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ato_service.analysis_profile import analysis_profile_sha256, load_pinned_fisma_synthetic_profile

PREFLIGHT_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class PreflightContext:
    package_revision_id: uuid.UUID
    profile_id: str
    status: str
    sealed_document: dict[str, Any] | None
    authority_manifest_id: str
    authority_manifest_sha256: str
    project_root: Any
    evaluated_at: datetime


def evaluate_preflight(context: PreflightContext) -> dict[str, Any]:
    """Return a schema-shaped preflight result for one package revision."""
    checks: list[dict[str, Any]] = []
    analysis_blockers: list[str] = []
    export_blockers: list[str] = []
    warnings: list[str] = []

    _add_check(
        checks,
        check_id="revision.ready",
        severity="analysis_blocker",
        outcome="passed" if context.status == "ready" else "failed",
        message="Package revision must be ready before analysis or export.",
    )
    if context.status != "ready":
        analysis_blockers.append("revision.ready")
        export_blockers.append("revision.ready")

    sealed_present = context.sealed_document is not None
    _add_check(
        checks,
        check_id="package.sealed_content",
        severity="analysis_blocker",
        outcome="passed" if sealed_present else "failed",
        message="Sealed package content must exist for ready revisions.",
    )
    if not sealed_present:
        analysis_blockers.append("package.sealed_content")
        export_blockers.append("package.sealed_content")

    document = context.sealed_document or {}
    assessor_inputs = document.get("assessor_inputs")
    has_assessor_inputs = isinstance(assessor_inputs, dict) and len(assessor_inputs) > 0
    _add_check(
        checks,
        check_id="assessor.inputs_present",
        severity="export_blocker",
        outcome="passed" if has_assessor_inputs else "failed",
        message="Imported assessor-owned inputs are required for export readiness.",
    )
    if not has_assessor_inputs:
        export_blockers.append("assessor.inputs_present")

    privacy = document.get("privacy")
    privacy_present = isinstance(privacy, dict) and privacy.get("artifacts_present") is True
    _add_check(
        checks,
        check_id="privacy.artifacts_present",
        severity="export_blocker",
        outcome="passed" if privacy_present else "failed",
        message="Required privacy artifacts must be attached before export claims.",
    )
    if not privacy_present:
        export_blockers.append("privacy.artifacts_present")

    profile_section = _profile_section(document=document, profile_id=context.profile_id)
    profile_populated = profile_section is not None and profile_section != {}
    _add_check(
        checks,
        check_id="profile.section_populated",
        severity="warning",
        outcome="passed" if profile_populated else "failed",
        message="Profile-specific section should be populated for submission prep.",
    )
    if not profile_populated:
        warnings.append("profile.section_populated")

    passed = sum(1 for check in checks if check["outcome"] == "passed")
    denominator = len(checks)
    readiness = {
        "numerator": passed,
        "denominator": denominator,
        "score": passed / denominator if denominator else 0.0,
    }
    analysis_eligible = not analysis_blockers
    export_eligible = not export_blockers

    profile = load_pinned_fisma_synthetic_profile(project_root=context.project_root)
    if context.profile_id != profile["profile_id"]:
        profile = {
            "profile_id": context.profile_id,
            "profile_version": "1.0.0",
            "assessment_items": [],
        }

    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "package_revision_id": str(context.package_revision_id).lower(),
        "analysis_eligible": analysis_eligible,
        "export_eligible": export_eligible,
        "readiness": readiness,
        "deterministic_checks": checks,
        "analysis_blockers": sorted(analysis_blockers),
        "export_blockers": sorted(export_blockers),
        "warnings": sorted(warnings),
        "authority_fingerprint": {
            "authority_manifest_id": context.authority_manifest_id,
            "sha256": context.authority_manifest_sha256,
        },
        "profile_fingerprint": {
            "profile_id": context.profile_id,
            "profile_version": profile.get("profile_version", "1.0.0"),
            "sha256": analysis_profile_sha256(profile),
        },
        "evaluated_at": _format_utc(context.evaluated_at),
    }


def _profile_section(*, document: dict[str, Any], profile_id: str) -> Any:
    if profile_id == "fedramp_20x_program":
        return document.get("fedramp_20x")
    if profile_id == "fedramp_rev5_transition":
        return document.get("fedramp_rev5_transition")
    return document.get("fisma_agency_security")


def _add_check(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    severity: str,
    outcome: str,
    message: str,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "severity": severity,
            "outcome": outcome,
            "message": message,
            "evidence_references": [],
        }
    )


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
