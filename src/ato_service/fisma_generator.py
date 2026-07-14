"""Deterministic agency FISMA security-only draft artifact generator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ato_service.fisma_template_pack import (
    LoadedFismaTemplatePack,
    is_template_pack_rendering_eligible,
    render_human_template,
)

PRIVACY_SCOPE_NOTICE = (
    "Privacy artifacts and privacy-control assessment are outside this product "
    "scope and must be completed in the customer's authorization process."
)
GENERIC_DRAFT_NOTICE = (
    "DRAFT — Generic agency FISMA security artifact. HS-002 template pack "
    "unavailable or unapproved; no agency field parity claimed."
)
TEMPLATE_PACK_NOTICE = (
    "DRAFT — Rendered from a digest-verified customer template pack. "
    "No agency field parity or customer-ready export is claimed."
)

FISMA_EXPORT_PATHS: tuple[str, ...] = (
    "human/ssp-security-draft.md",
    "machine/ssp-security-draft.json",
    "human/sar-input-pack.md",
    "machine/sar-input-pack.json",
    "human/poam-draft.md",
    "machine/poam-draft.json",
    "human/security-readiness.md",
    "machine/security-readiness.json",
    "human/assessment-matrix.md",
    "validation/fisma-export-readiness.json",
)


@dataclass(frozen=True, slots=True)
class FismaGenerationResult:
    contents: dict[str, str]
    readiness_blockers: tuple[str, ...]
    readiness_warnings: tuple[str, ...]
    rendering_mode: str
    provenance: dict[str, Any]


def generate_fisma_security_artifacts(
    *,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]] | None,
    matrix_rows: list[dict[str, Any]] | None,
    template_pack: LoadedFismaTemplatePack | None = None,
) -> FismaGenerationResult:
    """Generate deterministic FISMA security-only draft artifacts and provenance."""
    blockers: list[str] = []
    warnings: list[str] = []
    rendering_mode = "generic_draft"
    mapped_fields: dict[str, dict[str, Any]] = {}

    if template_pack is None:
        blockers.append("hs002_template_pack_unavailable")
        warnings.append("generic_draft_shape_only")
    elif not is_template_pack_rendering_eligible(template_pack):
        blockers.append("hs002_template_pack_unapproved")
        warnings.append("generic_draft_shape_only")
    else:
        rendering_mode = "template_pack"
        mapped_fields = _apply_template_mappings(
            sealed_document=sealed_document,
            template_pack=template_pack,
        )

    provenance = _build_provenance(
        sealed_document=sealed_document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions or [],
        matrix_rows=matrix_rows or [],
        template_pack=template_pack,
        rendering_mode=rendering_mode,
    )

    ssp_machine, ssp_human = _build_ssp_security_draft(
        sealed_document=sealed_document,
        mapped_fields=mapped_fields.get("ssp_security_draft", {}),
        template_pack=template_pack,
        rendering_mode=rendering_mode,
    )
    sar_machine, sar_human = _build_sar_input_pack(
        sealed_document=sealed_document,
        rendering_mode=rendering_mode,
    )
    poam_machine, poam_human = _build_poam_draft(
        dispositions=dispositions or [],
        matrix_rows=matrix_rows or [],
        rendering_mode=rendering_mode,
    )
    readiness_machine, readiness_human = _build_security_readiness(
        sealed_document=sealed_document,
        blockers=blockers,
        warnings=warnings,
        rendering_mode=rendering_mode,
        template_pack=template_pack,
        provenance=provenance,
    )
    matrix_human = _build_assessment_matrix_markdown(matrix_rows=matrix_rows or [])

    validation_payload = {
        "schema_version": "1.0.0",
        "profile_id": "fisma_agency_security",
        "rendering_mode": rendering_mode,
        "agency_parity_claimed": False,
        "privacy_scope_notice": PRIVACY_SCOPE_NOTICE,
        "readiness_blockers": sorted(set(blockers)),
        "readiness_warnings": sorted(set(warnings)),
        "template_pack": _template_pack_metadata(template_pack),
        "provenance": provenance,
    }

    contents = {
        "human/ssp-security-draft.md": ssp_human,
        "machine/ssp-security-draft.json": _json_text(ssp_machine),
        "human/sar-input-pack.md": sar_human,
        "machine/sar-input-pack.json": _json_text(sar_machine),
        "human/poam-draft.md": poam_human,
        "machine/poam-draft.json": _json_text(poam_machine),
        "human/security-readiness.md": readiness_human,
        "machine/security-readiness.json": _json_text(readiness_machine),
        "human/assessment-matrix.md": matrix_human,
        "validation/fisma-export-readiness.json": _json_text(validation_payload),
    }
    return FismaGenerationResult(
        contents=contents,
        readiness_blockers=tuple(sorted(set(blockers))),
        readiness_warnings=tuple(sorted(set(warnings))),
        rendering_mode=rendering_mode,
        provenance=provenance,
    )


def _apply_template_mappings(
    *,
    sealed_document: dict[str, Any],
    template_pack: LoadedFismaTemplatePack,
) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for mapping in template_pack.manifest.get("field_mappings", []):
        if not isinstance(mapping, dict):
            continue
        source_pointer = mapping.get("source_pointer")
        target_artifact = mapping.get("target_artifact")
        target_field = mapping.get("target_field")
        if not isinstance(source_pointer, str):
            continue
        if not isinstance(target_artifact, str) or not isinstance(target_field, str):
            continue
        value = _value_at_json_pointer(sealed_document, source_pointer)
        if value is None:
            continue
        mapped.setdefault(target_artifact, {})[target_field] = value
    return mapped


def _build_ssp_security_draft(
    *,
    sealed_document: dict[str, Any],
    mapped_fields: dict[str, Any],
    template_pack: LoadedFismaTemplatePack | None,
    rendering_mode: str,
) -> tuple[dict[str, Any], str]:
    system = sealed_document.get("system") if isinstance(sealed_document.get("system"), dict) else {}
    package = sealed_document.get("package") if isinstance(sealed_document.get("package"), dict) else {}
    controls = sealed_document.get("security_controls")
    if not isinstance(controls, dict):
        controls = {}

    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "rendering_mode": rendering_mode,
        "agency_parity_claimed": False,
        "privacy_scope_notice": PRIVACY_SCOPE_NOTICE,
        "system_name": mapped_fields.get("system_name", system.get("display_name", "")),
        "authorization_boundary": mapped_fields.get(
            "authorization_boundary",
            system.get("authorization_boundary", ""),
        ),
        "mission_summary": mapped_fields.get("mission_summary", system.get("mission_summary", "")),
        "package_title": mapped_fields.get("package_title", package.get("title", "")),
        "impact_level": system.get("impact_level"),
        "security_controls": {
            control_id: {
                "implementation_status": entry.get("implementation_status"),
                "implementation_statement": entry.get("implementation_statement", ""),
            }
            for control_id, entry in sorted(controls.items())
            if isinstance(entry, dict)
        },
    }

    if rendering_mode == "template_pack" and template_pack is not None:
        rendered = render_human_template(
            pack=template_pack,
            artifact_id="ssp_security_draft",
            field_values={
                key: value
                for key, value in payload.items()
                if isinstance(value, (str, int, float, bool)) or value is None
            },
        )
        if rendered is not None:
            return payload, rendered

    lines = [
        "# SSP Security Draft",
        "",
        TEMPLATE_PACK_NOTICE if rendering_mode == "template_pack" else GENERIC_DRAFT_NOTICE,
        "",
        f"System: {payload['system_name']}",
        f"Boundary: {payload['authorization_boundary']}",
        f"Mission: {payload['mission_summary']}",
        f"Impact level: {payload['impact_level']}",
        "",
        PRIVACY_SCOPE_NOTICE,
        "",
        "## Security controls",
    ]
    for control_id, entry in payload["security_controls"].items():
        lines.append(f"- {control_id}: {entry['implementation_status']} — {entry['implementation_statement']}")
    return payload, "\n".join(lines) + "\n"


def _build_sar_input_pack(
    *,
    sealed_document: dict[str, Any],
    rendering_mode: str,
) -> tuple[dict[str, Any], str]:
    assessor_inputs = sealed_document.get("assessor_inputs")
    if not isinstance(assessor_inputs, dict):
        assessor_inputs = {}
    payload = {
        "schema_version": "1.0.0",
        "rendering_mode": rendering_mode,
        "agency_parity_claimed": False,
        "privacy_scope_notice": PRIVACY_SCOPE_NOTICE,
        "assessor_inputs": assessor_inputs,
        "official_signed_sar_claimed": False,
    }
    lines = [
        "# SAR Input Pack",
        "",
        GENERIC_DRAFT_NOTICE if rendering_mode == "generic_draft" else TEMPLATE_PACK_NOTICE,
        "",
        "Imported assessor-owned inputs only. This product does not produce an official signed SAR.",
        "",
        PRIVACY_SCOPE_NOTICE,
        "",
        "## Assessor inputs",
        _json_text(assessor_inputs),
    ]
    return payload, "\n".join(lines) + "\n"


def _build_poam_draft(
    *,
    dispositions: list[dict[str, Any]],
    matrix_rows: list[dict[str, Any]],
    rendering_mode: str,
) -> tuple[dict[str, Any], str]:
    row_by_id = {
        str(row.get("matrix_row_id", "")).lower(): row
        for row in matrix_rows
        if isinstance(row, dict)
    }
    confirmed: list[dict[str, Any]] = []
    for disposition in sorted(dispositions, key=lambda item: str(item.get("matrix_row_id", ""))):
        if disposition.get("decision") != "weakness_confirmed":
            continue
        row = row_by_id.get(str(disposition.get("matrix_row_id", "")).lower(), {})
        confirmed.append(
            {
                "matrix_row_id": disposition.get("matrix_row_id"),
                "assessment_item_id": row.get("assessment_item_id"),
                "system_status": row.get("system_status"),
                "finding_summary": disposition.get("edited_summary") or row.get("finding_summary"),
                "decided_by": disposition.get("decided_by"),
                "decided_at": disposition.get("decided_at"),
            }
        )
    payload = {
        "schema_version": "1.0.0",
        "rendering_mode": rendering_mode,
        "agency_parity_claimed": False,
        "privacy_scope_notice": PRIVACY_SCOPE_NOTICE,
        "human_confirmed_weaknesses": confirmed,
    }
    lines = [
        "# POA&M Draft",
        "",
        GENERIC_DRAFT_NOTICE if rendering_mode == "generic_draft" else TEMPLATE_PACK_NOTICE,
        "",
        "Only human-confirmed weaknesses are listed. No authorization decision is made.",
        "",
        PRIVACY_SCOPE_NOTICE,
        "",
        "## Confirmed weaknesses",
    ]
    if not confirmed:
        lines.append("- None")
    else:
        for item in confirmed:
            lines.append(
                f"- {item['assessment_item_id']}: {item['finding_summary']} "
                f"(confirmed by {item['decided_by']} at {item['decided_at']})"
            )
    return payload, "\n".join(lines) + "\n"


def _build_security_readiness(
    *,
    sealed_document: dict[str, Any],
    blockers: list[str],
    warnings: list[str],
    rendering_mode: str,
    template_pack: LoadedFismaTemplatePack | None,
    provenance: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    controls = sealed_document.get("security_controls")
    control_count = len(controls) if isinstance(controls, dict) else 0
    payload = {
        "schema_version": "1.0.0",
        "rendering_mode": rendering_mode,
        "agency_parity_claimed": False,
        "privacy_scope_notice": PRIVACY_SCOPE_NOTICE,
        "security_control_count": control_count,
        "readiness_blockers": sorted(set(blockers)),
        "readiness_warnings": sorted(set(warnings)),
        "template_pack": _template_pack_metadata(template_pack),
        "provenance": provenance,
    }
    lines = [
        "# Security Readiness",
        "",
        GENERIC_DRAFT_NOTICE if rendering_mode == "generic_draft" else TEMPLATE_PACK_NOTICE,
        "",
        PRIVACY_SCOPE_NOTICE,
        "",
        f"Security controls present: {control_count}",
        "",
        "## Readiness blockers",
    ]
    if blockers:
        lines.extend(f"- {item}" for item in sorted(set(blockers)))
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Readiness warnings")
    if warnings:
        lines.extend(f"- {item}" for item in sorted(set(warnings)))
    else:
        lines.append("- None")
    return payload, "\n".join(lines) + "\n"


def _build_assessment_matrix_markdown(*, matrix_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Assessment Matrix",
        "",
        GENERIC_DRAFT_NOTICE,
        "",
        "| Control | Status | Summary |",
        "| --- | --- | --- |",
    ]
    for row in sorted(matrix_rows, key=lambda item: str(item.get("assessment_item_id", ""))):
        lines.append(
            f"| {row.get('assessment_item_id', '')} | {row.get('system_status', '')} | "
            f"{row.get('finding_summary', '')} |"
        )
    if not matrix_rows:
        lines.append("| — | — | — |")
    return "\n".join(lines) + "\n"


def _build_provenance(
    *,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]],
    matrix_rows: list[dict[str, Any]],
    template_pack: LoadedFismaTemplatePack | None,
    rendering_mode: str,
) -> dict[str, Any]:
    sealed_digest = hashlib.sha256(
        json.dumps(sealed_document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    disposition_digest = hashlib.sha256(
        json.dumps({"dispositions": dispositions}, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    matrix_digest = hashlib.sha256(
        json.dumps({"rows": matrix_rows}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "review_revision_id": str(review_revision_id).lower(),
        "run_id": str(run_id).lower(),
        "sealed_document_sha256": sealed_digest,
        "dispositions_sha256": disposition_digest,
        "matrix_rows_sha256": matrix_digest,
        "rendering_mode": rendering_mode,
        "template_pack": _template_pack_metadata(template_pack),
    }


def _template_pack_metadata(pack: LoadedFismaTemplatePack | None) -> dict[str, Any] | None:
    if pack is None:
        return None
    return {
        "pack_id": pack.pack_id,
        "pack_version": pack.pack_version,
        "approval_status": pack.approval_status,
        "archive_sha256": pack.archive_sha256,
    }


def _value_at_json_pointer(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    for raw_part in pointer.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            return None
    return current


def _json_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "FISMA_EXPORT_PATHS",
    "FismaGenerationResult",
    "GENERIC_DRAFT_NOTICE",
    "PRIVACY_SCOPE_NOTICE",
    "generate_fisma_security_artifacts",
]
