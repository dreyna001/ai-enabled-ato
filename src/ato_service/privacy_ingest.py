"""Privacy artifact ingest and scope notice updates (Component A Diff 11)."""

from __future__ import annotations

from typing import Any

from ato_service.db.models import SourceArtifact
from ato_service.extraction.types import ExtractionOutcome

_PRIVACY_ARTIFACT_KINDS = frozenset({"privacy_artifact"})


def _write(**kwargs: Any) -> Any:
    from ato_service.draft_builder import _ProvenanceWrite

    return _ProvenanceWrite(**kwargs)


def ingest_privacy_artifact(
    *,
    artifact: SourceArtifact,
    outcome: ExtractionOutcome,
    pending_writes: dict[str, Any],
) -> bool:
    """Mark privacy artifacts present without performing privacy assessment."""
    artifact_kind = getattr(artifact, "artifact_kind", None)
    if artifact_kind not in _PRIVACY_ARTIFACT_KINDS:
        return False
    pending_writes["/privacy"] = _write(
        draft_pointer="/privacy",
        value={
            "artifacts_present": True,
            "scope_notice": "Privacy review is external to this product.",
            "artifact_id": str(artifact.artifact_id).lower(),
            "display_filename": artifact.display_filename,
            "source_sha256": artifact.sha256,
        },
        source_artifact_id=artifact.artifact_id,
        source_sha256=artifact.sha256,
        source_locator={"kind": "privacy_artifact", "artifact_id": str(artifact.artifact_id).lower()},
        extraction_method="deterministic",
    )
    return True


__all__ = ["ingest_privacy_artifact"]
