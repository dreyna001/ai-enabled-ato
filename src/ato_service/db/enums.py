"""Closed enum value sets synchronized with domain contracts and service modules."""

from __future__ import annotations

from ato_service.lifecycle_transitions import AnalysisRunStatus, PackageRevisionStatus
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity

PACKAGE_REVISION_STATUS_VALUES: tuple[str, ...] = tuple(
    status.value for status in PackageRevisionStatus
)
ANALYSIS_RUN_STATUS_VALUES: tuple[str, ...] = tuple(
    status.value for status in AnalysisRunStatus
)
DATA_ORIGIN_VALUES: tuple[str, ...] = tuple(origin.value for origin in DataOrigin)
SENSITIVITY_VALUES: tuple[str, ...] = tuple(
    sensitivity.value for sensitivity in Sensitivity
)
ENDPOINT_PROFILE_VALUES: tuple[str, ...] = tuple(
    profile.value for profile in EndpointProfile
)

PROFILE_ID_VALUES: tuple[str, ...] = (
    "fedramp_20x_program",
    "fedramp_rev5_transition",
    "fisma_agency_security",
)

CERTIFICATION_CLASS_VALUES: tuple[str, ...] = ("B", "C")
IMPACT_LEVEL_VALUES: tuple[str, ...] = ("low", "moderate", "high")

ARTIFACT_KIND_VALUES: tuple[str, ...] = (
    "manifest",
    "fedramp_cpo",
    "fedramp_sdr",
    "fedramp_ocr",
    "fedramp_scg",
    "oscal",
    "evidence_document",
    "scanner_export",
    "architecture",
    "attestation",
    "reference_catalog",
)

MALWARE_SCAN_STATUS_VALUES: tuple[str, ...] = (
    "pending",
    "clean",
    "infected",
    "error",
)

EXTRACTION_STATUS_VALUES: tuple[str, ...] = (
    "pending",
    "succeeded",
    "failed",
    "not_applicable",
)

EXTRACTION_METHOD_VALUES: tuple[str, ...] = (
    "deterministic",
    "text",
    "vision",
    "llm_normalize",
)

FACT_PROPOSAL_REVIEW_STATUS_VALUES: tuple[str, ...] = (
    "pending",
    "accepted",
    "rejected",
    "edited",
)

ANALYSIS_RUN_TYPE_VALUES: tuple[str, ...] = (
    "full",
    "targeted",
    "deterministic_only",
)

RUN_STEP_TYPE_VALUES: tuple[str, ...] = (
    "normalize_proposal",
    "sufficiency_matrix",
    "consistency_brief",
    "narrative_flags",
    "provider_draft",
    "ksi_summary",
    "ocr_summary",
    "package_chat",
)

AUDIT_ACTOR_TYPE_VALUES: tuple[str, ...] = ("user", "service")
AUDIT_OUTCOME_VALUES: tuple[str, ...] = ("succeeded", "denied", "failed")

JOB_STATUS_VALUES: tuple[str, ...] = (
    "available",
    "leased",
    "completed",
    "failed",
    "reconciliation_required",
)

JOB_ATTEMPT_STATUS_VALUES: tuple[str, ...] = (
    "active",
    "succeeded",
    "failed",
)

INTAKE_WORK_PHASE_VALUES: tuple[str, ...] = (
    "malware_scan",
    "deterministic_extract",
)

INTAKE_WORK_STATUS_VALUES: tuple[str, ...] = JOB_STATUS_VALUES

INTAKE_ATTEMPT_STATUS_VALUES: tuple[str, ...] = JOB_ATTEMPT_STATUS_VALUES

NORMALIZATION_STEP_STATUS_VALUES: tuple[str, ...] = (
    "reserved",
    "running",
    "completed",
    "policy_blocked",
    "failed",
    "reconciliation_required",
)

NORMALIZATION_STEP_KEY_VALUES: tuple[str, ...] = ("normalize_proposal",)

ASSESSMENT_ITEM_TYPE_VALUES: tuple[str, ...] = (
    "nist_control",
    "fedramp_rule",
    "fedramp_ksi",
)

MATRIX_STATUS_VALUES: tuple[str, ...] = (
    "supported",
    "partial",
    "unsupported",
    "insufficient_evidence",
)

MATRIX_ROW_STATUS_VALUES: tuple[str, ...] = MATRIX_STATUS_VALUES
