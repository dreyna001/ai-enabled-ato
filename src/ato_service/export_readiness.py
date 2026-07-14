"""FedRAMP and export structural validation within hard-stop boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

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

    if profile_id == "fedramp_20x_program":
        section = sealed_document.get("fedramp_20x")
        if section is None:
            blockers.append("missing_fedramp_20x_section")
        else:
            warnings.extend(_validate_fedramp_payloads(section=section, project_root=project_root))

    if profile_id == "fedramp_rev5_transition":
        section = sealed_document.get("fedramp_rev5_transition")
        if section is None:
            blockers.append("missing_fedramp_rev5_section")

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


def _validate_fedramp_payloads(*, section: dict[str, Any], project_root: Path) -> list[str]:
    """Run vendored official schema structural checks; HS-001 blocks qualification claims."""
    warnings: list[str] = []
    schema_dir = project_root / "reference" / "authorities" / "fedramp"
    if not schema_dir.is_dir():
        warnings.append("authority_schemas_unavailable")
        return warnings

    for payload_key, schema_glob in (
        ("cpo_draft", "fedramp-certification-package-overview-schema-*.json"),
        ("sdr_draft", "fedramp-security-decision-record-schema-*.json"),
        ("ocr_draft", "fedramp-ongoing-certification-report-schema-*.json"),
    ):
        payload = section.get(payload_key)
        if payload is None:
            warnings.append(f"missing_{payload_key}")
            continue
        schema_path = _first_schema(schema_dir, schema_glob)
        if schema_path is None:
            warnings.append(f"schema_missing_for_{payload_key}")
            continue
        if not _validate_against_schema(payload=payload, schema_path=schema_path):
            warnings.append(f"structural_invalid_{payload_key}")
    return warnings


def _first_schema(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    return matches[0] if matches else None


def _validate_against_schema(*, payload: Any, schema_path: Path) -> bool:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    validator = Draft202012Validator(schema)
    return not any(True for _ in validator.iter_errors(payload))
