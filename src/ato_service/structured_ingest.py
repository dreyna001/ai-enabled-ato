"""Deterministic structured artifact ingest into package draft fields (Diff 8)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from ato_service.db.models import SourceArtifact
from ato_service.extraction.types import ExtractionOutcome

_SCANNER_EXPORT_KINDS = frozenset({"scanner_export"})
_OSCAL_KINDS = frozenset({"oscal", "reference_catalog"})
_CONTROL_ID_PATTERN = re.compile(r"^[A-Za-z]{2,3}-\d+(?:\(\d+\))?$")


def _write(**kwargs: Any) -> Any:
    from ato_service.draft_builder import _ProvenanceWrite

    return _ProvenanceWrite(**kwargs)


def _is_provenance_write(value: Any) -> bool:
    from ato_service.draft_builder import _ProvenanceWrite

    return isinstance(value, _ProvenanceWrite)


def ingest_structured_artifact(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
    pending_writes: dict[str, Any],
) -> bool:
    """Map scanner/OSCAL/reference artifacts into draft evidence and controls."""
    artifact_kind = getattr(artifact, "artifact_kind", None)
    if not isinstance(artifact_kind, str):
        return False
    if artifact_kind in _SCANNER_EXPORT_KINDS:
        return _ingest_scanner_export(
            artifact=artifact,
            outcome=outcome,
            pending_writes=pending_writes,
        )
    if artifact_kind in _OSCAL_KINDS:
        return _ingest_control_catalog(
            artifact=artifact,
            outcome=outcome,
            pending_writes=pending_writes,
        )
    return False


def _ingest_scanner_export(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
    pending_writes: dict[str, Any],
) -> bool:
    evidence_entries: dict[str, Any] = {}
    control_ids: set[str] = set()
    if outcome.detected_format == "sarif_json":
        document = _parse_json_document(outcome)
        runs = document.get("runs") if isinstance(document, dict) else None
        if isinstance(runs, list):
            for run_index, run in enumerate(runs):
                if not isinstance(run, dict):
                    continue
                results = run.get("results")
                if not isinstance(results, list):
                    continue
                for result_index, result in enumerate(results):
                    if not isinstance(result, dict):
                        continue
                    rule_id = _rule_identifier(result)
                    evidence_key = f"scanner-{artifact.artifact_id}-{run_index}-{result_index}"
                    evidence_entries[evidence_key] = {
                        "artifact_id": str(artifact.artifact_id),
                        "source_sha256": artifact.sha256,
                        "kind": "scanner_export",
                        "format": "sarif_json",
                        "rule_id": rule_id,
                        "message": _result_message(result),
                    }
                    control_id = _control_id_from_rule(rule_id)
                    if control_id:
                        control_ids.add(control_id)
    else:
        for index, segment in enumerate(outcome.segments):
            evidence_key = f"scanner-{artifact.artifact_id}-{index}"
            evidence_entries[evidence_key] = {
                "artifact_id": str(artifact.artifact_id),
                "source_sha256": artifact.sha256,
                "kind": "scanner_export",
                "format": outcome.detected_format,
                "locator": segment.locator,
                "text": segment.text[:2000],
            }
            control_id = _control_id_from_text(segment.text)
            if control_id:
                control_ids.add(control_id)

    if not evidence_entries:
        return False
    _merge_evidence(pending_writes, evidence_entries, artifact=artifact)
    _merge_controls(pending_writes, control_ids, artifact=artifact, evidence_keys=list(evidence_entries))
    return True


def _ingest_control_catalog(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
    pending_writes: dict[str, Any],
) -> bool:
    document = _parse_json_document(outcome) if outcome.detected_format.endswith("_json") else None
    control_ids: set[str] = set()
    if isinstance(document, dict):
        control_ids.update(_control_ids_from_oscal(document))
    for segment in outcome.segments:
        control_id = _control_id_from_text(segment.text)
        if control_id:
            control_ids.add(control_id)

    if not control_ids and document is None:
        return False

    catalog_pointer = "/control_set/source"
    pending_writes[catalog_pointer] = _write(
        draft_pointer=catalog_pointer,
        value={
            "artifact_id": str(artifact.artifact_id),
            "artifact_kind": artifact.artifact_kind,
            "sha256": artifact.sha256,
            "format": outcome.detected_format,
            "document": document if isinstance(document, dict) else None,
        },
        source_artifact_id=artifact.artifact_id,
        source_sha256=artifact.sha256,
        source_locator={"kind": "artifact", "artifact_id": str(artifact.artifact_id)},
        extraction_method="deterministic",
    )
    if control_ids:
        _merge_controls(
            pending_writes,
            control_ids,
            artifact=artifact,
            evidence_keys=[f"catalog-{artifact.artifact_id}"],
        )
    return True


def _merge_evidence(
    pending_writes: dict[str, Any],
    entries: dict[str, Any],
    *,
    artifact: SourceArtifact,
) -> None:
    existing = pending_writes.get("/evidence")
    merged: dict[str, Any]
    if _is_provenance_write(existing):
        current = existing.value if isinstance(existing.value, dict) else {}
        merged = {**current, **entries}
    elif isinstance(existing, dict):
        merged = {**existing, **entries}
    else:
        merged = dict(entries)
    pending_writes["/evidence"] = _write(
        draft_pointer="/evidence",
        value=merged,
        source_artifact_id=artifact.artifact_id,
        source_sha256=artifact.sha256,
        source_locator={"kind": "artifact", "artifact_id": str(artifact.artifact_id)},
        extraction_method="deterministic",
    )


def _merge_controls(
    pending_writes: dict[str, Any],
    control_ids: set[str],
    *,
    artifact: SourceArtifact,
    evidence_keys: list[str],
) -> None:
    existing = pending_writes.get("/security_controls")
    controls: dict[str, Any]
    if _is_provenance_write(existing):
        controls = dict(existing.value) if isinstance(existing.value, dict) else {}
    elif isinstance(existing, dict):
        controls = dict(existing)
    else:
        controls = {}
    for control_id in sorted(control_ids):
        entry = controls.get(control_id)
        if not isinstance(entry, dict):
            entry = {
                "implementation_status": "planned",
                "implementation_statement": "",
                "responsible_parties": [],
                "evidence_links": [],
            }
        links = list(entry.get("evidence_links", []))
        for evidence_key in evidence_keys:
            if evidence_key not in links:
                links.append(evidence_key)
        entry["evidence_links"] = links
        controls[control_id] = entry
    pending_writes["/security_controls"] = _write(
        draft_pointer="/security_controls",
        value=controls,
        source_artifact_id=artifact.artifact_id,
        source_sha256=artifact.sha256,
        source_locator={"kind": "artifact", "artifact_id": str(artifact.artifact_id)},
        extraction_method="deterministic",
    )


def _parse_json_document(outcome: ExtractionOutcome) -> Any:
    for segment in outcome.segments:
        try:
            parsed = json.loads(segment.text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return _reconstruct_json(outcome)


def _reconstruct_json(outcome: ExtractionOutcome) -> Any:
    merged: dict[str, Any] = {}
    for segment in outcome.segments:
        pointer = segment.locator.get("json_pointer")
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            continue
        parts = pointer.lstrip("/").split("/")
        current: Any = merged
        for part in parts[:-1]:
            key = part.replace("~1", "/").replace("~0", "~")
            next_value = current.get(key) if isinstance(current, dict) else None
            if not isinstance(next_value, dict):
                next_value = {}
                current[key] = next_value
            current = next_value
        leaf = parts[-1].replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current[leaf] = _parse_segment_value(segment.text)
    return merged if merged else None


def _parse_segment_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _rule_identifier(result: dict[str, Any]) -> str:
    rule = result.get("ruleId")
    if isinstance(rule, str) and rule:
        return rule
    rule_obj = result.get("rule")
    if isinstance(rule_obj, dict):
        rule_id = rule_obj.get("id")
        if isinstance(rule_id, str):
            return rule_id
    return "unknown-rule"


def _result_message(result: dict[str, Any]) -> str:
    message = result.get("message")
    if isinstance(message, dict):
        text = message.get("text")
        if isinstance(text, str):
            return text
    return ""


def _control_id_from_rule(rule_id: str) -> str | None:
    token = rule_id.split(":")[-1].strip().upper()
    return token if _CONTROL_ID_PATTERN.fullmatch(token) else None


def _control_id_from_text(text: str) -> str | None:
    match = re.search(r"\b([A-Z]{2,3}-\d+(?:\(\d+\))?)\b", text.upper())
    if not match:
        return None
    candidate = match.group(1)
    return candidate if _CONTROL_ID_PATTERN.fullmatch(candidate) else None


def _control_ids_from_oscal(document: dict[str, Any]) -> set[str]:
    control_ids: set[str] = set()
    catalog = document.get("catalog")
    if isinstance(catalog, dict):
        groups = catalog.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if isinstance(group, dict):
                    control_ids.update(_controls_from_oscal_group(group))
    controls = document.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                control_id = control.get("id")
                if isinstance(control_id, str):
                    control_ids.add(control_id.upper())
    return control_ids


def _controls_from_oscal_group(group: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    controls = group.get("controls")
    if isinstance(controls, list):
        for control in controls:
            if isinstance(control, dict):
                control_id = control.get("id")
                if isinstance(control_id, str):
                    ids.add(control_id.upper())
    nested = group.get("groups")
    if isinstance(nested, list):
        for child in nested:
            if isinstance(child, dict):
                ids.update(_controls_from_oscal_group(child))
    return ids


__all__ = ["ingest_structured_artifact"]
