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
