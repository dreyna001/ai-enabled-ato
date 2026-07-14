"""Profile-specific draft artifact generators within hard-stop boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ato_service.export_readiness import evaluate_export_readiness
from ato_service.fedramp_schema import evaluate_schema_purity

DRAFT_DISCLAIMER = (
    "DRAFT ONLY. This bundle does not constitute an official FedRAMP submission, "
    "certification, authorization, or assessor conclusion."
)
HS_001_DISCLAIMER = (
    "HS-001: Vendored authority bytes are present, but qualified human review of "
    "official schemas and rules remains pending. Official schema qualification "
    "claims are blocked."
)
HS_009_DISCLAIMER = (
    "HS-009: Assessor-owned fields are import-only. Complete Class C package "
    "readiness claims remain blocked without supplied assessor material."
)


@dataclass(frozen=True, slots=True)
class GeneratedProfileArtifacts:
    files: list[dict[str, Any]]
    contents: dict[str, bytes]


def generate_profile_artifacts(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]] | None = None,
    matrix_rows: list[dict[str, Any]] | None = None,
    project_root: Path | None = None,
) -> GeneratedProfileArtifacts:
    """Generate draft human/machine artifact descriptors without HS-001/HS-002 claims."""
    root = project_root or Path(__file__).resolve().parents[2]
    readiness = evaluate_export_readiness(
        profile_id=profile_id,
        sealed_document=sealed_document,
        project_root=root,
    )
    schema_results = evaluate_schema_purity(
        profile_id=profile_id,
        sealed_document=sealed_document,
        project_root=root,
    )

    contents: dict[str, bytes] = {}
    contents["README.txt"] = _readme_text(
        profile_id=profile_id,
        readiness=readiness,
    ).encode("utf-8")
    contents["human/readiness-summary.md"] = _readiness_summary(
        profile_id=profile_id,
        document=sealed_document,
        readiness=readiness,
    ).encode("utf-8")
    contents["machine/package-document.json"] = json.dumps(
        sealed_document,
        sort_keys=True,
    ).encode("utf-8")
    contents["provenance/review-run.json"] = json.dumps(
        {
            "review_revision_id": str(review_revision_id).lower(),
            "run_id": str(run_id).lower(),
        },
        sort_keys=True,
    ).encode("utf-8")

    if dispositions is not None:
        contents["provenance/dispositions.json"] = json.dumps(
            {"dispositions": dispositions},
            sort_keys=True,
        ).encode("utf-8")
    if matrix_rows is not None:
        contents["machine/assessment-matrix.json"] = json.dumps(
            {"rows": matrix_rows},
            sort_keys=True,
        ).encode("utf-8")
        contents["human/assessment-matrix.md"] = _assessment_matrix_markdown(
            matrix_rows=matrix_rows,
        ).encode("utf-8")

    contents["validation/export-readiness.json"] = json.dumps(
        _export_readiness_payload(
            profile_id=profile_id,
            readiness=readiness,
            schema_results=schema_results,
        ),
        sort_keys=True,
    ).encode("utf-8")
    contents["validation/schema-purity.json"] = json.dumps(
        _schema_purity_payload(schema_results=schema_results),
        sort_keys=True,
    ).encode("utf-8")

    assessor_inputs = sealed_document.get("assessor_inputs")
    if isinstance(assessor_inputs, dict) and assessor_inputs:
        contents["provenance/assessor-imports.json"] = json.dumps(
            {
                "assessor_inputs": assessor_inputs,
                "import_only": True,
                "owner": "assessor",
            },
            sort_keys=True,
        ).encode("utf-8")

    if profile_id == "fedramp_20x_program":
        _add_fedramp_20x_artifacts(
            contents=contents,
            sealed_document=sealed_document,
            readiness=readiness,
            schema_results=schema_results,
        )
    elif profile_id == "fedramp_rev5_transition":
        _add_fedramp_rev5_artifacts(
            contents=contents,
            sealed_document=sealed_document,
            readiness=readiness,
        )
    elif profile_id == "fisma_agency_security":
        section = sealed_document.get("fisma_agency_security") or {}
        contents["machine/fisma-agency-security-draft.json"] = json.dumps(
            section,
            sort_keys=True,
        ).encode("utf-8")

    files = [
        _descriptor_for_path(path=path, contents=payload, official_schema_id=None)
        for path, payload in sorted(contents.items())
    ]
    _apply_official_schema_ids(files=files, schema_results=schema_results)
    return GeneratedProfileArtifacts(files=files, contents=contents)


def build_profile_artifact_contents(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]],
    matrix_rows: list[dict[str, Any]],
    project_root: Path | None = None,
) -> dict[str, bytes]:
    """Return path -> bytes for every generated export artifact."""
    artifacts = generate_profile_artifacts(
        profile_id=profile_id,
        sealed_document=sealed_document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
        project_root=project_root,
    )
    return dict(artifacts.contents)


def _add_fedramp_20x_artifacts(
    *,
    contents: dict[str, bytes],
    sealed_document: dict[str, Any],
    readiness: Any,
    schema_results: list[Any],
) -> None:
    section = sealed_document.get("fedramp_20x")
    if not isinstance(section, dict):
        section = {}

    for artifact_key, machine_path, human_path in (
        ("cpo", "machine/cpo.json", "human/cpo.md"),
        ("sdr", "machine/sdr.json", "human/sdr.md"),
        ("ocr", "machine/ocr.json", "human/ocr.md"),
    ):
        payload = section.get(artifact_key) or {}
        contents[machine_path] = json.dumps(payload, sort_keys=True).encode("utf-8")
        validation = next(
            (result for result in schema_results if result.artifact_key == artifact_key),
            None,
        )
        contents[human_path] = _official_artifact_markdown(
            title=artifact_key.upper(),
            payload=payload,
            validation=validation,
        ).encode("utf-8")

    scg = section.get("scg") or {}
    contents["human/scg-readiness.md"] = _scg_readiness_markdown(scg=scg).encode("utf-8")

    ksi_methods = section.get("ksi_methods") or []
    metric_history = section.get("metric_history") or []
    contents["machine/ksi-summary.json"] = json.dumps(
        {
            "draft_only": True,
            "product_analysis": True,
            "ksi_methods": ksi_methods,
            "metric_history": metric_history,
            "method_count": len(ksi_methods) if isinstance(ksi_methods, list) else 0,
            "metric_count": len(metric_history) if isinstance(metric_history, list) else 0,
        },
        sort_keys=True,
    ).encode("utf-8")
    contents["human/ksi-summary.md"] = _ksi_summary_markdown(
        ksi_methods=ksi_methods,
        metric_history=metric_history,
    ).encode("utf-8")

    independent_assessment = section.get("independent_assessment") or {}
    contents["machine/fedramp-readiness.json"] = json.dumps(
        {
            "profile_id": "fedramp_20x_program",
            "draft_only": True,
            "blockers": list(readiness.blockers),
            "warnings": list(readiness.warnings),
            "independent_assessment_present": bool(independent_assessment),
            "ksi_method_count": len(ksi_methods) if isinstance(ksi_methods, list) else 0,
        },
        sort_keys=True,
    ).encode("utf-8")
    contents["human/fedramp-readiness.md"] = _fedramp_readiness_markdown(
        readiness=readiness,
        independent_assessment_present=bool(independent_assessment),
        ksi_method_count=len(ksi_methods) if isinstance(ksi_methods, list) else 0,
    ).encode("utf-8")


def _add_fedramp_rev5_artifacts(
    *,
    contents: dict[str, bytes],
    sealed_document: dict[str, Any],
    readiness: Any,
) -> None:
    section = sealed_document.get("fedramp_rev5_transition")
    if not isinstance(section, dict):
        section = {}

    for artifact_key, machine_path, human_path in (
        ("ssp", "machine/ssp.json", "human/ssp.md"),
        ("sap", "machine/sap.json", "human/sap.md"),
        ("sar", "machine/sar.json", "human/sar.md"),
        ("poam", "machine/poam.json", "human/poam.md"),
    ):
        payload = section.get(artifact_key) or {}
        contents[machine_path] = json.dumps(payload, sort_keys=True).encode("utf-8")
        contents[human_path] = _imported_artifact_markdown(
            title=artifact_key.upper(),
            payload=payload,
            assessor_owned=artifact_key == "sar",
        ).encode("utf-8")

    oscal = section.get("oscal") or {}
    if oscal:
        contents["machine/oscal.json"] = json.dumps(oscal, sort_keys=True).encode("utf-8")
        contents["human/oscal.md"] = _imported_artifact_markdown(
            title="OSCAL",
            payload=oscal,
            assessor_owned=False,
        ).encode("utf-8")

    contents["machine/rev5-transition-readiness.json"] = json.dumps(
        {
            "profile_id": "fedramp_rev5_transition",
            "draft_only": True,
            "blockers": list(readiness.blockers),
            "warnings": list(readiness.warnings),
            "imported_sections": {
                key: bool(section.get(key))
                for key in ("ssp", "sap", "sar", "poam", "oscal")
            },
        },
        sort_keys=True,
    ).encode("utf-8")
    contents["human/rev5-transition-readiness.md"] = _rev5_readiness_markdown(
        readiness=readiness,
        section=section,
    ).encode("utf-8")


def _readme_text(*, profile_id: str, readiness: Any) -> str:
    lines = [
        DRAFT_DISCLAIMER,
        "",
        f"Profile: {profile_id}",
        HS_001_DISCLAIMER,
        HS_009_DISCLAIMER,
        "",
        "Open export-readiness blockers:",
    ]
    if readiness.blockers:
        lines.extend(f"- {blocker}" for blocker in readiness.blockers)
    else:
        lines.append("- none")
    lines.append("")
    lines.append(
        "Official schema qualification and complete package readiness claims remain "
        "blocked while HS-001 and HS-009 are open."
    )
    return "\n".join(lines)


def _readiness_summary(
    *,
    profile_id: str,
    document: dict[str, Any],
    readiness: Any,
) -> str:
    privacy = document.get("privacy", {})
    assessor_count = len(document.get("assessor_inputs") or {})
    lines = [
        "# Draft readiness summary",
        "",
        DRAFT_DISCLAIMER,
        "",
        f"Profile: {profile_id}",
        f"Assessor imports: {assessor_count}",
        f"Privacy artifacts present: {privacy.get('artifacts_present', False)}",
        f"Scope notice: {privacy.get('scope_notice', '')}",
        "",
        "## Hard-stop disclaimers",
        "",
        f"- {HS_001_DISCLAIMER}",
        f"- {HS_009_DISCLAIMER}",
        "",
        "## Export blockers",
        "",
    ]
    if readiness.blockers:
        lines.extend(f"- {blocker}" for blocker in readiness.blockers)
    else:
        lines.append("- none")
    if readiness.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in readiness.warnings)
    return "\n".join(lines) + "\n"


def _assessment_matrix_markdown(*, matrix_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Assessment matrix (draft)",
        "",
        DRAFT_DISCLAIMER,
        "",
        "| Item | Model status | System status | Summary |",
        "| --- | --- | --- | --- |",
    ]
    for row in matrix_rows:
        lines.append(
            "| {item} | {model} | {system} | {summary} |".format(
                item=row.get("assessment_item_id", ""),
                model=row.get("model_proposed_status", ""),
                system=row.get("system_status", ""),
                summary=(row.get("finding_summary") or "").replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _official_artifact_markdown(
    *,
    title: str,
    payload: dict[str, Any],
    validation: Any | None,
) -> str:
    lines = [
        f"# {title} draft",
        "",
        DRAFT_DISCLAIMER,
        "",
        "This human-readable draft summarizes supplied official-shaped JSON only. "
        "No assessor conclusions or official fields were invented.",
        "",
    ]
    if validation is not None:
        lines.append(f"Schema available: {validation.schema_available}")
        lines.append(f"Structurally valid: {validation.structurally_valid}")
        if validation.errors:
            lines.append("")
            lines.append("## Structural validation notes")
            lines.extend(f"- {error}" for error in validation.errors[:10])
        lines.append("")
    if payload:
        lines.append("## Supplied top-level fields")
        lines.extend(f"- {key}" for key in sorted(payload))
    else:
        lines.append("No official-shaped payload was supplied for this artifact.")
    return "\n".join(lines) + "\n"


def _imported_artifact_markdown(
    *,
    title: str,
    payload: dict[str, Any],
    assessor_owned: bool,
) -> str:
    lines = [
        f"# {title} import draft",
        "",
        DRAFT_DISCLAIMER,
        "",
    ]
    if assessor_owned:
        lines.append(
            "Assessor-owned import only. The product does not generate or alter "
            "assessor conclusions."
        )
        lines.append("")
    lines.append(
        "This draft preserves imported material and provenance references only."
    )
    lines.append("")
    if payload:
        lines.append("## Imported top-level fields")
        lines.extend(f"- {key}" for key in sorted(payload))
    else:
        lines.append("No imported payload was supplied for this artifact.")
    return "\n".join(lines) + "\n"


def _scg_readiness_markdown(*, scg: dict[str, Any]) -> str:
    lines = [
        "# SCG readiness (draft)",
        "",
        DRAFT_DISCLAIMER,
        "",
        "Secure configuration guidance is provider-owned. The product does not "
        "invent product settings or secure defaults.",
        "",
    ]
    if scg:
        lines.append("## Supplied SCG reference fields")
        lines.extend(f"- {key}" for key in sorted(scg))
    else:
        lines.append("No SCG reference material was supplied.")
    return "\n".join(lines) + "\n"


def _ksi_summary_markdown(
    *,
    ksi_methods: Any,
    metric_history: Any,
) -> str:
    method_count = len(ksi_methods) if isinstance(ksi_methods, list) else 0
    metric_count = len(metric_history) if isinstance(metric_history, list) else 0
    lines = [
        "# KSI summary (product analysis)",
        "",
        DRAFT_DISCLAIMER,
        "",
        "Auxiliary product analysis only. KSI validation methods are not operated "
        "by this product.",
        "",
        f"Imported validation methods: {method_count}",
        f"Imported metric history entries: {metric_count}",
    ]
    return "\n".join(lines) + "\n"


def _fedramp_readiness_markdown(
    *,
    readiness: Any,
    independent_assessment_present: bool,
    ksi_method_count: int,
) -> str:
    lines = [
        "# FedRAMP 20x package readiness (draft)",
        "",
        DRAFT_DISCLAIMER,
        "",
        f"Independent assessment import present: {independent_assessment_present}",
        f"Imported KSI validation methods: {ksi_method_count}",
        "",
        "## Export blockers",
        "",
    ]
    if readiness.blockers:
        lines.extend(f"- {blocker}" for blocker in readiness.blockers)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _rev5_readiness_markdown(*, readiness: Any, section: dict[str, Any]) -> str:
    lines = [
        "# FedRAMP Rev. 5 transition readiness (draft)",
        "",
        DRAFT_DISCLAIMER,
        "",
        "Imported SSP/SAP/SAR/POA&M/OSCAL material is preserved without "
        "generating assessor conclusions.",
        "",
        "## Imported sections",
        "",
    ]
    for key in ("ssp", "sap", "sar", "poam", "oscal"):
        lines.append(f"- {key}: {'present' if section.get(key) else 'missing'}")
    lines.extend(["", "## Export blockers", ""])
    if readiness.blockers:
        lines.extend(f"- {blocker}" for blocker in readiness.blockers)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _export_readiness_payload(
    *,
    profile_id: str,
    readiness: Any,
    schema_results: list[Any],
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "draft_only": True,
        "disclaimers": [DRAFT_DISCLAIMER, HS_001_DISCLAIMER, HS_009_DISCLAIMER],
        "blockers": list(readiness.blockers),
        "warnings": list(readiness.warnings),
        "structural_checks_passed": readiness.structural_checks_passed,
        "schema_purity": _schema_purity_payload(schema_results=schema_results),
    }


def _schema_purity_payload(*, schema_results: list[Any]) -> dict[str, Any]:
    return {
        "results": [
            {
                "artifact_key": result.artifact_key,
                "schema_available": result.schema_available,
                "payload_present": result.payload_present,
                "structurally_valid": result.structurally_valid,
                "authority_id": result.authority_id,
                "errors": list(result.errors),
            }
            for result in schema_results
        ]
    }


def _descriptor_for_path(
    *,
    path: str,
    contents: bytes,
    official_schema_id: str | None,
) -> dict[str, Any]:
    return {
        "path": path,
        "media_type": _media_type_for_path(path),
        "sha256": hashlib.sha256(contents).hexdigest(),
        "size_bytes": len(contents),
        "official_schema_id": official_schema_id,
    }


def _apply_official_schema_ids(
    *,
    files: list[dict[str, Any]],
    schema_results: list[Any],
) -> None:
    path_by_key = {
        "cpo": "machine/cpo.json",
        "sdr": "machine/sdr.json",
        "ocr": "machine/ocr.json",
    }
    valid_by_key = {
        result.artifact_key: result.authority_id
        for result in schema_results
        if result.structurally_valid and result.authority_id
    }
    for descriptor in files:
        for key, path in path_by_key.items():
            if descriptor["path"] == path:
                descriptor["official_schema_id"] = valid_by_key.get(key)
                break


def _media_type_for_path(path: str) -> str:
    if path.endswith(".md"):
        return "text/markdown"
    if path.endswith(".json"):
        return "application/json"
    return "text/plain"
