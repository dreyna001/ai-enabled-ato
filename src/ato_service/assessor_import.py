"""Import-only assessor artifact ingest into assessor_inputs (Component A Diff 9)."""

from __future__ import annotations

import json
import re
from typing import Any

from ato_service.db.models import SourceArtifact
from ato_service.extraction.types import ExtractionOutcome

_ASSESSOR_ARTIFACT_KINDS = frozenset({"attestation"})
_ASSESSOR_REPORT_FILENAME_PATTERN = re.compile(
    r"(?:sar|assessment|attestation|assessor)",
    re.IGNORECASE,
)
_CONTROL_ID_PATTERN = re.compile(r"^[A-Za-z]{2,3}-\d+(?:\(\d+\))?$")
_STRUCTURED_JSON_FORMATS = frozenset({"json", "oscal_json", "sarif_json", "stig_json"})


def _write(**kwargs: Any) -> Any:
    from ato_service.draft_builder import _ProvenanceWrite

    return _ProvenanceWrite(**kwargs)


def ingest_assessor_artifact(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
    pending_writes: dict[str, Any],
) -> bool:
    """Map attestation or assessor-report uploads into import-only assessor_inputs."""
    artifact_kind = getattr(artifact, "artifact_kind", None)
    if not isinstance(artifact_kind, str):
        return False
    if artifact_kind not in _ASSESSOR_ARTIFACT_KINDS and not _looks_like_assessor_report(
        artifact_kind=artifact_kind,
        display_filename=getattr(artifact, "display_filename", ""),
    ):
        return False
    if outcome.status != "succeeded":
        return False
    if outcome.detected_format not in _STRUCTURED_JSON_FORMATS:
        return False

    document = _parse_json_document(outcome)
    if not isinstance(document, dict):
        return False

    import_key = f"artifact-{artifact.artifact_id}"
    entry = _build_assessor_input_entry(
        artifact=artifact,
        document=document,
        outcome=outcome,
    )
    pending_writes[f"/assessor_inputs/{import_key}"] = _write(
        draft_pointer=f"/assessor_inputs/{import_key}",
        value=entry,
        source_artifact_id=artifact.artifact_id,
        source_sha256=artifact.sha256,
        source_locator={"kind": "artifact_import", "artifact_id": str(artifact.artifact_id).lower()},
        extraction_method="deterministic",
    )
    return True


def _looks_like_assessor_report(*, artifact_kind: str, display_filename: str) -> bool:
    if artifact_kind != "evidence_document":
        return False
    return bool(_ASSESSOR_REPORT_FILENAME_PATTERN.search(display_filename))


def _parse_json_document(outcome: ExtractionOutcome) -> Any:
    if not outcome.segments:
        return None
    text = outcome.segments[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _build_assessor_input_entry(
    *,
    artifact: SourceArtifact,
    document: dict[str, Any],
    outcome: ExtractionOutcome,
) -> dict[str, Any]:
    findings = _extract_findings(document)
    controls = sorted(_extract_control_ids(document, findings))
    return {
        "owner": "assessor",
        "import_only": True,
        "artifact_id": str(artifact.artifact_id).lower(),
        "display_filename": artifact.display_filename,
        "source_sha256": artifact.sha256,
        "detected_format": outcome.detected_format,
        "imported_at": None,
        "summary": _extract_summary(document),
        "findings": findings,
        "linked_controls": controls,
        "raw_excerpt_keys": sorted(document.keys())[:20],
    }


def _extract_summary(document: dict[str, Any]) -> str:
    for key in ("summary", "executive_summary", "assessment_summary", "title"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:2000]
    return "Imported assessor artifact"


def _extract_findings(document: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for key in ("findings", "assessment_findings", "results"):
        raw = document.get(key)
        if isinstance(raw, list):
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    continue
                findings.append(
                    {
                        "finding_id": str(item.get("id") or item.get("finding_id") or f"finding-{index}"),
                        "control_id": _normalize_control_id(item.get("control_id") or item.get("control")),
                        "status": item.get("status") or item.get("result") or "imported",
                        "statement": _finding_statement(item),
                    }
                )
    if not findings and isinstance(document.get("controls"), list):
        for index, item in enumerate(document["controls"]):
            if not isinstance(item, dict):
                continue
            control_id = _normalize_control_id(item.get("id") or item.get("control_id"))
            if control_id is None:
                continue
            findings.append(
                {
                    "finding_id": f"control-{index}",
                    "control_id": control_id,
                    "status": item.get("status") or "imported",
                    "statement": _finding_statement(item),
                }
            )
    return findings[:100]


def _finding_statement(item: dict[str, Any]) -> str:
    for key in ("statement", "description", "summary", "message"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:2000]
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()[:2000]
    return "Imported assessor finding"


def _extract_control_ids(
    document: dict[str, Any],
    findings: list[dict[str, Any]],
) -> set[str]:
    control_ids: set[str] = set()
    for finding in findings:
        control_id = finding.get("control_id")
        if isinstance(control_id, str):
            control_ids.add(control_id)
    for key in ("controls", "control_ids", "assessed_controls"):
        raw = document.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    normalized = _normalize_control_id(item)
                    if normalized:
                        control_ids.add(normalized)
                elif isinstance(item, dict):
                    normalized = _normalize_control_id(item.get("id") or item.get("control_id"))
                    if normalized:
                        control_ids.add(normalized)
    return control_ids


def _normalize_control_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    return token if _CONTROL_ID_PATTERN.fullmatch(token) else None


__all__ = ["ingest_assessor_artifact"]
