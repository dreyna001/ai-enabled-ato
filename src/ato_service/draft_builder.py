"""Deterministic package draft builder for Component A Diff 3 intake."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from ato_service.db.models import PackageRevision, SourceArtifact, System
from ato_service.extraction.types import ExtractionOutcome, ExtractedSegment
from ato_service.project_root import contract_path

DOCUMENT_SCHEMA_VERSION = "1.0.0"
_DEFAULT_PRIVACY_SCOPE_NOTICE = "Privacy review is external to this product."
_JSON_POINTER_PATTERN = re.compile(r"^(/([^~/]|~[01])*)*$")
_FORMAT_CHECKER = FormatChecker()

_STRUCTURED_JSON_FORMATS = frozenset({"json", "oscal_json", "sarif_json", "stig_json"})
_NARRATIVE_FORMATS = frozenset({"text", "markdown", "pdf", "docx", "xlsx", "xml"})
_IMAGE_FORMATS = frozenset({"png", "jpeg", "webp", "svg"})
_STRUCTURED_XML_FORMATS = frozenset({"oscal_xml", "nessus_xml", "stig_xml"})

_ASSESSOR_OWNED_DRAFT_PREFIXES = (
    "/assessor_inputs",
    "/findings",
    "/poam_candidates",
    "/fedramp_20x/independent_assessment",
    "/fedramp_rev5_transition/sar",
)


class DraftBuildError(ValueError):
    """Raised when deterministic draft assembly cannot proceed safely."""

    def __init__(self, message: str, *, error_code: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AggregatedIntakeDraft:
    """Schema-valid draft document, provenance, and pending system-context proposal."""

    document: dict[str, Any]
    field_provenance: dict[str, Any]
    system_context_proposal: dict[str, Any] | None
    segment_count: int


@dataclass(frozen=True, slots=True)
class _ProvenanceWrite:
    draft_pointer: str
    value: Any
    source_artifact_id: uuid.UUID
    source_sha256: str
    source_locator: dict[str, Any]
    extraction_method: str


def build_initial_draft(
    *,
    revision: PackageRevision,
    system: System,
    artifacts: Sequence[SourceArtifact],
    artifact_outcomes: Sequence[tuple[SourceArtifact, ExtractionOutcome]],
) -> AggregatedIntakeDraft:
    """Build one schema-valid draft from revision metadata and extraction outcomes."""
    _require_complete_artifact_outcomes(
        artifacts=artifacts,
        artifact_outcomes=artifact_outcomes,
    )
    profile_id = revision.profile_id
    document = _empty_profile_shell(profile_id=profile_id, system=system)
    provenance: dict[str, Any] = {}
    field_values: dict[str, Any] = {}
    segment_count = 0
    extension_segments: list[dict[str, Any]] = []
    evidence_only_artifacts: list[dict[str, Any]] = []
    from ato_service.assessor_import import ingest_assessor_artifact
    from ato_service.privacy_ingest import ingest_privacy_artifact
    from ato_service.structured_ingest import ingest_structured_artifact

    for artifact, outcome in artifact_outcomes:
        segment_count += len(outcome.segments)
        if ingest_privacy_artifact(
            artifact=artifact,
            outcome=outcome,
            pending_writes=field_values,
        ):
            continue
        if ingest_assessor_artifact(
            artifact=artifact,
            outcome=outcome,
            pending_writes=field_values,
        ):
            continue
        if ingest_structured_artifact(
            artifact=artifact,
            outcome=outcome,
            pending_writes=field_values,
        ):
            continue
        if outcome.status == "evidence_only":
            evidence_only_artifacts.append(
                _evidence_only_record(artifact=artifact, outcome=outcome)
            )
            continue
        if outcome.status == "vision_deferred":
            evidence_only_artifacts.append(
                _vision_deferred_record(artifact=artifact, outcome=outcome)
            )
            continue
        if outcome.detected_format in _STRUCTURED_JSON_FORMATS:
            manifest = _reconstruct_json_document(outcome.segments)
            if _looks_like_owner_manifest(manifest):
                _apply_owner_manifest_mapping(
                    document=document,
                    manifest=manifest,
                    artifact=artifact,
                    profile_id=profile_id,
                    revision=revision,
                    pending_writes=field_values,
                )
            else:
                extension_segments.extend(
                    _structured_extension_segments(artifact=artifact, outcome=outcome)
                )
            continue
        if outcome.detected_format in _STRUCTURED_XML_FORMATS:
            extension_segments.extend(
                _structured_extension_segments(artifact=artifact, outcome=outcome)
            )
            continue
        if outcome.detected_format in _NARRATIVE_FORMATS or outcome.detected_format in _IMAGE_FORMATS:
            extension_segments.extend(
                _narrative_extension_segments(artifact=artifact, outcome=outcome)
            )
            continue
        extension_segments.extend(
            _narrative_extension_segments(artifact=artifact, outcome=outcome)
        )

    for draft_pointer in sorted(field_values):
        write = field_values[draft_pointer]
        if isinstance(write, _ProvenanceWrite):
            if write.draft_pointer.startswith("/assessor_inputs/"):
                _commit_assessor_import_field(
                    document=document,
                    provenance=provenance,
                    write=write,
                )
            else:
                _commit_field(document=document, provenance=provenance, write=write)

    if extension_segments:
        document.setdefault("extensions", {})
        document["extensions"]["unmapped_segments"] = extension_segments
    if evidence_only_artifacts:
        document.setdefault("extensions", {})
        document["extensions"]["evidence_only_artifacts"] = evidence_only_artifacts

    system_context_proposal = _build_system_context_proposal(
        document=document,
        provenance=provenance,
        system=system,
    )
    if system_context_proposal is not None:
        document.setdefault("extensions", {})
        document["extensions"]["system_context_proposal"] = system_context_proposal

    validate_package_draft_document(document)
    return AggregatedIntakeDraft(
        document=document,
        field_provenance=provenance,
        system_context_proposal=system_context_proposal,
        segment_count=segment_count,
    )


def validate_package_draft_document(document: dict[str, Any]) -> None:
    """Validate one assembled draft document against the checked-in schema."""
    errors = sorted(
        _package_draft_validator().iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise DraftBuildError(
            _format_validation_error(errors[0]),
            error_code="draft_schema_invalid",
        )


def _commit_assessor_import_field(
    *,
    document: dict[str, Any],
    provenance: dict[str, Any],
    write: _ProvenanceWrite,
) -> None:
    """Commit import-only assessor_inputs entries from attestation uploads."""
    if not write.draft_pointer.startswith("/assessor_inputs/"):
        raise DraftBuildError(
            "assessor import may only populate assessor_inputs",
            error_code="draft_schema_invalid",
        )
    value = write.value
    if not isinstance(value, dict) or value.get("owner") != "assessor" or value.get("import_only") is not True:
        raise DraftBuildError(
            "assessor import entries must be owner-tagged and import-only",
            error_code="draft_schema_invalid",
        )
    _require_valid_json_pointer(write.draft_pointer)
    if write.draft_pointer in provenance:
        existing_value = _value_at_json_pointer(document, write.draft_pointer)
        if existing_value == write.value:
            return
        raise DraftBuildError(
            "conflicting values map to the same canonical draft pointer",
            error_code="duplicate_canonical_id",
        )
    _set_json_pointer(document, write.draft_pointer, write.value)
    provenance[write.draft_pointer] = {
        "source_artifact_id": str(write.source_artifact_id).lower(),
        "source_sha256": write.source_sha256,
        "source_locator": write.source_locator,
        "extraction_method": write.extraction_method,
        "model_step_id": None,
        "owner": "assessor",
        "import_only": True,
    }


def _commit_field(
    *,
    document: dict[str, Any],
    provenance: dict[str, Any],
    write: _ProvenanceWrite,
) -> None:
    if write.draft_pointer.startswith(_ASSESSOR_OWNED_DRAFT_PREFIXES):
        raise DraftBuildError(
            "owner uploads cannot populate assessor-owned draft fields",
            error_code="draft_schema_invalid",
        )
    _require_valid_json_pointer(write.draft_pointer)
    if write.draft_pointer in provenance:
        existing_value = _value_at_json_pointer(document, write.draft_pointer)
        if existing_value == write.value:
            return
        raise DraftBuildError(
            "conflicting values map to the same canonical draft pointer",
            error_code="duplicate_canonical_id",
        )
    _set_json_pointer(document, write.draft_pointer, write.value)
    provenance[write.draft_pointer] = {
        "source_artifact_id": str(write.source_artifact_id).lower(),
        "source_sha256": write.source_sha256,
        "source_locator": write.source_locator,
        "extraction_method": write.extraction_method,
        "model_step_id": None,
    }


def _queue_field(
    *,
    pending_writes: dict[str, Any],
    draft_pointer: str,
    value: Any,
    artifact: SourceArtifact,
    source_locator: dict[str, Any],
    extraction_method: str,
) -> None:
    write = _ProvenanceWrite(
        draft_pointer=draft_pointer,
        value=value,
        source_artifact_id=artifact.artifact_id,
        source_sha256=artifact.sha256,
        source_locator=source_locator,
        extraction_method=extraction_method,
    )
    existing = pending_writes.get(draft_pointer)
    if existing is None:
        pending_writes[draft_pointer] = write
        return
    raise DraftBuildError(
        "multiple sources map to the same canonical draft pointer",
        error_code="duplicate_canonical_id",
    )


def _require_complete_artifact_outcomes(
    *,
    artifacts: Sequence[SourceArtifact],
    artifact_outcomes: Sequence[tuple[SourceArtifact, ExtractionOutcome]],
) -> None:
    artifact_ids = [artifact.artifact_id for artifact in artifacts]
    outcome_artifact_ids = [artifact.artifact_id for artifact, _ in artifact_outcomes]
    if len(set(artifact_ids)) != len(artifact_ids):
        raise DraftBuildError(
            "revision artifact list contains duplicate artifact IDs",
            error_code="duplicate_canonical_id",
        )
    if len(set(outcome_artifact_ids)) != len(outcome_artifact_ids):
        raise DraftBuildError(
            "artifact extraction outcomes contain duplicate artifact IDs",
            error_code="duplicate_canonical_id",
        )
    if set(outcome_artifact_ids) != set(artifact_ids):
        raise DraftBuildError(
            "artifact extraction outcomes do not match revision artifacts",
            error_code="draft_schema_invalid",
        )


def _apply_owner_manifest_mapping(
    *,
    document: dict[str, Any],
    manifest: dict[str, Any],
    artifact: SourceArtifact,
    profile_id: str,
    revision: PackageRevision,
    pending_writes: dict[str, Any],
) -> None:
    package = manifest.get("package")
    if isinstance(package, dict):
        _map_package_metadata(
            pending_writes=pending_writes,
            package=package,
            artifact=artifact,
            profile_id=profile_id,
        )

    system_section = manifest.get("system")
    if isinstance(system_section, dict):
        _map_system_section(
            pending_writes=pending_writes,
            system_section=system_section,
            package=package if isinstance(package, dict) else {},
            revision=revision,
            profile_id=profile_id,
            artifact=artifact,
        )

    contacts = manifest.get("contacts")
    if isinstance(contacts, dict):
        _map_contacts_section(
            pending_writes=pending_writes,
            contacts=contacts,
            artifact=artifact,
        )

    security_controls = manifest.get("security_controls")
    if isinstance(security_controls, dict):
        _map_security_controls_section(
            pending_writes=pending_writes,
            security_controls=security_controls,
            artifact=artifact,
        )

    evidence = manifest.get("evidence")
    if isinstance(evidence, dict):
        _map_object_section(
            pending_writes=pending_writes,
            draft_prefix="/evidence",
            section=evidence,
            artifact=artifact,
        )

    fisma_section = manifest.get("fisma_agency_security")
    if isinstance(fisma_section, dict) and profile_id == "fisma_agency_security":
        _map_fisma_profile_section(
            pending_writes=pending_writes,
            section=fisma_section,
            artifact=artifact,
        )

    _preserve_unmapped_manifest_fields(
        document=document,
        manifest=manifest,
        profile_id=profile_id,
        artifact=artifact,
    )


def _map_package_metadata(
    *,
    pending_writes: dict[str, Any],
    package: dict[str, Any],
    artifact: SourceArtifact,
    profile_id: str,
) -> None:
    for source_key, draft_pointer in (
        ("title", "/package/title"),
        ("prepared_for", "/package/prepared_for"),
        ("reporting_period", "/package/reporting_period"),
    ):
        if source_key not in package:
            continue
        value = package[source_key]
        if draft_pointer == "/package/reporting_period" and value is None:
            continue
        _queue_field(
            pending_writes=pending_writes,
            draft_pointer=draft_pointer,
            value=value,
            artifact=artifact,
            source_locator={
                "kind": "json_pointer",
                "json_pointer": f"/package/{source_key}",
            },
            extraction_method="deterministic",
        )
    declared_profile = package.get("profile_id")
    if declared_profile is not None and declared_profile != profile_id:
        raise DraftBuildError(
            "upload manifest profile_id does not match revision profile",
            error_code="draft_schema_invalid",
        )


def _map_system_section(
    *,
    pending_writes: dict[str, Any],
    system_section: dict[str, Any],
    package: dict[str, Any],
    revision: PackageRevision,
    profile_id: str,
    artifact: SourceArtifact,
) -> None:
    display_name = system_section.get("display_name", system_section.get("name"))
    if display_name is not None:
        source_pointer = (
            "/system/display_name"
            if "display_name" in system_section
            else "/system/name"
        )
        _queue_field(
            pending_writes=pending_writes,
            draft_pointer="/system/display_name",
            value=display_name,
            artifact=artifact,
            source_locator={"kind": "json_pointer", "json_pointer": source_pointer},
            extraction_method="deterministic",
        )

    mission_summary = system_section.get(
        "mission_summary", system_section.get("description")
    )
    if mission_summary is not None:
        source_pointer = (
            "/system/mission_summary"
            if "mission_summary" in system_section
            else "/system/description"
        )
        _queue_field(
            pending_writes=pending_writes,
            draft_pointer="/system/mission_summary",
            value=mission_summary,
            artifact=artifact,
            source_locator={"kind": "json_pointer", "json_pointer": source_pointer},
            extraction_method="deterministic",
        )

    if "authorization_boundary" in system_section:
        _queue_field(
            pending_writes=pending_writes,
            draft_pointer="/system/authorization_boundary",
            value=system_section["authorization_boundary"],
            artifact=artifact,
            source_locator={
                "kind": "json_pointer",
                "json_pointer": "/system/authorization_boundary",
            },
            extraction_method="deterministic",
        )

    impact_level = (
        system_section.get("impact_level")
        or package.get("impact_level")
        or revision.impact_level
    )
    if impact_level is not None and profile_id != "fedramp_20x_program":
        source_pointer = (
            "/system/impact_level"
            if "impact_level" in system_section
            else "/package/impact_level"
        )
        _queue_field(
            pending_writes=pending_writes,
            draft_pointer="/system/impact_level",
            value=impact_level,
            artifact=artifact,
            source_locator={"kind": "json_pointer", "json_pointer": source_pointer},
            extraction_method="deterministic",
        )

    if "authorization_path" in system_section:
        _queue_field(
            pending_writes=pending_writes,
            draft_pointer="/system/authorization_path",
            value=system_section["authorization_path"],
            artifact=artifact,
            source_locator={
                "kind": "json_pointer",
                "json_pointer": "/system/authorization_path",
            },
            extraction_method="deterministic",
        )


def _map_contacts_section(
    *,
    pending_writes: dict[str, Any],
    contacts: dict[str, Any],
    artifact: SourceArtifact,
) -> None:
    for role in (
        "system_owner",
        "isso",
        "issm",
        "control_owners",
        "assessors",
        "approvers",
    ):
        if role not in contacts:
            continue
        if role == "assessors":
            continue
        entries = _normalize_contact_entries(contacts[role], role=role)
        if not entries:
            continue
        _queue_field(
            pending_writes=pending_writes,
            draft_pointer=f"/contacts/{role}",
            value=entries,
            artifact=artifact,
            source_locator={
                "kind": "json_pointer",
                "json_pointer": f"/contacts/{role}",
            },
            extraction_method="deterministic",
        )


def _map_security_controls_section(
    *,
    pending_writes: dict[str, Any],
    security_controls: dict[str, Any],
    artifact: SourceArtifact,
) -> None:
    mapped_controls: dict[str, Any] = {}
    for control_id, raw_entry in security_controls.items():
        if not isinstance(raw_entry, dict):
            continue
        entry = dict(raw_entry)
        if "implementation_statement" not in entry and "summary" in entry:
            entry["implementation_statement"] = entry.pop("summary")
        entry.setdefault("responsible_parties", [])
        entry.setdefault("evidence_links", [])
        if "implementation_status" not in entry:
            continue
        mapped_controls[control_id] = {
            "implementation_status": entry["implementation_status"],
            "implementation_statement": entry.get("implementation_statement", ""),
            "responsible_parties": entry.get("responsible_parties", []),
            "evidence_links": entry.get("evidence_links", []),
        }
    if not mapped_controls:
        return
    _queue_field(
        pending_writes=pending_writes,
        draft_pointer="/security_controls",
        value=mapped_controls,
        artifact=artifact,
        source_locator={
            "kind": "json_pointer",
            "json_pointer": "/security_controls",
        },
        extraction_method="deterministic",
    )


def _map_object_section(
    *,
    pending_writes: dict[str, Any],
    draft_prefix: str,
    section: dict[str, Any],
    artifact: SourceArtifact,
) -> None:
    if not section:
        return
    _queue_field(
        pending_writes=pending_writes,
        draft_pointer=draft_prefix,
        value=section,
        artifact=artifact,
        source_locator={"kind": "json_pointer", "json_pointer": draft_prefix},
        extraction_method="deterministic",
    )


def _map_fisma_profile_section(
    *,
    pending_writes: dict[str, Any],
    section: dict[str, Any],
    artifact: SourceArtifact,
) -> None:
    if not section:
        return
    _queue_field(
        pending_writes=pending_writes,
        draft_pointer="/fisma_agency_security",
        value=section,
        artifact=artifact,
        source_locator={
            "kind": "json_pointer",
            "json_pointer": "/fisma_agency_security",
        },
        extraction_method="deterministic",
    )


def _preserve_unmapped_manifest_fields(
    *,
    document: dict[str, Any],
    manifest: dict[str, Any],
    profile_id: str,
    artifact: SourceArtifact,
) -> None:
    reserved = {
        "package",
        "system",
        "contacts",
        "security_controls",
        "evidence",
        "fisma_agency_security",
    }
    customer_fields: dict[str, Any] = {}
    for key, value in manifest.items():
        if key in reserved:
            continue
        customer_fields[key] = value

    package = manifest.get("package")
    if isinstance(package, dict):
        for key in ("data_origin", "sensitivity", "impact_level"):
            if key in package:
                customer_fields.setdefault("package_metadata", {})[key] = package[key]

    system_section = manifest.get("system")
    if isinstance(system_section, dict):
        for key in ("owner", "operating_environment"):
            if key in system_section:
                customer_fields.setdefault("system_metadata", {})[key] = system_section[key]

    if not customer_fields:
        return

    if profile_id == "fisma_agency_security":
        fisma = document.get("fisma_agency_security")
        if not isinstance(fisma, dict):
            fisma = {"security_plan_sections": {}}
            document["fisma_agency_security"] = fisma
        existing = fisma.get("customer_defined_fields")
        if not isinstance(existing, dict):
            existing = {}
        existing.update(customer_fields)
        fisma["customer_defined_fields"] = existing
        return

    document.setdefault("extensions", {})
    document["extensions"].setdefault("upload_metadata", {})
    document["extensions"]["upload_metadata"][str(artifact.artifact_id).lower()] = (
        customer_fields
    )


def _empty_profile_shell(*, profile_id: str, system: System) -> dict[str, Any]:
    shell: dict[str, Any] = {
        "package": {
            "profile_id": profile_id,
            "title": "",
            "prepared_for": "",
            "reporting_period": None,
        },
        "system": {
            "display_name": system.display_name,
            "authorization_boundary": "",
            "mission_summary": "",
            "impact_level": None if profile_id == "fedramp_20x_program" else None,
            "authorization_path": _default_authorization_path(profile_id),
        },
        "contacts": {
            "system_owner": [],
            "isso": [],
            "issm": [],
            "control_owners": [],
            "assessors": [],
            "approvers": [],
        },
        "control_set": {
            "source": {},
            "tailoring": [],
            "organization_defined_parameters": {},
            "inheritance": [],
        },
        "security_controls": {},
        "evidence": {},
        "findings": {},
        "poam_candidates": {},
        "assessor_inputs": {},
        "privacy": {
            "artifacts_present": False,
            "scope_notice": _DEFAULT_PRIVACY_SCOPE_NOTICE,
        },
        "fedramp_20x": None,
        "fedramp_rev5_transition": None,
        "fisma_agency_security": None,
        "extensions": {},
    }
    if profile_id == "fisma_agency_security":
        shell["fisma_agency_security"] = {"security_plan_sections": {}}
    elif profile_id == "fedramp_20x_program":
        shell["system"]["impact_level"] = None
        shell["fedramp_20x"] = {
            "cpo": {},
            "sdr": {},
            "ocr": {},
            "scg": {},
            "ksi_methods": [],
            "metric_history": [],
            "independent_assessment": {},
        }
    elif profile_id == "fedramp_rev5_transition":
        shell["fedramp_rev5_transition"] = {
            "ssp": {},
            "sap": {},
            "sar": {},
            "poam": {},
            "oscal": {},
        }
    return shell


def _build_system_context_proposal(
    *,
    document: dict[str, Any],
    provenance: dict[str, Any],
    system: System,
) -> dict[str, Any] | None:
    system_section = document.get("system")
    if not isinstance(system_section, dict):
        return None

    proposal_document = {
        "display_name": system_section.get("display_name") or system.display_name,
        "authorization_boundary": system_section.get("authorization_boundary", ""),
        "mission_summary": system_section.get("mission_summary", ""),
        "impact_level": system_section.get("impact_level"),
        "authorization_path": system_section.get(
            "authorization_path",
            _default_authorization_path(document["package"]["profile_id"]),
        ),
    }
    proposal_provenance = {
        pointer: entry
        for pointer, entry in provenance.items()
        if pointer.startswith("/system/")
    }
    if not proposal_provenance:
        return None
    return {
        "status": "proposed",
        "document": proposal_document,
        "field_provenance": proposal_provenance,
        "requires_human_approval": True,
    }


def _looks_like_owner_manifest(document: dict[str, Any]) -> bool:
    return isinstance(document.get("package"), dict) or isinstance(
        document.get("system"), dict
    )


def _reconstruct_json_document(segments: Sequence[ExtractedSegment]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for segment in segments:
        pointer = segment.locator.get("json_pointer")
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            continue
        _set_json_pointer(document, pointer, _parse_segment_value(segment.text))
    return document


def _parse_segment_value(text: str) -> Any:
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "null":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _structured_extension_segments(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": str(artifact.artifact_id).lower(),
            "detected_format": outcome.detected_format,
            "segment_index": segment.segment_index,
            "text": segment.text,
            "source_locator": segment.locator,
            "extraction_method": segment.extraction_method,
        }
        for segment in outcome.segments
    ]


def _narrative_extension_segments(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
) -> list[dict[str, Any]]:
    return _structured_extension_segments(artifact=artifact, outcome=outcome)


def _evidence_only_record(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
) -> dict[str, Any]:
    return {
        "artifact_id": str(artifact.artifact_id).lower(),
        "detected_format": outcome.detected_format,
        "vision_status": outcome.vision_status,
        "status": outcome.status,
    }


def _vision_deferred_record(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
) -> dict[str, Any]:
    return _evidence_only_record(artifact=artifact, outcome=outcome)


def _normalize_contact_entries(value: Any, *, role: str) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [
            {
                "name": role.replace("_", " ").title(),
                "role": role,
                "email": value,
            }
        ]
    if isinstance(value, list):
        entries: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                entries.append(
                    {
                        "name": role.replace("_", " ").title(),
                        "role": role,
                        "email": item,
                    }
                )
            elif isinstance(item, dict):
                name = item.get("name") or role.replace("_", " ").title()
                email = item.get("email")
                if not isinstance(email, str):
                    continue
                entry = {
                    "name": name,
                    "role": item.get("role") or role,
                    "email": email,
                }
                if isinstance(item.get("organization"), str):
                    entry["organization"] = item["organization"]
                if isinstance(item.get("phone"), str):
                    entry["phone"] = item["phone"]
                entries.append(entry)
        return entries
    return []


def _default_authorization_path(profile_id: str) -> str:
    if profile_id in {"fedramp_20x_program", "fedramp_rev5_transition"}:
        return "fedramp"
    return "agency"


def _require_valid_json_pointer(pointer: str) -> None:
    if len(pointer) > 2000 or _JSON_POINTER_PATTERN.fullmatch(pointer) is None:
        raise DraftBuildError(
            "draft JSON pointer exceeds domain limits",
            error_code="draft_schema_invalid",
        )


def _set_json_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    if pointer == "":
        raise DraftBuildError(
            "cannot set the empty JSON pointer on a draft document",
            error_code="draft_schema_invalid",
        )
    parts = pointer.lstrip("/").split("/")
    current: Any = document
    for index, raw_part in enumerate(parts):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        is_last = index == len(parts) - 1
        if is_last:
            if isinstance(current, dict):
                current[part] = value
            elif isinstance(current, list):
                current[int(part)] = value
            return
        if isinstance(current, dict):
            next_value = current.get(part)
            if next_value is None:
                next_value = {}
                current[part] = next_value
            current = next_value
        elif isinstance(current, list):
            current = current[int(part)]


def _value_at_json_pointer(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    if pointer == "":
        return document
    for raw_part in pointer.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
    return current


def _format_validation_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"{path}: {error.message}"
    return error.message


@cache
def _package_draft_schema_path() -> Path:
    return contract_path("package-draft-document.schema.json")


@cache
def _package_draft_validator() -> Draft202012Validator:
    schema = json.loads(_package_draft_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    validator.check_schema(schema)
    return validator


__all__ = [
    "AggregatedIntakeDraft",
    "DOCUMENT_SCHEMA_VERSION",
    "DraftBuildError",
    "build_initial_draft",
    "validate_package_draft_document",
]
