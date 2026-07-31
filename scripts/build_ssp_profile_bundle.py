"""Build the first offline agency FISMA SSP profile from pinned NIST OSCAL."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

ARCHIVE_RELATIVE_PATH = Path("reference/authorities/nist/oscal-content-1.5.0.zip")
BUNDLE_RELATIVE_PATH = Path(
    "reference/ssp_profiles/agency-fisma-nist-sp800-53-rev5-1.2.0"
)
ARCHIVE_VERSION = "1.5.0"
PROFILE_VERSION = "1.2.0"
SSP_REQUIREMENTS_SCHEMA_VERSION = "1.2.0"
SP800_18R2_SOURCE_ID = "nist-sp-800-18-r2"
SP800_18R2_VERSION = "2.0.0"
SP800_18R2_REFERENCE = "https://doi.org/10.6028/NIST.SP.800-18r2 (final June 2026)"
ARCHIVE_PREFIX = "oscal-content-1.5.0/nist.gov/SP800-53/rev5/json"
CATALOG_MEMBER = f"{ARCHIVE_PREFIX}/NIST_SP-800-53_rev5_catalog-min.json"
BASELINE_MEMBERS = {
    "low": (
        f"{ARCHIVE_PREFIX}/"
        "NIST_SP-800-53_rev5_LOW-baseline-resolved-profile_catalog-min.json"
    ),
    "moderate": (
        f"{ARCHIVE_PREFIX}/"
        "NIST_SP-800-53_rev5_MODERATE-baseline-resolved-profile_catalog-min.json"
    ),
    "high": (
        f"{ARCHIVE_PREFIX}/"
        "NIST_SP-800-53_rev5_HIGH-baseline-resolved-profile_catalog-min.json"
    ),
}
EXPECTED_BASELINE_COUNTS = {
    "low": 149,
    "moderate": 287,
    "high": 370,
}
OUTPUT_FILENAMES = (
    "catalog.json",
    "baselines.json",
    "ssp-requirements.json",
    "manifest.json",
)


class BuildProfileBundleError(ValueError):
    """Raised when pinned content cannot produce the expected local bundle."""


def build_bundle(*, archive_path: Path, output_dir: Path) -> None:
    archive_path = archive_path.resolve()
    output_dir = output_dir.resolve()
    if not archive_path.is_file() or archive_path.is_symlink():
        raise BuildProfileBundleError(
            "pinned OSCAL content archive must be a regular non-symlink file"
        )
    if output_dir.exists() and (not output_dir.is_dir() or output_dir.is_symlink()):
        raise BuildProfileBundleError(
            "profile bundle output must be a non-symlink directory"
        )

    try:
        with ZipFile(archive_path) as archive:
            catalog_bytes = archive.read(CATALOG_MEMBER)
            catalog_document = _load_json_object(
                catalog_bytes,
                label="NIST SP 800-53 catalog",
            )
            baseline_documents = {
                impact_level: _load_json_object(
                    archive.read(member),
                    label=f"NIST SP 800-53B {impact_level} resolved baseline",
                )
                for impact_level, member in BASELINE_MEMBERS.items()
            }
    except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
        raise BuildProfileBundleError(
            "pinned OSCAL content archive is invalid or missing required Rev. 5 files"
        ) from exc

    catalog_version = _metadata_version(catalog_document, label="catalog")
    if catalog_version != "5.2.0":
        raise BuildProfileBundleError(
            f"expected NIST catalog version '5.2.0', got {catalog_version!r}"
        )

    catalog_ids = set(_catalog_control_ids(catalog_document))
    baselines: dict[str, list[str] | str] = {"schema_version": "1.0.0"}
    for impact_level in ("low", "moderate", "high"):
        baseline_document = baseline_documents[impact_level]
        baseline_version = _metadata_version(
            baseline_document,
            label=f"{impact_level} baseline",
        )
        if baseline_version != catalog_version:
            raise BuildProfileBundleError(
                f"{impact_level} baseline version {baseline_version!r} does not "
                f"match catalog version {catalog_version!r}"
            )
        control_ids = sorted(_catalog_control_ids(baseline_document))
        expected_count = EXPECTED_BASELINE_COUNTS[impact_level]
        if len(control_ids) != expected_count:
            raise BuildProfileBundleError(
                f"{impact_level} baseline resolved to {len(control_ids)} controls; "
                f"expected {expected_count}"
            )
        missing_ids = sorted(set(control_ids) - catalog_ids)
        if missing_ids:
            raise BuildProfileBundleError(
                f"{impact_level} baseline contains controls missing from catalog: "
                + ", ".join(missing_ids)
            )
        baselines[impact_level] = control_ids

    generated_files = {
        "catalog.json": catalog_bytes,
        "baselines.json": _canonical_json_bytes(baselines),
        "ssp-requirements.json": _canonical_json_bytes(_ssp_requirements()),
    }
    manifest = _manifest(
        archive_path=archive_path,
        catalog_version=catalog_version,
        generated_files=generated_files,
    )
    generated_files["manifest.json"] = _canonical_json_bytes(manifest)

    if output_dir.exists() and any(output_dir.iterdir()):
        existing_names = tuple(sorted(path.name for path in output_dir.iterdir()))
        expected_names = tuple(sorted(OUTPUT_FILENAMES))
        members_are_identical = existing_names == expected_names and all(
            (output_dir / filename).is_file()
            and not (output_dir / filename).is_symlink()
            and (output_dir / filename).read_bytes() == generated_files[filename]
            for filename in OUTPUT_FILENAMES
        )
        if not members_are_identical:
            raise BuildProfileBundleError(
                "immutable profile bundle version already exists with different content"
            )
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in OUTPUT_FILENAMES:
        (output_dir / filename).write_bytes(generated_files[filename])


def _catalog_control_ids(document: dict[str, Any]) -> tuple[str, ...]:
    catalog = document.get("catalog")
    if not isinstance(catalog, dict):
        raise BuildProfileBundleError("OSCAL document must contain a catalog")
    groups = catalog.get("groups")
    if not isinstance(groups, list):
        raise BuildProfileBundleError("OSCAL catalog must contain groups")

    control_ids: list[str] = []

    def collect_controls(controls: Any) -> None:
        if not isinstance(controls, list):
            return
        for control in controls:
            if not isinstance(control, dict):
                raise BuildProfileBundleError("OSCAL controls must be objects")
            control_id = control.get("id")
            if not isinstance(control_id, str) or not control_id.strip():
                raise BuildProfileBundleError(
                    "OSCAL control must declare a non-empty id"
                )
            control_ids.append(control_id.strip().upper())
            collect_controls(control.get("controls"))

    def collect_groups(group_items: Any) -> None:
        if not isinstance(group_items, list):
            return
        for group in group_items:
            if not isinstance(group, dict):
                raise BuildProfileBundleError("OSCAL groups must be objects")
            collect_controls(group.get("controls"))
            collect_groups(group.get("groups"))

    collect_groups(groups)
    if not control_ids:
        raise BuildProfileBundleError("OSCAL catalog contains no controls")
    if len(control_ids) != len(set(control_ids)):
        raise BuildProfileBundleError("OSCAL catalog contains duplicate control ids")
    return tuple(control_ids)


def _metadata_version(document: dict[str, Any], *, label: str) -> str:
    catalog = document.get("catalog")
    metadata = catalog.get("metadata") if isinstance(catalog, dict) else None
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise BuildProfileBundleError(f"{label} metadata must declare version")
    return version.strip()


def _ssp_requirements() -> dict[str, Any]:
    impact_levels = ["low", "moderate", "high"]
    items = [
        _string_item(
            "system.name",
            "System Name",
            1,
            standard_refs=["table1.system-name-and-identifier"],
        ),
        _string_item(
            "system.identifier",
            "System Identifier",
            1,
            standard_refs=["table1.system-name-and-identifier"],
        ),
        _string_item(
            "system.purpose",
            "System Purpose",
            20,
            standard_refs=["table1.system-overview"],
        ),
        _string_item(
            "system.owner",
            "System Owner",
            1,
            standard_refs=["table1.role-identification-and-responsible-personnel"],
        ),
        _string_item(
            "system.isso",
            "Information System Security Officer",
            1,
            standard_refs=["table1.role-identification-and-responsible-personnel"],
        ),
        _string_list_item(
            "system.responsible_personnel",
            "Other Responsible Personnel",
            standard_refs=["table1.role-identification-and-responsible-personnel"],
            minimum_entries=1,
        ),
        _string_item(
            "system.type",
            "Information System Type",
            1,
            standard_refs=["table1.system-type"],
        ),
        _string_item(
            "system.authorization_boundary",
            "Authorization Boundary",
            20,
            standard_refs=["table1.authorization-boundary-description"],
        ),
        _string_item(
            "system.environment",
            "Operational Environment",
            20,
            standard_refs=["table1.system-overview"],
        ),
        _string_item(
            "system.hosting_model",
            "Hosting Model",
            allowed_values=["on_premises", "agency_cloud", "agency_hybrid"],
            standard_refs=["table1.system-type"],
        ),
        _string_list_item(
            "system.components",
            "Major System Components",
            standard_refs=["table1.system-component-inventory"],
        ),
        _string_list_item(
            "system.interconnections",
            "System Interconnections",
            standard_refs=["table1.information-exchanges-summary"],
        ),
        _string_list_item(
            "system.data_types",
            "Information Types",
            standard_refs=["table1.system-information-types"],
        ),
        _string_list_item(
            "system.user_roles",
            "User and Privileged Roles",
            standard_refs=["table1.system-overview"],
        ),
        _string_item(
            "system.confidentiality_impact",
            "Confidentiality Impact Level",
            allowed_values=impact_levels,
            standard_refs=["table1.system-categorization"],
        ),
        _string_item(
            "system.integrity_impact",
            "Integrity Impact Level",
            allowed_values=impact_levels,
            standard_refs=["table1.system-categorization"],
        ),
        _string_item(
            "system.availability_impact",
            "Availability Impact Level",
            allowed_values=impact_levels,
            standard_refs=["table1.system-categorization"],
        ),
        _string_item(
            "system.impact_level",
            "Security Categorization Impact Level",
            allowed_values=impact_levels,
            standard_refs=["table1.system-categorization"],
        ),
        _string_item(
            "system.categorization_rationale",
            "Security Categorization Rationale",
            20,
            standard_refs=["table1.system-categorization"],
        ),
        _string_item(
            "system.laws_regulations_policies",
            "Laws, Regulations, and Policies",
            20,
            standard_refs=[
                "table1.laws-regulations-and-policies-affecting-system-requirements"
            ],
        ),
        _string_list_item(
            "system.diagram_references",
            "Referenced Diagrams and Architecture Artifacts",
            standard_refs=["table1.environment-of-operation-diagrams"],
        ),
        _string_item(
            "system.authorization_path",
            "Authorization Path",
            10,
            standard_refs=["table1.system-authorization-decision"],
        ),
        _string_item(
            "system.plan_approval",
            "Security Plan Approval",
            10,
            standard_refs=["table1.system-plan-approval"],
        ),
        _string_item(
            "system.authorization_decision",
            "Authorization Decision",
            10,
            standard_refs=["table1.system-authorization-decision"],
        ),
        _string_item(
            "system.operational_status",
            "Operational Status",
            allowed_values=[
                "operational",
                "under_development",
                "major_modification",
                "disposal",
                "other",
            ],
            standard_refs=["table1.system-operational-status"],
        ),
        _string_item(
            "system.control_assessment_status",
            "Control Assessment Status Summary",
            20,
            standard_refs=["table1.control-assessment-status"],
        ),
        _string_list_item(
            "system.remediation_actions",
            "Remediation Actions and Milestones",
            standard_refs=["table1.remediation-actions"],
        ),
        _string_item(
            "system.digital_identity_acceptance",
            "Digital Identity Acceptance Statement",
            20,
            required=False,
            standard_refs=["table1.digital-identity-acceptance-statement"],
        ),
        _string_list_item(
            "system.plan_review_records",
            "Security Plan Review Records",
            standard_refs=["table1.system-plan-review-records"],
        ),
        _string_list_item(
            "system.plan_change_records",
            "Security Plan Change Records",
            standard_refs=["table1.system-plan-change-records"],
        ),
        _string_item(
            "system.identity_and_access",
            "Identification and Access Approach",
            20,
            standard_refs=["table1.control-implementation-details"],
        ),
        _string_item(
            "system.logging_and_monitoring",
            "Logging and Monitoring Approach",
            20,
            standard_refs=["table1.control-implementation-details"],
        ),
        _string_item(
            "system.contingency_and_recovery",
            "Contingency and Recovery Approach",
            20,
            standard_refs=["table1.control-implementation-details"],
        ),
        _string_list_item(
            "ssp.acronyms_and_glossary",
            "Acronyms and Glossary",
            standard_refs=["outline.acronyms-and-glossary"],
            minimum_entries=1,
        ),
    ]
    return {
        "schema_version": SSP_REQUIREMENTS_SCHEMA_VERSION,
        "control_response": {
            "implementation_statuses": [
                "implemented",
                "partially_implemented",
                "planned",
                "not_implemented",
                "not_applicable",
                "unknown",
            ],
            "responsibilities": [
                "system_specific",
                "hybrid",
                "inherited",
                "unknown",
            ],
            "question_owner_types": [
                "isso",
                "agency",
                "technical",
                "system_owner",
            ],
            "evidence_required_for_agent_statement": True,
        },
        "standard_coverage": _standard_coverage(),
        "implementation_statement_policy": _implementation_statement_policy(),
        "items": items,
    }




def _implementation_statement_policy() -> dict[str, Any]:
    return {
        "policy_version": "1.0.0",
        "deterministic": {
            "reject_oscal_parameter_insert_syntax": True,
            "require_question_for_unresolved_parameterized_controls": True,
            "require_evidence_for_agent_non_unknown_claims": True,
            "require_statement_gap_or_question_before_approval": True,
            "semantic_quality_findings_are_advisory": True,
        },
        "agent_instructions": {
            "statement_content": [
                "Address the applicable control requirement with evidence-grounded implementation, responsibility, scope when material, timing only when the requirement or implementation is time- or event-dependent, and explicit coverage of each applicable requirement clause.",
                "Use one or more concise sentences; do not invent mechanisms, roles, scope, timing, or requirement coverage that evidence does not support.",
                "When responsibility, scope, timing, or a requirement clause is unsupported, record an explicit gap or ask one targeted control question instead of plausible filler.",
            ],
            "organization_defined_parameters": [
                "Never invent values for organization-defined parameters referenced in control requirement_text placeholders.",
                "Use direct evidence only when it explicitly supports a parameter value and cite supporting_fact_ids.",
                "When evidence does not support a parameter value, leave implementation_statement empty, keep status and responsibility unknown, and ask one concise control-targeted question for the missing agency or organization value.",
                "Never emit literal OSCAL placeholder syntax in implementation_statement text.",
            ],
            "inherited_and_hybrid_responsibility": [
                "Use inherited or hybrid responsibility only when direct evidence supports that split.",
                "When evidence supports inherited or hybrid responsibility, describe the known provider or common portion and the system-specific portion separately.",
                "Never invent provider scope, inheritance boundaries, or shared-service details.",
                "When evidence does not support inheritance details, keep responsibility unknown and ask a targeted question.",
            ],
            "semantic_review": [
                "Evaluate whether an evidence-grounded statement covers the profile-required elements and applicable control clauses.",
                "Missing semantic content should produce a targeted control question, not invented text.",
                "Semantic quality findings are advisory; they must not alone approve or reject a revision.",
            ],
        },
        "authority_refs": [
            {
                "source_id": SP800_18R2_SOURCE_ID,
                "requirement_ids": [
                    "table1.control-implementation-details",
                    "table1.control-implementation-status",
                ],
            }
        ],
    }


def _standard_coverage() -> list[dict[str, Any]]:
    return [
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-name-and-identifier",
            "title": "System Name and Identifier",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.name", "system.identifier"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-type",
            "title": "System Type",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.type", "system.hosting_model"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-overview",
            "title": "System Overview",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.purpose", "system.environment", "system.user_roles"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.role-identification-and-responsible-personnel",
            "title": "Role Identification and Responsible Personnel",
            "coverage_kind": "ssp_item",
            "item_ids": [
                "system.owner",
                "system.isso",
                "system.responsible_personnel",
            ],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-information-types",
            "title": "System Information Types",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.data_types"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": (
                "table1.laws-regulations-and-policies-affecting-system-requirements"
            ),
            "title": "Laws, Regulations, and Policies Affecting the System Requirements",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.laws_regulations_policies"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.authorization-boundary-description",
            "title": "Authorization Boundary Description",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.authorization_boundary"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-component-inventory",
            "title": "System Component Inventory",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.components"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.environment-of-operation-diagrams",
            "title": "Environment of Operation Diagrams",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.diagram_references"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-categorization",
            "title": "System Categorization",
            "coverage_kind": "ssp_item",
            "item_ids": [
                "system.confidentiality_impact",
                "system.integrity_impact",
                "system.availability_impact",
                "system.impact_level",
                "system.categorization_rationale",
            ],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.control-implementation-details",
            "title": "Control Implementation Details",
            "coverage_kind": "controls",
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-plan-approval",
            "title": "System Plan Approval",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.plan_approval"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.control-implementation-status",
            "title": "Control Implementation Status",
            "coverage_kind": "controls",
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.information-exchanges-summary",
            "title": "Information Exchanges Summary",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.interconnections"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.control-assessment-status",
            "title": "Control Assessment Status",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.control_assessment_status"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.remediation-actions",
            "title": "Remediation Actions",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.remediation_actions"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.digital-identity-acceptance-statement",
            "title": "Digital Identity Acceptance Statement",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.digital_identity_acceptance"],
            "required": False,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-authorization-decision",
            "title": "System Authorization Decision",
            "coverage_kind": "ssp_item",
            "item_ids": [
                "system.authorization_path",
                "system.authorization_decision",
            ],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-operational-status",
            "title": "System Operational Status",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.operational_status"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-plan-review-records",
            "title": "System Plan Review Records",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.plan_review_records"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "table1.system-plan-change-records",
            "title": "System Plan Change Records",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.plan_change_records"],
            "required": True,
        },
        {
            "source_id": SP800_18R2_SOURCE_ID,
            "requirement_id": "outline.acronyms-and-glossary",
            "title": "Acronyms and Glossary",
            "coverage_kind": "ssp_item",
            "item_ids": ["ssp.acronyms_and_glossary"],
            "required": True,
        },
    ]


def _string_item(
    item_id: str,
    title: str,
    minimum_length: int | None = None,
    *,
    allowed_values: list[str] | None = None,
    required: bool = True,
    standard_refs: list[str] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "item_id": item_id,
        "title": title,
        "value_type": "string",
        "evidence_required_for_agent": True,
        "required": required,
    }
    if minimum_length is not None:
        item["min_length"] = minimum_length
    if allowed_values is not None:
        item["allowed_values"] = allowed_values
    if standard_refs is not None:
        item["standard_refs"] = standard_refs
    return item


def _string_list_item(
    item_id: str,
    title: str,
    *,
    required: bool = True,
    standard_refs: list[str] | None = None,
    minimum_entries: int | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "item_id": item_id,
        "title": title,
        "value_type": "string_list",
        "evidence_required_for_agent": True,
        "required": required,
    }
    if minimum_entries is not None:
        item["min_length"] = minimum_entries
    if standard_refs is not None:
        item["standard_refs"] = standard_refs
    return item


def _manifest(
    *,
    archive_path: Path,
    catalog_version: str,
    generated_files: dict[str, bytes],
) -> dict[str, Any]:
    archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    baseline_members = ", ".join(
        BASELINE_MEMBERS[level] for level in ("low", "moderate", "high")
    )
    return {
        "schema_version": "1.0.0",
        "profile_id": "agency-fisma-nist-sp800-53-rev5",
        "profile_version": PROFILE_VERSION,
        "nist_control_catalog_release": catalog_version,
        "display_name": ("Agency FISMA — NIST SP 800-53 Revision 5 Low/Moderate/High"),
        "sources": [
            {
                "source_id": "nist-oscal-content",
                "title": "NIST OSCAL Content",
                "version": ARCHIVE_VERSION,
                "reference": (
                    f"{ARCHIVE_RELATIVE_PATH.as_posix()} (sha256:{archive_sha256})"
                ),
            },
            {
                "source_id": SP800_18R2_SOURCE_ID,
                "title": (
                    "Developing Security, Privacy, and Cybersecurity Supply Chain "
                    "Risk Management Plans for Systems"
                ),
                "version": SP800_18R2_VERSION,
                "reference": SP800_18R2_REFERENCE,
            },
            {
                "source_id": "nist-sp-800-53",
                "title": (
                    "Security and Privacy Controls for Information Systems "
                    "and Organizations"
                ),
                "version": catalog_version,
                "reference": CATALOG_MEMBER,
            },
            {
                "source_id": "nist-sp-800-53b",
                "title": (
                    "Control Baselines for Information Systems and Organizations"
                ),
                "version": catalog_version,
                "reference": baseline_members,
            },
        ],
        "files": [
            {
                "role": "catalog",
                "path": "catalog.json",
                "sha256": hashlib.sha256(generated_files["catalog.json"]).hexdigest(),
            },
            {
                "role": "baselines",
                "path": "baselines.json",
                "sha256": hashlib.sha256(generated_files["baselines.json"]).hexdigest(),
            },
            {
                "role": "ssp_requirements",
                "path": "ssp-requirements.json",
                "sha256": hashlib.sha256(
                    generated_files["ssp-requirements.json"]
                ).hexdigest(),
            },
        ],
    }


def _load_json_object(raw_bytes: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildProfileBundleError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise BuildProfileBundleError(f"{label} must be a JSON object")
    return document


def _canonical_json_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    root = _project_root()
    parser = argparse.ArgumentParser(
        description=(
            "Build a local SSP profile bundle from the pinned NIST OSCAL archive."
        )
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=root / ARCHIVE_RELATIVE_PATH,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / BUNDLE_RELATIVE_PATH,
    )
    args = parser.parse_args()
    try:
        build_bundle(archive_path=args.archive, output_dir=args.output)
    except BuildProfileBundleError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
