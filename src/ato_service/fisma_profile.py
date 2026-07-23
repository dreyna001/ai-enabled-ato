"""Agency FISMA security analysis profile compiler."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ato_service.analysis_profile_compiler import (
    AnalysisProfileCompileError,
    ProfileIdentity,
    compile_draft_analysis_profile,
)
from ato_service.authority_catalog import (
    AuthorityCatalogError,
    load_json_authority_archive_member,
)
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.fisma_control_inventory import (
    FismaControlInventory,
    privacy_family_prefixes_from_catalog,
)
from ato_service.oscal_catalog import (
    OscalCatalogError,
    OscalControlRecord,
    index_oscal_catalog_controls,
)

NIST_AUTHORITY_ID = "nist-sp800-53-release-5.2.0"
CATALOG_MEMBER_SUFFIX = "NIST_SP-800-53_rev5_catalog-min.json"

_STATUS_POLICY: dict[str, Any] = {
    "allowed_statuses": [
        "supported",
        "partial",
        "unsupported",
        "insufficient_evidence",
    ],
    "no_evidence_status": "insufficient_evidence",
    "incomplete_context_ceiling": "partial",
    "all_stale_ceiling": "partial",
    "system_may_be_more_favorable_than_model": False,
    "exact_row_coverage_required": True,
    "repair_attempts": 1,
}

_FISMA_APPLICABILITY_PATH = "agency_fisma"
_FISMA_APPLICABILITY_AFFECTS = ("system_owner",)
_RECOGNIZED_INVENTORY_STATUSES = frozenset({"draft", "approved"})


class FismaProfileError(ValueError):
    """Raised when agency FISMA security profile compilation fails."""


def extract_fisma_agency_security_assessment_items(
    *,
    inventory: FismaControlInventory,
    catalog_document: dict[str, Any],
    catalog_archive_member: str,
) -> list[dict[str, Any]]:
    """Extract FISMA assessment items for one customer control inventory."""
    if not catalog_archive_member:
        raise FismaProfileError("catalog_archive_member is required")

    try:
        catalog_index = index_oscal_catalog_controls(catalog_document)
    except OscalCatalogError as exc:
        raise FismaProfileError(str(exc)) from exc

    control_ids = inventory.control_ids
    if not control_ids:
        raise FismaProfileError("inventory must declare at least one control id")

    privacy_prefixes = _privacy_family_prefixes_from_catalog_or_raise(catalog_document)

    items: list[dict[str, Any]] = []
    for control_id in control_ids:
        _reject_privacy_control_id(control_id, privacy_prefixes=privacy_prefixes)
        record = catalog_index.get(control_id)
        if record is None:
            raise FismaProfileError(
                f"inventory control {control_id!r} is missing from catalog index"
            )
        if not record.title.strip():
            raise FismaProfileError(
                f"inventory control {control_id!r} has empty catalog title"
            )
        if not record.requirement_text.strip():
            raise FismaProfileError(
                f"inventory control {control_id!r} has malformed statement prose"
            )
        items.append(
            _build_assessment_item(
                record=record,
                impact_level=inventory.impact_level,
                catalog_archive_member=catalog_archive_member,
            )
        )

    if len(items) != len(control_ids):
        raise FismaProfileError(
            "assessment item count must exactly match inventory control ids"
        )
    return items


def fisma_agency_artifact_requirements() -> list[dict[str, Any]]:
    """Return required agency FISMA security export artifact requirements."""
    return [
        {
            "artifact_id": "ssp_security_draft",
            "display_name": "SSP Security Draft",
            "required": True,
            "owner": "system_owner",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": [
                "human/ssp-security-draft.md",
                "machine/ssp-security-draft.json",
            ],
            "authority_refs": [],
        },
        {
            "artifact_id": "sar_input_pack",
            "display_name": "SAR Input Pack",
            "required": True,
            "owner": "assessor",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": [
                "human/sar-input-pack.md",
                "machine/sar-input-pack.json",
            ],
            "authority_refs": [],
        },
        {
            "artifact_id": "poam_draft",
            "display_name": "POA&M Draft",
            "required": True,
            "owner": "system_owner",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": [
                "human/poam-draft.md",
                "machine/poam-draft.json",
            ],
            "authority_refs": [],
        },
        {
            "artifact_id": "security_readiness_matrix",
            "display_name": "Security Readiness and Assessment Matrix",
            "required": True,
            "owner": "product_analysis",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": [
                "human/security-readiness.md",
                "machine/security-readiness.json",
                "human/assessment-matrix.md",
                "validation/fisma-export-readiness.json",
            ],
            "authority_refs": [],
        },
    ]


def compile_fisma_agency_security_profile(
    *,
    inventory: FismaControlInventory,
    manifest_path: Path,
    project_root: Path,
    generated_at: datetime,
    profile_version: str = "1.0.0",
) -> dict[str, Any]:
    """Compile a draft agency FISMA security analysis profile."""
    _validate_inventory_status(inventory)

    root = project_root.resolve()
    resolved_manifest_path = manifest_path.resolve()

    try:
        manifest = verify_authority_manifest(
            resolved_manifest_path,
            project_root=root,
        )
    except AuthorityManifestVerificationError as exc:
        raise FismaProfileError(str(exc)) from exc
    except AuthorityCatalogError as exc:
        raise FismaProfileError(str(exc)) from exc

    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id:
        raise FismaProfileError(
            "verified authority manifest must declare manifest_id"
        )
    if inventory.authority_manifest_id != manifest_id:
        raise FismaProfileError(
            "inventory authority_manifest_id "
            f"{inventory.authority_manifest_id!r} does not match authority manifest "
            f"manifest_id {manifest_id!r}"
        )

    try:
        catalog_member_name, catalog_document = load_json_authority_archive_member(
            manifest=manifest,
            authority_id=NIST_AUTHORITY_ID,
            project_root=root,
            member_suffix=CATALOG_MEMBER_SUFFIX,
        )
    except AuthorityCatalogError as exc:
        raise FismaProfileError(str(exc)) from exc

    try:
        assessment_items = extract_fisma_agency_security_assessment_items(
            inventory=inventory,
            catalog_document=catalog_document,
            catalog_archive_member=catalog_member_name,
        )
    except FismaProfileError:
        raise
    except (TypeError, ValueError) as exc:
        raise FismaProfileError(
            "failed to extract agency FISMA security assessment items"
        ) from exc

    identity = ProfileIdentity(
        profile_id="fisma_agency_security",
        profile_version=profile_version,
        certification_class=None,
        impact_level=inventory.impact_level,
    )

    try:
        return compile_draft_analysis_profile(
            identity=identity,
            generated_at=generated_at,
            assessment_items=assessment_items,
            artifact_requirements=fisma_agency_artifact_requirements(),
            cadence_rules=[],
            status_policy=_STATUS_POLICY,
            manifest_path=resolved_manifest_path,
            project_root=root,
        )
    except AnalysisProfileCompileError as exc:
        raise FismaProfileError(str(exc)) from exc


def _validate_inventory_status(inventory: FismaControlInventory) -> None:
    if inventory.status not in _RECOGNIZED_INVENTORY_STATUSES:
        raise FismaProfileError(
            f"unsupported inventory status {inventory.status!r}; expected draft or approved"
        )


def _privacy_family_prefixes_from_catalog_or_raise(
    catalog_document: dict[str, Any],
) -> frozenset[str]:
    prefixes = privacy_family_prefixes_from_catalog(catalog_document)
    if prefixes is None:
        raise FismaProfileError(
            "NIST catalog does not declare a recognizable privacy control family "
            "namespace"
        )
    return prefixes


def _reject_privacy_control_id(
    control_id: str,
    *,
    privacy_prefixes: frozenset[str],
) -> None:
    family_prefix = control_id.split("-", 1)[0]
    if family_prefix in privacy_prefixes:
        raise FismaProfileError(
            f"privacy-family control_id {control_id!r} is out of scope for "
            "agency FISMA security profiles"
        )


def _build_assessment_item(
    *,
    record: OscalControlRecord,
    impact_level: str,
    catalog_archive_member: str,
) -> dict[str, Any]:
    return {
        "assessment_item_type": "nist_control",
        "assessment_item_id": record.normalized_id,
        "title": record.title,
        "requirement_text": record.requirement_text,
        "force": "customer_required",
        "owner": "system_owner",
        "applicability": {
            "paths": [_FISMA_APPLICABILITY_PATH],
            "classes": [],
            "impact_levels": [impact_level],
            "affects": list(_FISMA_APPLICABILITY_AFFECTS),
        },
        "authority_refs": [
            {
                "authority_id": NIST_AUTHORITY_ID,
                "archive_member": catalog_archive_member,
                "source_pointer": record.catalog_pointer,
            }
        ],
        "required_evidence_kinds": [],
        "model_analysis_allowed": False,
    }


__all__ = [
    "CATALOG_MEMBER_SUFFIX",
    "FismaProfileError",
    "NIST_AUTHORITY_ID",
    "compile_fisma_agency_security_profile",
    "extract_fisma_agency_security_assessment_items",
    "fisma_agency_artifact_requirements",
]
