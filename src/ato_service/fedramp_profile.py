"""FedRAMP 20x Program Class C analysis profile compiler."""

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
    load_json_authority_source,
)
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)

FEDRAMP_AUTHORITY_ID = "fedramp-consolidated-rules-2026"
CPO_SCHEMA_AUTHORITY_ID = "fedramp-schema-cpo-2026-06-24"
SDR_SCHEMA_AUTHORITY_ID = "fedramp-schema-sdr-2026-06-24"
OCR_SCHEMA_AUTHORITY_ID = "fedramp-schema-ocr-2026-06-24"

EXPECTED_FRR_RULE_COUNT = 155
EXPECTED_KSI_COUNT = 46
EXPECTED_ASSESSMENT_ITEM_COUNT = 201

KSI_VVK_PARENT_POINTER = "/FRR/FRC/data/20x/CSX/FRC-CSX-VVK"
KSI_MOT_PARENT_POINTER = "/FRR/FRC/data/20x/CSX/FRC-CSX-MOT"
KSI_AIA_PARENT_POINTER = "/FRR/IVV/data/20x/CSX/IVV-CSX-AIA"

_RECOGNIZED_FORCES = frozenset({"MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY"})
_FORCE_NORMALIZATION = {"MUST NOT": "MUST_NOT", "SHOULD NOT": "SHOULD_NOT"}

_CLASS_C_APPLICABILITY: dict[str, Any] = {
    "paths": ["program"],
    "classes": ["C"],
    "impact_levels": [],
    "affects": ["provider"],
}

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


class FedrampProfileError(ValueError):
    """Raised when FedRAMP 20x Class C profile compilation fails."""


def extract_fedramp_20x_class_c_assessment_items(
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract Class C Program provider assessment items from consolidated rules."""
    frr = document.get("FRR")
    if not isinstance(frr, dict):
        raise FedrampProfileError("consolidated rules document must include FRR")

    ksi_root = document.get("KSI")
    if not isinstance(ksi_root, dict):
        raise FedrampProfileError("consolidated rules document must include KSI")

    items_by_id: dict[str, dict[str, Any]] = {}
    frr_items = _extract_frr_assessment_items(frr)
    for item in frr_items:
        item_id = item["assessment_item_id"]
        if item_id in items_by_id:
            raise FedrampProfileError(
                f"duplicate assessment_item_id {item_id!r} while extracting FRR rules"
            )
        items_by_id[item_id] = item

    ksi_items = _extract_ksi_assessment_items(ksi_root)
    for item in ksi_items:
        item_id = item["assessment_item_id"]
        if item_id in items_by_id:
            raise FedrampProfileError(
                f"duplicate assessment_item_id {item_id!r} while extracting KSI indicators"
            )
        items_by_id[item_id] = item

    items = [items_by_id[item_id] for item_id in sorted(items_by_id)]
    _enforce_assessment_item_counts(items)
    return items


def fedramp_20x_artifact_requirements() -> list[dict[str, Any]]:
    """Return required FedRAMP 20x Program Class C artifact requirements."""
    return [
        {
            "artifact_id": "cpo",
            "display_name": "Certification Package Overview",
            "required": True,
            "owner": "provider",
            "official_schema_authority_id": CPO_SCHEMA_AUTHORITY_ID,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["human/cpo.md", "machine/cpo.json"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/CPO/data/all/CSO/CPO-CSO-OVR",
                }
            ],
        },
        {
            "artifact_id": "sdr",
            "display_name": "Security Decision Record",
            "required": True,
            "owner": "provider",
            "official_schema_authority_id": SDR_SCHEMA_AUTHORITY_ID,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["human/sdr.md", "machine/sdr.json"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/SDR/data/all/CSO/SDR-CSO-FRR",
                }
            ],
        },
        {
            "artifact_id": "ocr",
            "display_name": "Ongoing Certification Report",
            "required": True,
            "owner": "provider",
            "official_schema_authority_id": OCR_SCHEMA_AUTHORITY_ID,
            "human_readable_required": True,
            "machine_readable_required": True,
            "export_paths": ["human/ocr.md", "machine/ocr.json"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/CCM/data/all/OCR/CCM-OCR-AVL",
                }
            ],
        },
        {
            "artifact_id": "scg",
            "display_name": "Secure Configuration Guide",
            "required": True,
            "owner": "provider",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": False,
            "export_paths": ["human/scg-readiness.md"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/SCG/data/all/CSO/SCG-CSO-RSC",
                },
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/SCG/data/all/CSO/SCG-CSO-AUP",
                },
            ],
        },
        {
            "artifact_id": "independent_assessment",
            "display_name": "FedRAMP Independent Assessment Results",
            "required": True,
            "owner": "assessor",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": False,
            "export_paths": ["provenance/assessor-imports.json"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/IVV/data/all/CSO/IVV-CSO-ICP",
                },
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": "/FRR/IVV/data/all/IAS/IVV-IAS-OSA",
                },
            ],
        },
        {
            "artifact_id": "ksi_metric_material",
            "display_name": "Key Security Indicator Methods and Metric History",
            "required": True,
            "owner": "shared",
            "official_schema_authority_id": None,
            "human_readable_required": True,
            "machine_readable_required": False,
            "export_paths": ["human/ksi-summary.md", "machine/ksi-summary.json"],
            "authority_refs": [
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": KSI_VVK_PARENT_POINTER,
                },
                {
                    "authority_id": FEDRAMP_AUTHORITY_ID,
                    "source_pointer": KSI_MOT_PARENT_POINTER,
                },
            ],
        },
    ]


def compile_fedramp_20x_class_c_profile(
    *,
    manifest_path: Path,
    project_root: Path,
    generated_at: datetime,
    profile_version: str = "1.0.0",
) -> dict[str, Any]:
    """Compile a draft FedRAMP 20x Program Class C analysis profile."""
    root = project_root.resolve()
    resolved_manifest_path = manifest_path.resolve()

    try:
        manifest = verify_authority_manifest(
            resolved_manifest_path,
            project_root=root,
        )
        document = load_json_authority_source(
            manifest=manifest,
            authority_id=FEDRAMP_AUTHORITY_ID,
            project_root=root,
        )
    except AuthorityManifestVerificationError as exc:
        raise FedrampProfileError(str(exc)) from exc
    except AuthorityCatalogError as exc:
        raise FedrampProfileError(str(exc)) from exc

    try:
        assessment_items = extract_fedramp_20x_class_c_assessment_items(document)
    except FedrampProfileError:
        raise
    except (TypeError, ValueError) as exc:
        raise FedrampProfileError(
            "failed to extract FedRAMP 20x Class C assessment items"
        ) from exc

    identity = ProfileIdentity(
        profile_id="fedramp_20x_program",
        profile_version=profile_version,
        certification_class="C",
        impact_level=None,
    )

    try:
        return compile_draft_analysis_profile(
            identity=identity,
            generated_at=generated_at,
            assessment_items=assessment_items,
            artifact_requirements=fedramp_20x_artifact_requirements(),
            cadence_rules=[],
            status_policy=_STATUS_POLICY,
            manifest_path=resolved_manifest_path,
            project_root=root,
        )
    except AnalysisProfileCompileError as exc:
        raise FedrampProfileError(str(exc)) from exc


def _extract_frr_assessment_items(frr: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for family_key, family in frr.items():
        if not isinstance(family, dict):
            raise FedrampProfileError(
                f"FRR family {family_key!r} must be an object"
            )

        info = family.get("info")
        if not isinstance(info, dict):
            raise FedrampProfileError(
                f"FRR family {family_key!r} must declare info"
            )

        applicable_subset_keys = _applicable_subset_keys(info)
        data = family.get("data")
        if not isinstance(data, dict):
            continue

        for data_tier in ("all", "20x"):
            tier_data = data.get(data_tier)
            if not isinstance(tier_data, dict):
                continue

            for subset_key, rules in tier_data.items():
                if subset_key not in applicable_subset_keys:
                    continue
                if not isinstance(rules, dict):
                    raise FedrampProfileError(
                        f"malformed FRR rule container at "
                        f"/FRR/{family_key}/data/{data_tier}/{subset_key}"
                    )

                for rule_id, rule in rules.items():
                    if not isinstance(rule, dict):
                        raise FedrampProfileError(
                            f"malformed FRR rule at "
                            f"/FRR/{family_key}/data/{data_tier}/{subset_key}/{rule_id}"
                        )
                    if "Providers" not in (rule.get("affects") or []):
                        continue

                    pointer = (
                        f"/FRR/{family_key}/data/{data_tier}/{subset_key}/{rule_id}"
                    )
                    item = _build_frr_assessment_item(
                        rule_id=rule_id,
                        rule=rule,
                        source_pointer=pointer,
                    )
                    if rule_id in seen_ids:
                        raise FedrampProfileError(
                            f"duplicate FRR assessment_item_id {rule_id!r} at {pointer}"
                        )
                    seen_ids.add(rule_id)
                    items.append(item)

    return items


def _extract_ksi_assessment_items(ksi_root: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for category_key, category in ksi_root.items():
        if not isinstance(category, dict):
            raise FedrampProfileError(
                f"KSI category {category_key!r} must be an object"
            )

        indicators = category.get("indicators")
        if not isinstance(indicators, dict):
            raise FedrampProfileError(
                f"KSI category {category_key!r} must declare indicators"
            )

        for indicator_id, indicator in indicators.items():
            if not isinstance(indicator, dict):
                raise FedrampProfileError(
                    f"malformed KSI indicator at "
                    f"/KSI/{category_key}/indicators/{indicator_id}"
                )

            pointer = f"/KSI/{category_key}/indicators/{indicator_id}"
            item = _build_ksi_assessment_item(
                indicator_id=indicator_id,
                indicator=indicator,
                source_pointer=pointer,
            )
            items.append(item)

    return items


def _build_frr_assessment_item(
    *,
    rule_id: str,
    rule: dict[str, Any],
    source_pointer: str,
) -> dict[str, Any]:
    resolved = _resolve_class_c_variant(rule)
    title = resolved.get("name")
    requirement_text = resolved.get("statement")
    force = resolved.get("force")

    if not isinstance(title, str) or not title.strip():
        raise FedrampProfileError(
            f"FRR rule at {source_pointer} must declare a nonempty name"
        )
    if not isinstance(requirement_text, str) or not requirement_text.strip():
        raise FedrampProfileError(
            f"FRR rule at {source_pointer} must declare a nonempty statement"
        )
    if not isinstance(force, str) or force not in _RECOGNIZED_FORCES:
        raise FedrampProfileError(
            f"FRR rule at {source_pointer} has unrecognized force {force!r}"
        )

    return {
        "assessment_item_type": "fedramp_rule",
        "assessment_item_id": rule_id,
        "title": title,
        "requirement_text": requirement_text,
        "force": _normalize_force(force),
        "owner": "provider",
        "applicability": dict(_CLASS_C_APPLICABILITY),
        "authority_refs": [
            {
                "authority_id": FEDRAMP_AUTHORITY_ID,
                "source_pointer": source_pointer,
            }
        ],
        "required_evidence_kinds": [],
        "model_analysis_allowed": True,
    }


def _build_ksi_assessment_item(
    *,
    indicator_id: str,
    indicator: dict[str, Any],
    source_pointer: str,
) -> dict[str, Any]:
    resolved = _resolve_class_c_variant(indicator)
    title = resolved.get("name")
    requirement_text = resolved.get("statement")

    if not isinstance(title, str) or not title.strip():
        raise FedrampProfileError(
            f"KSI indicator at {source_pointer} must declare a nonempty name"
        )
    if not isinstance(requirement_text, str) or not requirement_text.strip():
        raise FedrampProfileError(
            f"KSI indicator at {source_pointer} must declare a nonempty statement"
        )

    return {
        "assessment_item_type": "fedramp_ksi",
        "assessment_item_id": indicator_id,
        "title": title,
        "requirement_text": requirement_text,
        "force": "MUST",
        "owner": "provider",
        "applicability": dict(_CLASS_C_APPLICABILITY),
        "authority_refs": [
            {
                "authority_id": FEDRAMP_AUTHORITY_ID,
                "source_pointer": source_pointer,
            },
            {
                "authority_id": FEDRAMP_AUTHORITY_ID,
                "source_pointer": KSI_VVK_PARENT_POINTER,
            },
            {
                "authority_id": FEDRAMP_AUTHORITY_ID,
                "source_pointer": KSI_MOT_PARENT_POINTER,
            },
            {
                "authority_id": FEDRAMP_AUTHORITY_ID,
                "source_pointer": KSI_AIA_PARENT_POINTER,
            },
        ],
        "required_evidence_kinds": [],
        "model_analysis_allowed": True,
    }


def _applicable_subset_keys(info: dict[str, Any]) -> set[str]:
    merged_subsets = dict(info.get("subsets") or {})
    twenty_x = info.get("20x")
    if isinstance(twenty_x, dict):
        merged_subsets.update(twenty_x.get("subsets") or {})

    applicable: set[str] = set()
    for subset_key, subset_info in merged_subsets.items():
        if not isinstance(subset_info, dict):
            raise FedrampProfileError(
                f"malformed subset definition {subset_key!r} in FRR family info"
            )
        if _subset_applies_to_class_c_providers(subset_info):
            applicable.add(subset_key)
    return applicable


def _subset_applies_to_class_c_providers(subset_info: dict[str, Any]) -> bool:
    applicability = subset_info.get("applicability")
    if not isinstance(applicability, dict):
        raise FedrampProfileError("subset applicability must be an object")

    types = applicability.get("types")
    paths = applicability.get("paths")
    classes = applicability.get("classes")
    affects = applicability.get("affects")

    if not isinstance(types, list) or not isinstance(paths, list):
        raise FedrampProfileError("subset applicability types and paths must be lists")
    if not isinstance(classes, list) or not isinstance(affects, list):
        raise FedrampProfileError("subset applicability classes and affects must be lists")

    return (
        "20x" in types
        and "Program" in paths
        and "C" in classes
        and "Providers" in affects
    )


def _resolve_class_c_variant(node: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(node)
    varies_by_class = node.get("varies_by_class")
    if varies_by_class is None:
        return resolved
    if not isinstance(varies_by_class, dict):
        raise FedrampProfileError("varies_by_class must be an object")

    class_variant = varies_by_class.get("c")
    if not isinstance(class_variant, dict):
        raise FedrampProfileError(
            "Class C authority item with varies_by_class must declare a c variant"
        )

    for key, value in class_variant.items():
        if key != "varies_by_class":
            resolved[key] = value
    return resolved


def _normalize_force(force: str) -> str:
    return _FORCE_NORMALIZATION.get(force, force)


def _enforce_assessment_item_counts(items: list[dict[str, Any]]) -> None:
    frr_count = sum(
        1 for item in items if item.get("assessment_item_type") == "fedramp_rule"
    )
    ksi_count = sum(
        1 for item in items if item.get("assessment_item_type") == "fedramp_ksi"
    )
    total_count = len(items)

    if frr_count != EXPECTED_FRR_RULE_COUNT:
        raise FedrampProfileError(
            "expected "
            f"{EXPECTED_FRR_RULE_COUNT} fedramp_rule assessment items, found {frr_count}"
        )
    if ksi_count != EXPECTED_KSI_COUNT:
        raise FedrampProfileError(
            "expected "
            f"{EXPECTED_KSI_COUNT} fedramp_ksi assessment items, found {ksi_count}"
        )
    if total_count != EXPECTED_ASSESSMENT_ITEM_COUNT:
        raise FedrampProfileError(
            "expected "
            f"{EXPECTED_ASSESSMENT_ITEM_COUNT} total assessment items, found {total_count}"
        )


__all__ = [
    "FedrampProfileError",
    "compile_fedramp_20x_class_c_profile",
    "extract_fedramp_20x_class_c_assessment_items",
    "fedramp_20x_artifact_requirements",
]
