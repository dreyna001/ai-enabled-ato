"""Revision lineage delta for ConMon-lite child revisions (Component A Diff 12)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class RevisionDeltaReport:
    """Deterministic delta between a parent and child package revision."""

    parent_revision_id: uuid.UUID
    child_revision_id: uuid.UUID
    changed_artifact_ids: tuple[str, ...]
    added_artifact_ids: tuple[str, ...]
    removed_artifact_ids: tuple[str, ...]
    changed_control_ids: tuple[str, ...]
    changed_evidence_keys: tuple[str, ...]
    content_digest_changed: bool
    generated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "parent_revision_id": str(self.parent_revision_id).lower(),
            "child_revision_id": str(self.child_revision_id).lower(),
            "changed_artifact_ids": list(self.changed_artifact_ids),
            "added_artifact_ids": list(self.added_artifact_ids),
            "removed_artifact_ids": list(self.removed_artifact_ids),
            "changed_control_ids": list(self.changed_control_ids),
            "changed_evidence_keys": list(self.changed_evidence_keys),
            "content_digest_changed": self.content_digest_changed,
            "generated_at": _format_utc(self.generated_at),
        }


def compute_revision_delta(
    *,
    parent_revision_id: uuid.UUID,
    child_revision_id: uuid.UUID,
    parent_artifacts: Sequence[Any],
    child_artifacts: Sequence[Any],
    parent_document: dict[str, Any] | None,
    child_document: dict[str, Any] | None,
    parent_content_sha256: str | None,
    child_content_sha256: str | None,
    now: datetime,
) -> RevisionDeltaReport:
    """Compare parent and child revisions for changed evidence and controls."""
    parent_by_id = {str(item.artifact_id).lower(): item for item in parent_artifacts}
    child_by_id = {str(item.artifact_id).lower(): item for item in child_artifacts}
    parent_ids = set(parent_by_id)
    child_ids = set(child_by_id)

    added = sorted(child_ids - parent_ids)
    removed = sorted(parent_ids - child_ids)
    changed: list[str] = []
    for artifact_id in sorted(parent_ids & child_ids):
        parent_item = parent_by_id[artifact_id]
        child_item = child_by_id[artifact_id]
        if parent_item.sha256 != child_item.sha256:
            changed.append(artifact_id)

    parent_controls = _control_map(parent_document)
    child_controls = _control_map(child_document)
    changed_controls = sorted(
        control_id
        for control_id in sorted(set(parent_controls) | set(child_controls))
        if parent_controls.get(control_id) != child_controls.get(control_id)
    )

    parent_evidence = _evidence_map(parent_document)
    child_evidence = _evidence_map(child_document)
    changed_evidence = sorted(
        key
        for key in sorted(set(parent_evidence) | set(child_evidence))
        if parent_evidence.get(key) != child_evidence.get(key)
    )

    return RevisionDeltaReport(
        parent_revision_id=parent_revision_id,
        child_revision_id=child_revision_id,
        changed_artifact_ids=tuple(changed),
        added_artifact_ids=tuple(added),
        removed_artifact_ids=tuple(removed),
        changed_control_ids=tuple(changed_controls),
        changed_evidence_keys=tuple(changed_evidence),
        content_digest_changed=parent_content_sha256 != child_content_sha256,
        generated_at=now,
    )


def targeted_assessment_item_ids_from_delta(
    *,
    delta: RevisionDeltaReport,
    child_document: dict[str, Any] | None,
) -> tuple[str, ...]:
    """Derive targeted re-analysis assessment items from a revision delta."""
    if child_document is None:
        return ()
    controls = child_document.get("security_controls")
    if not isinstance(controls, dict):
        return ()
    item_ids: list[str] = []
    for control_id in delta.changed_control_ids:
        if control_id in controls:
            item_ids.append(control_id)
    return tuple(sorted(set(item_ids)))


def document_content_sha256(document: dict[str, Any]) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _control_map(document: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    controls = document.get("security_controls")
    if not isinstance(controls, dict):
        return {}
    return {
        key: controls[key]
        for key in sorted(controls)
        if isinstance(controls[key], dict)
    }


def _evidence_map(document: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    evidence = document.get("evidence")
    if not isinstance(evidence, dict):
        return {}
    return {key: evidence[key] for key in sorted(evidence)}


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
