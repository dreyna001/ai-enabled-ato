"""FedRAMP Rev. 5 transition analysis profile compiler."""

from __future__ import annotations

from dataclasses import dataclass
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
    load_json_authority_source,
)
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.oscal_catalog import (
    OscalCatalogError,
    OscalControlRecord,
    index_oscal_catalog_controls,
    normalize_oscal_control_id as _normalize_oscal_control_id,
)

NIST_AUTHORITY_ID = "nist-sp800-53-release-5.2.0"
FEDRAMP_AUTHORITY_ID = "fedramp-consolidated-rules-2026"

CATALOG_MEMBER_SUFFIX = "NIST_SP-800-53_rev5_catalog-min.json"
BASELINE_MEMBER_SUFFIX_BY_IMPACT: dict[str, str] = {
    "low": "NIST_SP-800-53_rev5_LOW-baseline_profile-min.json",
    "moderate": "NIST_SP-800-53_rev5_MODERATE-baseline_profile-min.json",
    "high": "NIST_SP-800-53_rev5_HIGH-baseline_profile-min.json",
}

EXPECTED_BASELINE_CONTROL_COUNTS: dict[str, int] = {
    "low": 149,
    "moderate": 287,
    "high": 370,
}

_RECOGNIZED_IMPACT_LEVELS = frozenset(BASELINE_MEMBER_SUFFIX_BY_IMPACT)

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


class Rev5ProfileError(ValueError):
    """Raised when FedRAMP Rev. 5 transition profile compilation fails."""


@dataclass(frozen=True)
class _BaselineSelection:
    ordered_ids: list[str]
    membership_pointers: dict[str, str]


def normalize_oscal_control_id(control_id: str) -> str:
    """Normalize an OSCAL control identifier by uppercasing only."""
    try:
        return _normalize_oscal_control_id(control_id)
    except OscalCatalogError as exc:
        raise Rev5ProfileError(str(exc)) from exc


def extract_fedramp_rev5_transition_assessment_items(
    *,
    impact_level: str,
    catalog_document: dict[str, Any],
    baseline_document: dict[str, Any],
    catalog_archive_member: str = CATALOG_MEMBER_SUFFIX,
    baseline_archive_member: str | None = None,
) -> list[dict[str, Any]]:
    """Extract Rev. 5 transition assessment items for one impact baseline."""
    normalized_impact = _normalize_impact_level(impact_level)
    resolved_baseline_member = baseline_archive_member or BASELINE_MEMBER_SUFFIX_BY_IMPACT[
        normalized_impact
    ]
    try:
        catalog_index = index_oscal_catalog_controls(catalog_document)
    except OscalCatalogError as exc:
        raise Rev5ProfileError(str(exc)) from exc
    baseline_selection = _extract_baseline_selection(baseline_document)

    _enforce_baseline_control_count(
        baseline_selection.ordered_ids,
        impact_level=normalized_impact,
    )

    items: list[dict[str, Any]] = []
    for normalized_id in baseline_selection.ordered_ids:
        record = catalog_index.get(normalized_id)
        if record is None:
            raise Rev5ProfileError(
                f"baseline control {normalized_id!r} is missing from catalog index"
            )
        if not record.requirement_text.strip():
            raise Rev5ProfileError(
                f"baseline control {normalized_id!r} has malformed statement prose"
            )
        membership_pointer = baseline_selection.membership_pointers.get(normalized_id)
        if membership_pointer is None:
            raise Rev5ProfileError(
                f"baseline control {normalized_id!r} is missing membership pointer"
            )
        items.append(
            _build_assessment_item(
                record=record,
                impact_level=normalized_impact,
                catalog_archive_member=catalog_archive_member,
                baseline_archive_member=resolved_baseline_member,
                baseline_membership_pointer=membership_pointer,
            )
        )

    return items


def fedramp_rev5_transition_artifact_requirements() -> list[dict[str, Any]]:
    """Return required FedRAMP Rev. 5 transition import artifact requirements."""
    return [
        {
            "artifact_id": "ssp",
            "display_name": "Imported System Security Plan",
            "required": True,
            "owner": "provider",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["machine/ssp.json", "human/ssp.md"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/SDR/data/rev5/CSF/SDR-CSF-CTF",
                },
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/FRC/data/rev5/CSF/FRC-CSF-ACP",
                },
            ],
        },
        {
            "artifact_id": "sap",
            "display_name": "Imported Security Assessment Plan",
            "required": True,
            "owner": "provider",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["machine/sap.json", "human/sap.md"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/IVV/data/rev5/CSF/IVV-CSF-MCA",
                }
            ],
        },
        {
            "artifact_id": "sar",
            "display_name": "Imported Security Assessment Results",
            "required": True,
            "owner": "assessor",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["machine/sar.json", "human/sar.md"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/IVV/data/all/IAS/IVV-IAS-SUM",
                },
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/IVV/data/all/IAS/IVV-IAS-OSA",
                },
            ],
        },
        {
            "artifact_id": "poam",
            "display_name": "Imported Plan of Action and Milestones",
            "required": True,
            "owner": "provider",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["machine/poam.json", "human/poam.md"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/IVV/data/rev5/CSF/IVV-CSF-ACF",
                }
            ],
        },
        {
            "artifact_id": "oscal",
            "display_name": "Imported OSCAL Package",
            "required": False,
            "owner": "provider",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["machine/oscal.json", "human/oscal.md"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/FRC/data/rev5/CSF/FRC-CSF-FFG",
                }
            ],
        },
    ]


def compile_fedramp_rev5_transition_profile(
    *,
    impact_level: str,
    manifest_path: Path,
    project_root: Path,
    generated_at: datetime,
    profile_version: str = "1.0.0",
) -> dict[str, Any]:
    """Compile a draft FedRAMP Rev. 5 transition analysis profile."""
    normalized_impact = _normalize_impact_level(impact_level)
    root = project_root.resolve()
    resolved_manifest_path = manifest_path.resolve()

    try:
        manifest = verify_authority_manifest(
            resolved_manifest_path,
            project_root=root,
        )
        catalog_member_name, catalog_document = load_json_authority_archive_member(
            manifest=manifest,
            authority_id=NIST_AUTHORITY_ID,
            project_root=root,
            member_suffix=CATALOG_MEMBER_SUFFIX,
        )
        baseline_member_name, baseline_document = load_json_authority_archive_member(
            manifest=manifest,
            authority_id=NIST_AUTHORITY_ID,
            project_root=root,
            member_suffix=BASELINE_MEMBER_SUFFIX_BY_IMPACT[normalized_impact],
        )
    except AuthorityManifestVerificationError as exc:
        raise Rev5ProfileError(str(exc)) from exc
    except AuthorityCatalogError as exc:
        raise Rev5ProfileError(str(exc)) from exc

    try:
        assessment_items = extract_fedramp_rev5_transition_assessment_items(
            impact_level=normalized_impact,
            catalog_document=catalog_document,
            baseline_document=baseline_document,
            catalog_archive_member=catalog_member_name,
            baseline_archive_member=baseline_member_name,
        )
    except Rev5ProfileError:
        raise
    except (TypeError, ValueError) as exc:
        raise Rev5ProfileError(
            "failed to extract FedRAMP Rev. 5 transition assessment items"
        ) from exc

    identity = ProfileIdentity(
        profile_id="fedramp_rev5_transition",
        profile_version=profile_version,
        certification_class=None,
        impact_level=normalized_impact,
    )

    try:
        return compile_draft_analysis_profile(
            identity=identity,
            generated_at=generated_at,
            assessment_items=assessment_items,
            artifact_requirements=fedramp_rev5_transition_artifact_requirements(),
            cadence_rules=[],
            status_policy=_STATUS_POLICY,
            manifest_path=resolved_manifest_path,
            project_root=root,
        )
    except AnalysisProfileCompileError as exc:
        raise Rev5ProfileError(str(exc)) from exc


def _normalize_impact_level(impact_level: str) -> str:
    if not isinstance(impact_level, str) or not impact_level:
        raise Rev5ProfileError("impact_level is required")
    normalized = impact_level.strip().lower()
    if normalized not in _RECOGNIZED_IMPACT_LEVELS:
        raise Rev5ProfileError(
            f"unsupported impact_level {impact_level!r}; expected one of "
            f"{sorted(_RECOGNIZED_IMPACT_LEVELS)}"
        )
    return normalized


def _extract_baseline_selection(baseline_document: dict[str, Any]) -> _BaselineSelection:
    profile = baseline_document.get("profile")
    if not isinstance(profile, dict):
        raise Rev5ProfileError("baseline document must include profile")

    imports = profile.get("imports")
    if not isinstance(imports, list) or not imports:
        raise Rev5ProfileError("baseline profile must declare imports")

    ordered_ids: list[str] = []
    membership_pointers: dict[str, str] = {}
    seen_ids: set[str] = set()

    for import_index, import_entry in enumerate(imports):
        if not isinstance(import_entry, dict):
            raise Rev5ProfileError(
                f"baseline import at /profile/imports/{import_index} must be an object"
            )
        include_controls = import_entry.get("include-controls")
        if not isinstance(include_controls, list):
            continue

        for include_index, include_entry in enumerate(include_controls):
            if not isinstance(include_entry, dict):
                raise Rev5ProfileError(
                    "baseline include-controls entry at "
                    f"/profile/imports/{import_index}/include-controls/{include_index} "
                    "must be an object"
                )
            with_ids = include_entry.get("with-ids")
            if not isinstance(with_ids, list):
                raise Rev5ProfileError(
                    "baseline include-controls entry at "
                    f"/profile/imports/{import_index}/include-controls/{include_index} "
                    "must declare with-ids"
                )

            pointer_prefix = (
                f"/profile/imports/{import_index}/include-controls/"
                f"{include_index}/with-ids"
            )
            for with_id_index, raw_control_id in enumerate(with_ids):
                if not isinstance(raw_control_id, str) or not raw_control_id.strip():
                    raise Rev5ProfileError(
                        f"baseline control id at {pointer_prefix}/{with_id_index} "
                        "must be a non-empty string"
                    )
                normalized_id = normalize_oscal_control_id(raw_control_id)
                if normalized_id in seen_ids:
                    raise Rev5ProfileError(
                        f"duplicate baseline control id {normalized_id!r} at "
                        f"{pointer_prefix}/{with_id_index}"
                    )
                seen_ids.add(normalized_id)
                ordered_ids.append(normalized_id)
                membership_pointers[normalized_id] = (
                    f"{pointer_prefix}/{with_id_index}"
                )

    if not ordered_ids:
        raise Rev5ProfileError("baseline profile did not declare any control ids")
    return _BaselineSelection(
        ordered_ids=ordered_ids,
        membership_pointers=membership_pointers,
    )


def _build_assessment_item(
    *,
    record: OscalControlRecord,
    impact_level: str,
    catalog_archive_member: str,
    baseline_archive_member: str,
    baseline_membership_pointer: str,
) -> dict[str, Any]:
    return {
        "assessment_item_type": "nist_control",
        "assessment_item_id": record.normalized_id,
        "title": record.title,
        "requirement_text": record.requirement_text,
        "force": "MUST",
        "owner": "provider",
        "applicability": {
            "paths": ["rev5_transition"],
            "classes": [],
            "impact_levels": [impact_level],
            "affects": ["provider"],
        },
        "authority_refs": [
            {
                "authority_id": NIST_AUTHORITY_ID,
                "archive_member": catalog_archive_member,
                "source_pointer": record.catalog_pointer,
            },
            {
                "authority_id": NIST_AUTHORITY_ID,
                "archive_member": baseline_archive_member,
                "source_pointer": baseline_membership_pointer,
            },
        ],
        "required_evidence_kinds": [],
        "model_analysis_allowed": True,
    }


def _enforce_baseline_control_count(
    control_ids: list[str],
    *,
    impact_level: str,
) -> None:
    expected_count = EXPECTED_BASELINE_CONTROL_COUNTS[impact_level]
    unique_ids = set(control_ids)
    actual_count = len(unique_ids)
    if actual_count != expected_count:
        raise Rev5ProfileError(
            f"expected {expected_count} unique baseline controls for "
            f"{impact_level} impact, found {actual_count}"
        )
    if len(control_ids) != actual_count:
        raise Rev5ProfileError(
            f"baseline profile contains duplicate control ids for {impact_level} impact"
        )


__all__ = [
    "Rev5ProfileError",
    "compile_fedramp_rev5_transition_profile",
    "extract_fedramp_rev5_transition_assessment_items",
    "fedramp_rev5_transition_artifact_requirements",
    "normalize_oscal_control_id",
]
