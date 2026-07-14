"""FedRAMP and export structural validation within hard-stop boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ato_service.fedramp_schema import evaluate_schema_purity


@dataclass(frozen=True, slots=True)
class ExportReadinessResult:
    """Deterministic export-readiness blockers without HS-001 qualification claims."""

    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    structural_checks_passed: bool


def evaluate_export_readiness(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    project_root: Path,
) -> ExportReadinessResult:
    """Evaluate structural export readiness for a sealed package document."""
    blockers: list[str] = []
    warnings: list[str] = []

    assessor_inputs = sealed_document.get("assessor_inputs")
    if not isinstance(assessor_inputs, dict) or not assessor_inputs:
        blockers.append("missing_assessor_inputs")

    privacy = sealed_document.get("privacy")
    if not isinstance(privacy, dict) or privacy.get("artifacts_present") is not True:
        blockers.append("missing_privacy_artifacts")

    warnings.extend(_authority_review_warnings(project_root=project_root))

    if profile_id == "fedramp_20x_program":
        blockers.extend(_fedramp_20x_blockers(sealed_document=sealed_document))
        warnings.extend(
            _fedramp_20x_warnings(
                sealed_document=sealed_document,
                project_root=project_root,
            )
        )
    elif profile_id == "fedramp_rev5_transition":
        blockers.extend(_fedramp_rev5_blockers(sealed_document=sealed_document))
        warnings.extend(_fedramp_rev5_warnings(sealed_document=sealed_document))

    return ExportReadinessResult(
        blockers=tuple(sorted(set(blockers))),
        warnings=tuple(sorted(set(warnings))),
        structural_checks_passed=not blockers,
    )


def _authority_review_warnings(*, project_root: Path) -> list[str]:
    """HS-001 blocks qualification claims; emit explicit readiness warnings."""
    manifest_path = project_root / "docs" / "contracts" / "authority-manifest.json"
    if not manifest_path.is_file():
        return ["authority_manifest_unavailable", "hs_001_authority_review_pending"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["authority_manifest_unavailable", "hs_001_authority_review_pending"]
    warnings = ["hs_001_authority_review_pending"]
    if manifest.get("status") != "draft":
        warnings = [warning for warning in warnings if warning != "hs_001_authority_review_pending"]
    for source in manifest.get("sources") or []:
        if isinstance(source, dict) and source.get("review_status") == "pending":
            warnings.append("hs_001_authority_review_pending")
            break
    return sorted(set(warnings))


def _fedramp_20x_blockers(*, sealed_document: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    section = sealed_document.get("fedramp_20x")
    if section is None:
        blockers.append("missing_fedramp_20x_section")
        return blockers

    independent_assessment = section.get("independent_assessment")
    if not isinstance(independent_assessment, dict) or not independent_assessment:
        blockers.append("hs_009_missing_independent_assessment")

    ksi_methods = section.get("ksi_methods")
    if not isinstance(ksi_methods, list) or not ksi_methods:
        blockers.append("missing_ksi_methods")

    return blockers


def _fedramp_20x_warnings(
    *,
    sealed_document: dict[str, Any],
    project_root: Path,
) -> list[str]:
    warnings: list[str] = []
    section = sealed_document.get("fedramp_20x")
    if not isinstance(section, dict):
        return warnings

    if not section.get("scg"):
        warnings.append("missing_scg_reference")

    metric_history = section.get("metric_history")
    if not isinstance(metric_history, list) or not metric_history:
        warnings.append("missing_metric_history")

    for result in evaluate_schema_purity(
        profile_id="fedramp_20x_program",
        sealed_document=sealed_document,
        project_root=project_root,
    ):
        if not result.schema_available:
            warnings.append(f"schema_unavailable_{result.artifact_key}")
            continue
        if not result.payload_present:
            warnings.append(f"missing_{result.artifact_key}")
            continue
        if not result.structurally_valid:
            warnings.append(f"structural_invalid_{result.artifact_key}")

    return warnings


def _fedramp_rev5_blockers(*, sealed_document: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    section = sealed_document.get("fedramp_rev5_transition")
    if section is None:
        blockers.append("missing_fedramp_rev5_section")
        return blockers
    if not isinstance(section, dict):
        blockers.append("missing_fedramp_rev5_section")
        return blockers

    for required_key in ("ssp", "sap", "sar", "poam"):
        payload = section.get(required_key)
        if not isinstance(payload, dict) or not payload:
            blockers.append(f"missing_rev5_{required_key}")
    return blockers


def _fedramp_rev5_warnings(*, sealed_document: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    section = sealed_document.get("fedramp_rev5_transition")
    if not isinstance(section, dict):
        return warnings

    oscal = section.get("oscal")
    if not isinstance(oscal, dict) or not oscal:
        warnings.append("missing_rev5_oscal")

    sar = section.get("sar")
    if isinstance(sar, dict) and sar:
        warnings.append("assessor_owned_sar_import_only")

    return warnings
