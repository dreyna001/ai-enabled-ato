"""Draft OSCAL 1.2.2 SSP JSON export from approved workspace snapshots.

This module produces structurally schema-valid draft OSCAL SSP JSON only. It does
not claim OSCAL SSP completeness, FedRAMP conformance, or authorization readiness.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ato_service.project_root import find_project_root
from ato_service.oscal_catalog import normalize_oscal_control_id
from ato_service.oscal_ssp_schema import OSCAL_VERSION, validate_oscal_ssp_document
from ato_service.ssp_workspace.export import (
    WorkspaceExportValidationError,
    normalize_export_snapshot,
)

_EXPORT_PROP_NS = "https://ato-analyzer.local/ns/ssp-workspace-export/v1"
_OSCAL_ID_NAMESPACE = uuid.UUID("7b2f4c9e-1a6d-4f0e-9c3b-8d5e2a1f9047")
_UNRESOLVED_SYSTEM_DESCRIPTION = (
    "System description unresolved: no purpose or description section was present "
    "in the approved snapshot."
)
_UNRESOLVED_AUTHORIZATION_BOUNDARY = (
    "Authorization boundary unresolved: no authorization-boundary section was "
    "present in the approved snapshot."
)
_UNRESOLVED_INFORMATION_TYPES = (
    "Information types unresolved: no information-type section was present in the "
    "approved snapshot."
)
_HS001_DISCLAIMER = (
    "HS-001: Authority sources in docs/contracts/authority-manifest.json remain "
    "draft or pending review; this export does not assert authority qualification."
)
_HS002_DISCLAIMER = (
    "HS-002: Agency template parity is not claimed; this export is draft working "
    "material from the internal SSP workspace."
)
_IMPLEMENTATION_STATUS_TO_OSCAL = {
    "implemented": "implemented",
    "partially_implemented": "partial",
    "planned": "planned",
    "not_applicable": "not-applicable",
}
_PURPOSE_SECTION_IDS = frozenset(
    {
        "purpose",
        "system_purpose",
        "system-purpose",
        "description",
        "system_description",
        "system-description",
    }
)
_BOUNDARY_SECTION_IDS = frozenset(
    {
        "boundary",
        "authorization_boundary",
        "authorization-boundary",
    }
)
_INFORMATION_TYPE_SECTION_IDS = frozenset(
    {
        "information_types",
        "information-types",
        "information_type",
        "information-type",
    }
)


class OscalSspExportError(ValueError):
    """Raised when a snapshot cannot be exported as draft OSCAL SSP JSON."""


def build_draft_oscal_ssp_json_export(
    snapshot: dict[str, Any],
    *,
    include_open_questions: bool,
    project_root: Path | None = None,
) -> bytes:
    """Return canonical UTF-8 OSCAL SSP JSON for one approved workspace snapshot."""
    root = (project_root or find_project_root()).resolve()
    try:
        normalized = normalize_export_snapshot(
            snapshot,
            include_open_questions=include_open_questions,
        )
    except WorkspaceExportValidationError as error:
        raise OscalSspExportError(str(error)) from error

    document = _build_oscal_document(normalized, include_open_questions=include_open_questions)
    try:
        validate_oscal_ssp_document(document, project_root=root)
    except ValueError as error:
        raise OscalSspExportError(str(error)) from error

    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _build_oscal_document(
    normalized: dict[str, Any],
    *,
    include_open_questions: bool,
) -> dict[str, Any]:
    workspace_id = normalized["workspace_id"]
    revision_id = normalized["revision_id"]
    content_sha256 = normalized["content_sha256"]
    profile = normalized["profile"]
    system = normalized["system"]

    purpose_section, boundary_section, information_section, unmapped_sections = (
        _partition_sections(normalized["sections"])
    )
    system_description = (
        (purpose_section["content"] or "").strip()
        if purpose_section is not None
        else _UNRESOLVED_SYSTEM_DESCRIPTION
    )
    authorization_boundary = (
        (boundary_section["content"] or "").strip()
        if boundary_section is not None
        else _UNRESOLVED_AUTHORIZATION_BOUNDARY
    )

    ssp_uuid = _stable_uuid(
        "ssp",
        workspace_id,
        revision_id,
    )
    component_uuid = _stable_uuid("component", workspace_id, content_sha256)
    information_type_uuid = _stable_uuid("information-type", workspace_id, content_sha256)
    profile_href = _profile_href(
        profile_id=profile["profile_id"],
        version=profile["version"],
    )

    information_types = _build_information_types(
        information_section,
        information_type_uuid=information_type_uuid,
    )
    implemented_requirements = [
        _build_implemented_requirement(
            control=control,
            workspace_id=workspace_id,
            revision_id=revision_id,
            content_sha256=content_sha256,
            component_uuid=component_uuid,
        )
        for control in normalized["controls"]
    ]
    back_matter = _build_back_matter(
        unmapped_sections=unmapped_sections,
        open_questions=normalized["open_questions"] if include_open_questions else [],
        workspace_id=workspace_id,
        revision_id=revision_id,
        content_sha256=content_sha256,
    )

    ssp: dict[str, Any] = {
        "uuid": ssp_uuid,
        "metadata": _build_metadata(normalized),
        "import-profile": {"href": profile_href},
        "system-characteristics": {
            "system-ids": [_build_system_id(system, workspace_id=workspace_id)],
            "system-name": system["display_name"],
            "description": system_description,
            "system-information": {"information-types": information_types},
            "status": {
                "state": "other",
                "remarks": (
                    "Operational authorization status is unknown; this draft export "
                    "does not assert an operational system."
                ),
            },
            "authorization-boundary": {"description": authorization_boundary},
        },
        "system-implementation": {
            "components": [
                {
                    "uuid": component_uuid,
                    "type": "this-system",
                    "title": system["display_name"],
                    "description": (
                        "Synthetic OSCAL component representing the current system "
                        "described by the approved workspace snapshot."
                    ),
                    "status": {"state": "other"},
                }
            ]
        },
        "control-implementation": {
            "description": (
                "Draft control implementation mapped from the approved internal SSP "
                "workspace snapshot."
            ),
            "implemented-requirements": implemented_requirements,
        },
    }
    if back_matter is not None:
        ssp["back-matter"] = back_matter
    return {"system-security-plan": ssp}


def _build_metadata(normalized: dict[str, Any]) -> dict[str, Any]:
    profile = normalized["profile"]
    return {
        "title": f"{normalized['system']['display_name']} System Security Plan (Draft)",
        "last-modified": normalized["approved_at"],
        "version": "draft",
        "oscal-version": OSCAL_VERSION,
        "props": [
            _export_prop("export-kind", "draft"),
            _export_prop("workspace-id", normalized["workspace_id"]),
            _export_prop("revision-id", normalized["revision_id"]),
            _export_prop("content-sha256", normalized["content_sha256"]),
            _export_prop("profile-id", profile["profile_id"]),
            _export_prop("profile-version", profile["version"]),
            _export_prop("hs-001-disclaimer", _HS001_DISCLAIMER),
            _export_prop("hs-002-disclaimer", _HS002_DISCLAIMER),
        ],
    }


def _build_system_id(system: dict[str, Any], *, workspace_id: str) -> dict[str, str]:
    external_system_id = system.get("external_system_id")
    if isinstance(external_system_id, str) and external_system_id.strip():
        return {
            "identifier-type": "https://ato-analyzer.local/id/system",
            "id": external_system_id.strip(),
        }
    return {
        "identifier-type": "https://ato-analyzer.local/id/workspace",
        "id": workspace_id,
    }


def _build_information_types(
    information_section: dict[str, Any] | None,
    *,
    information_type_uuid: str,
) -> list[dict[str, Any]]:
    if information_section is not None:
        content = (information_section.get("content") or "").strip()
        title = information_section.get("title") or "Information Types"
        return [
            {
                "uuid": information_type_uuid,
                "title": title,
                "description": content or _UNRESOLVED_INFORMATION_TYPES,
            }
        ]
    return [
        {
            "uuid": information_type_uuid,
            "title": "Unresolved information types",
            "description": _UNRESOLVED_INFORMATION_TYPES,
        }
    ]


def _build_implemented_requirement(
    control: dict[str, Any],
    *,
    workspace_id: str,
    revision_id: str,
    content_sha256: str,
    component_uuid: str,
) -> dict[str, Any]:
    control_id = normalize_oscal_control_id(control["control_id"])
    requirement: dict[str, Any] = {
        "uuid": _stable_uuid(
            "implemented-requirement",
            workspace_id,
            revision_id,
            content_sha256,
            control_id,
        ),
        "control-id": control_id,
        "props": [
            _export_prop("workspace-control-state", control["state"]),
            _export_prop("responsibility", control["responsibility"]),
        ],
    }

    oscal_status = _IMPLEMENTATION_STATUS_TO_OSCAL.get(control["implementation_status"])
    if oscal_status is not None:
        requirement["props"].append(
            _export_prop("implementation-status", control["implementation_status"])
        )
    else:
        requirement["props"].append(
            _export_prop(
                "implementation-status-unmapped",
                control["implementation_status"],
            )
        )

    statement = (control.get("implementation_statement") or "").strip()
    responsibility = control["responsibility"]
    if responsibility == "inherited":
        requirement["remarks"] = _inherited_control_remarks(control, statement)
    elif statement and responsibility in {"system_specific", "hybrid"}:
        requirement["by-components"] = [
            _build_by_component(
                control=control,
                statement=statement,
                workspace_id=workspace_id,
                revision_id=revision_id,
                content_sha256=content_sha256,
                component_uuid=component_uuid,
                oscal_status=oscal_status,
            )
        ]
    elif statement:
        requirement["remarks"] = statement

    evidence_links = control.get("evidence_links") or []
    if evidence_links:
        for evidence_reference in evidence_links:
            requirement["props"].append(
                _export_prop("evidence-reference", evidence_reference)
            )
        requirement["links"] = [
            {
                "href": _evidence_href(
                    workspace_id=workspace_id,
                    content_sha256=content_sha256,
                    evidence_reference=evidence_reference,
                ),
                "rel": "evidence",
            }
            for evidence_reference in evidence_links
        ]
    return requirement


def _build_by_component(
    control: dict[str, Any],
    *,
    statement: str,
    workspace_id: str,
    revision_id: str,
    content_sha256: str,
    component_uuid: str,
    oscal_status: str | None,
) -> dict[str, Any]:
    control_id = normalize_oscal_control_id(control["control_id"])
    by_component: dict[str, Any] = {
        "component-uuid": component_uuid,
        "uuid": _stable_uuid(
            "by-component",
            workspace_id,
            revision_id,
            content_sha256,
            control_id,
        ),
        "description": statement,
    }
    if oscal_status is not None:
        by_component["implementation-status"] = {"state": oscal_status}
    return by_component


def _inherited_control_remarks(control: dict[str, Any], statement: str) -> str:
    lines = [
        "Inherited control: leveraged authorization and provider details were not "
        "fabricated for this draft export.",
        f"Responsibility: {control['responsibility']}.",
        f"Implementation status: {control['implementation_status']}.",
    ]
    if statement:
        lines.append(statement)
    return " ".join(lines)


def _build_back_matter(
    *,
    unmapped_sections: list[dict[str, Any]],
    open_questions: list[dict[str, Any]],
    workspace_id: str,
    revision_id: str,
    content_sha256: str,
) -> dict[str, Any] | None:
    resources: list[dict[str, Any]] = []
    for section in unmapped_sections:
        resources.append(
            {
                "uuid": _stable_uuid(
                    "back-matter-section",
                    workspace_id,
                    revision_id,
                    content_sha256,
                    section["section_id"],
                ),
                "title": section["title"],
                "description": (section.get("content") or "").strip()
                or "No content provided.",
                "props": [_export_prop("source-section-id", section["section_id"])],
            }
        )
    for question in open_questions:
        resources.append(
            {
                "uuid": _stable_uuid(
                    "back-matter-question",
                    workspace_id,
                    revision_id,
                    content_sha256,
                    question["question_id"],
                ),
                "title": f"Open question: {question['target']}",
                "description": (
                    f"{question['question']} (owner: {question['owner_type']})"
                ),
                "props": [
                    _export_prop("question-id", question["question_id"]),
                    _export_prop("question-status", question["status"]),
                ],
            }
        )
    if not resources:
        return None
    return {"resources": resources}


def _partition_sections(
    sections: list[dict[str, Any]],
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[dict[str, Any]],
]:
    purpose: dict[str, Any] | None = None
    boundary: dict[str, Any] | None = None
    information: dict[str, Any] | None = None
    unmapped: list[dict[str, Any]] = []

    for section in sections:
        section_id = section["section_id"]
        title = section["title"].casefold()
        if purpose is None and (
            section_id in _PURPOSE_SECTION_IDS
            or "purpose" in title
            or "system description" in title
        ):
            purpose = section
            continue
        if boundary is None and (
            section_id in _BOUNDARY_SECTION_IDS or "authorization boundary" in title
        ):
            boundary = section
            continue
        if information is None and (
            section_id in _INFORMATION_TYPE_SECTION_IDS or "information type" in title
        ):
            information = section
            continue
        unmapped.append(section)

    return purpose, boundary, information, unmapped


def _export_prop(name: str, value: str) -> dict[str, str]:
    return {"name": name, "ns": _EXPORT_PROP_NS, "value": value}


def _stable_uuid(kind: str, *parts: str) -> str:
    material = ":".join((kind, *parts))
    return str(uuid.uuid5(_OSCAL_ID_NAMESPACE, material))


def _profile_href(*, profile_id: str, version: str) -> str:
    return f"urn:ato-analyzer:profile:{profile_id}:{version}"


def _evidence_href(
    *,
    workspace_id: str,
    content_sha256: str,
    evidence_reference: str,
) -> str:
    evidence_uuid = _stable_uuid(
        "evidence-link",
        workspace_id,
        content_sha256,
        evidence_reference,
    )
    return f"urn:ato-analyzer:evidence:{evidence_uuid}"
