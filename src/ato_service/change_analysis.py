"""Change analysis helpers for targeted re-analysis (Component H)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ato_service.revision_delta import (
    compute_revision_delta,
    targeted_assessment_item_ids_from_delta,
)


def build_change_analysis(
    *,
    parent_revision_id: uuid.UUID,
    child_revision_id: uuid.UUID,
    parent_artifacts: list[Any],
    child_artifacts: list[Any],
    parent_document: dict[str, Any] | None,
    child_document: dict[str, Any] | None,
    parent_content_sha256: str | None,
    child_content_sha256: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Produce a delta report and targeted assessment item ids for child revisions."""
    delta = compute_revision_delta(
        parent_revision_id=parent_revision_id,
        child_revision_id=child_revision_id,
        parent_artifacts=parent_artifacts,
        child_artifacts=child_artifacts,
        parent_document=parent_document,
        child_document=child_document,
        parent_content_sha256=parent_content_sha256,
        child_content_sha256=child_content_sha256,
        now=now,
    )
    targeted_ids = targeted_assessment_item_ids_from_delta(
        delta=delta,
        child_document=child_document,
    )
    return {
        "delta": delta.to_dict(),
        "targeted_assessment_item_ids": list(targeted_ids),
        "requires_targeted_reanalysis": bool(
            delta.changed_artifact_ids
            or delta.added_artifact_ids
            or delta.changed_control_ids
            or delta.changed_evidence_keys
        ),
    }
