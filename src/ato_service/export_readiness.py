"""FedRAMP and export structural validation within hard-stop boundaries."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ato_service.fedramp_schema import evaluate_schema_purity
from ato_service.fisma_template_pack import (
    FismaTemplatePackError,
    load_template_pack_reference,
    load_verified_template_pack,
)


@dataclass(frozen=True, slots=True)
class ExportReadinessResult:
    """Deterministic export-readiness blockers without HS-001 qualification claims."""

    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    structural_checks_passed: bool


EXPORT_BLOCKER_PORTAL_CODES: dict[str, str] = {
    "missing_assessor_inputs": "assessor.inputs_present",
    "missing_privacy_artifacts": "privacy.artifacts_present",
}


def portal_export_blocker_codes(blockers: tuple[str, ...]) -> tuple[str, ...]:
    """Map structural export blockers to portal preflight-style check ids."""
    mapped: list[str] = []
    for code in blockers:
        mapped.append(EXPORT_BLOCKER_PORTAL_CODES.get(code, code))
    return tuple(sorted(set(mapped)))


def export_readiness_payload(
    *,
    package_revision_id: uuid.UUID,
    profile_id: str,
    document: dict[str, Any],
    project_root: Path,
    runtime_config_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a portal-friendly export-readiness view for one package document."""
    readiness = evaluate_export_readiness(
        profile_id=profile_id,
        sealed_document=document,
        project_root=project_root,
        runtime_config_document=runtime_config_document,
    )
    return {
        "schema_version": "1.0.0",
        "package_revision_id": str(package_revision_id).lower(),
        "profile_id": profile_id,
        "export_eligible": not readiness.blockers,
        "export_blockers": list(portal_export_blocker_codes(readiness.blockers)),
        "warnings": list(readiness.warnings),
        "structural_checks_passed": readiness.structural_checks_passed,
    }


def evaluate_export_readiness(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    project_root: Path,
    runtime_config_document: dict[str, Any] | None = None,
) -> ExportReadinessResult:
    """Evaluate structural export readiness for a sealed package document."""
    blockers: list[str] = []
    warnings: list[str] = []

    if profile_id == "fisma_agency_security":
        warnings.extend(
            _evaluate_fisma_security_readiness(
                sealed_document=sealed_document,
                runtime_config_document=runtime_config_document,
            )
        )
        return ExportReadinessResult(
            blockers=tuple(sorted(set(blockers))),
            warnings=tuple(sorted(set(warnings))),
            structural_checks_passed=True,
        )

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


def _evaluate_fisma_security_readiness(
    *,
    sealed_document: dict[str, Any],
    runtime_config_document: dict[str, Any] | None,
) -> list[str]:
    """Return FISMA security-only readiness warnings without privacy execution claims."""
    warnings: list[str] = []
    section = sealed_document.get("fisma_agency_security")
    if not isinstance(section, dict) or not section:
        warnings.append("missing_fisma_agency_security_section")

    privacy = sealed_document.get("privacy")
    if not isinstance(privacy, dict) or not privacy.get("scope_notice"):
        warnings.append("missing_privacy_scope_notice")

    try:
        reference = load_template_pack_reference(runtime_config_document)
    except FismaTemplatePackError:
        warnings.append("hs002_template_pack_invalid_reference")
        return warnings

    if reference is None:
        warnings.append("hs002_template_pack_unavailable")
        return warnings

    try:
        pack = load_verified_template_pack(reference)
    except FismaTemplatePackError:
        warnings.append("hs002_template_pack_digest_or_archive_invalid")
        return warnings

    if pack.approval_status != "approved":
        warnings.append("hs002_template_pack_unapproved")
    return warnings


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
