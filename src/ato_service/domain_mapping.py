"""Map persistence rows to exact domain JSON contracts."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

DOMAIN_SCHEMA_VERSION = "2.0.0"


def format_uuid(value: UUID) -> str:
    """Return lowercase UUID strings for domain JSON."""
    return str(value).lower()


def format_iso_date(value: date | None) -> str | None:
    """Return YYYY-MM-DD dates for domain JSON."""
    if value is None:
        return None
    return value.isoformat()


def format_utc_datetime(value: datetime) -> str:
    """Return UTC datetimes with a trailing Z suffix."""
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    utc_value = value.astimezone(timezone.utc).replace(tzinfo=None)
    text = utc_value.isoformat(timespec="microseconds")
    if text.endswith("+00:00"):
        text = text[: -len("+00:00")]
    if text.endswith(".000000"):
        text = text[: -len(".000000")]
    return f"{text}Z"


def map_system_to_domain(system: Any) -> dict[str, Any]:
    """Map a duck-typed System row to domain JSON."""
    return {
        "schema_version": DOMAIN_SCHEMA_VERSION,
        "object_type": "system",
        "system_id": format_uuid(system.system_id),
        "display_name": system.display_name,
        "external_system_id": system.external_system_id,
        "owner_group": system.owner_group,
        "viewer_groups": list(system.viewer_groups),
        "created_at": format_utc_datetime(system.created_at),
        "archived_at": (
            None
            if system.archived_at is None
            else format_utc_datetime(system.archived_at)
        ),
    }


def map_package_revision_to_domain(package_revision: Any) -> dict[str, Any]:
    """Map a duck-typed PackageRevision row to domain JSON."""
    return {
        "schema_version": DOMAIN_SCHEMA_VERSION,
        "object_type": "package_revision",
        "package_revision_id": format_uuid(package_revision.package_revision_id),
        "system_id": format_uuid(package_revision.system_id),
        "parent_revision_id": (
            None
            if package_revision.parent_revision_id is None
            else format_uuid(package_revision.parent_revision_id)
        ),
        "profile_id": package_revision.profile_id,
        "certification_class": package_revision.certification_class,
        "impact_level": package_revision.impact_level,
        "data_origin": package_revision.data_origin,
        "sensitivity": package_revision.sensitivity,
        "effective_data_labels": list(package_revision.effective_data_labels),
        "authority_manifest_id": package_revision.authority_manifest_id,
        "content_manifest_sha256": package_revision.content_manifest_sha256,
        "revision_version": package_revision.revision_version,
        "status": package_revision.status,
        "created_by": package_revision.created_by,
        "created_at": format_utc_datetime(package_revision.created_at),
    }


def map_source_artifact_to_domain(source_artifact: Any) -> dict[str, Any]:
    """Map a duck-typed SourceArtifact row to domain JSON."""
    return {
        "schema_version": DOMAIN_SCHEMA_VERSION,
        "object_type": "source_artifact",
        "artifact_id": format_uuid(source_artifact.artifact_id),
        "package_revision_id": format_uuid(source_artifact.package_revision_id),
        "display_filename": source_artifact.display_filename,
        "storage_key": source_artifact.storage_key,
        "sha256": source_artifact.sha256,
        "size_bytes": source_artifact.size_bytes,
        "declared_media_type": source_artifact.declared_media_type,
        "detected_media_type": source_artifact.detected_media_type,
        "artifact_kind": source_artifact.artifact_kind,
        "malware_scan_status": source_artifact.malware_scan_status,
        "extraction_status": source_artifact.extraction_status,
        "source_date": format_iso_date(source_artifact.source_date),
        "uploaded_at": format_utc_datetime(source_artifact.uploaded_at),
    }


def map_fact_proposal_to_domain(fact_proposal: Any) -> dict[str, Any]:
    """Map a duck-typed FactProposal row to domain JSON."""
    return {
        "schema_version": DOMAIN_SCHEMA_VERSION,
        "object_type": "fact_proposal",
        "fact_proposal_id": format_uuid(fact_proposal.fact_proposal_id),
        "package_revision_id": format_uuid(fact_proposal.package_revision_id),
        "json_pointer": fact_proposal.json_pointer,
        "proposed_value": fact_proposal.proposed_value,
        "source_artifact_id": format_uuid(fact_proposal.source_artifact_id),
        "source_sha256": fact_proposal.source_sha256,
        "source_locator": fact_proposal.source_locator,
        "extraction_method": fact_proposal.extraction_method,
        "model_step_id": (
            None
            if fact_proposal.model_step_id is None
            else format_uuid(fact_proposal.model_step_id)
        ),
        "review_status": fact_proposal.review_status,
        "reviewed_by": fact_proposal.reviewed_by,
        "reviewed_at": (
            None
            if fact_proposal.reviewed_at is None
            else format_utc_datetime(fact_proposal.reviewed_at)
        ),
    }


def map_analysis_run_to_domain(analysis_run: Any) -> dict[str, Any]:
    """Map a duck-typed AnalysisRun row to domain JSON."""
    return {
        "schema_version": DOMAIN_SCHEMA_VERSION,
        "object_type": "analysis_run",
        "run_id": format_uuid(analysis_run.run_id),
        "package_revision_id": format_uuid(analysis_run.package_revision_id),
        "parent_run_id": (
            None
            if analysis_run.parent_run_id is None
            else format_uuid(analysis_run.parent_run_id)
        ),
        "run_type": analysis_run.run_type,
        "status": analysis_run.status,
        "requested_by": analysis_run.requested_by,
        "requested_at": format_utc_datetime(analysis_run.requested_at),
        "started_at": (
            None
            if analysis_run.started_at is None
            else format_utc_datetime(analysis_run.started_at)
        ),
        "completed_at": (
            None
            if analysis_run.completed_at is None
            else format_utc_datetime(analysis_run.completed_at)
        ),
        "authority_manifest_id": analysis_run.authority_manifest_id,
        "analysis_profile_sha256": analysis_run.analysis_profile_sha256,
        "config_fingerprint": analysis_run.config_fingerprint,
        "prompt_bundle_sha256": analysis_run.prompt_bundle_sha256,
        "model_profile": analysis_run.model_profile,
        "artifact_manifest_sha256": analysis_run.artifact_manifest_sha256,
        "llm_call_count": analysis_run.llm_call_count,
        "error_code": analysis_run.error_code,
        "error_retryable": analysis_run.error_retryable,
    }


def map_matrix_row_to_domain(matrix_row: Any) -> dict[str, Any]:
    """Map a duck-typed MatrixRow row to domain JSON."""
    return {
        "schema_version": DOMAIN_SCHEMA_VERSION,
        "object_type": "matrix_row",
        "matrix_row_id": format_uuid(matrix_row.matrix_row_id),
        "assessment_item_type": matrix_row.assessment_item_type,
        "assessment_item_id": matrix_row.assessment_item_id,
        "model_proposed_status": matrix_row.model_proposed_status,
        "system_status": matrix_row.system_status,
        "finding_summary": matrix_row.finding_summary,
        "gaps": list(matrix_row.gaps),
        "assessor_questions": list(matrix_row.assessor_questions),
        "citations": list(matrix_row.citations),
        "context_complete": matrix_row.context_complete,
        "producing_run_id": format_uuid(matrix_row.producing_run_id),
        "source_run_id": format_uuid(matrix_row.source_run_id),
    }
