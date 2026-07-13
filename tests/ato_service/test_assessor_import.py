"""Tests for import-only assessor artifact ingest (Component A Diff 9)."""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace

from ato_service.assessor_import import ingest_assessor_artifact
from ato_service.extraction.types import ExtractionOutcome, ExtractedSegment


def _outcome(*, detected_format: str, segments: tuple[ExtractedSegment, ...]) -> ExtractionOutcome:
    return ExtractionOutcome(
        status="succeeded",
        detected_format=detected_format,
        detected_media_type="application/json",
        page_count=None,
        total_text_characters=sum(len(segment.text) for segment in segments),
        vision_status="not_requested",
        segments=segments,
    )


def _artifact(*, artifact_kind: str, filename: str = "upload.json") -> SimpleNamespace:
    artifact_uuid = uuid.uuid4()
    content = b"{}"
    digest = hashlib.sha256(content).hexdigest()
    return SimpleNamespace(
        artifact_id=artifact_uuid,
        artifact_kind=artifact_kind,
        sha256=digest,
        size_bytes=len(content),
        display_filename=filename,
    )


def test_attestation_populates_assessor_inputs_import_only() -> None:
    artifact = _artifact(artifact_kind="attestation", filename="sar-summary.json")
    sar = {
        "summary": "Independent assessment complete",
        "findings": [
            {
                "id": "f-1",
                "control_id": "AC-2",
                "status": "satisfied",
                "statement": "Account management reviewed",
            }
        ],
    }
    segment = ExtractedSegment(
        segment_index=0,
        text=json.dumps(sar),
        locator={"kind": "json_pointer", "json_pointer": ""},
        extraction_method="deterministic",
    )
    outcome = _outcome(detected_format="json", segments=(segment,))
    pending: dict[str, object] = {}
    assert ingest_assessor_artifact(
        artifact=artifact,
        outcome=outcome,
        pending_writes=pending,
    )
    assessor_write = next(value for key, value in pending.items() if key.startswith("/assessor_inputs/"))
    entry = assessor_write.value
    assert entry["owner"] == "assessor"
    assert entry["import_only"] is True
    assert "AC-2" in entry["linked_controls"]
    assert entry["findings"][0]["control_id"] == "AC-2"


def test_evidence_document_sar_filename_is_treated_as_assessor_report() -> None:
    artifact = _artifact(artifact_kind="evidence_document", filename="agency-sar-export.json")
    sar = {"controls": [{"id": "AU-2", "status": "not_satisfied", "description": "Audit gaps"}]}
    segment = ExtractedSegment(
        segment_index=0,
        text=json.dumps(sar),
        locator={"kind": "json_pointer", "json_pointer": ""},
        extraction_method="deterministic",
    )
    outcome = _outcome(detected_format="json", segments=(segment,))
    pending: dict[str, object] = {}
    assert ingest_assessor_artifact(
        artifact=artifact,
        outcome=outcome,
        pending_writes=pending,
    )
    assessor_write = next(value for key, value in pending.items() if key.startswith("/assessor_inputs/"))
    assert assessor_write.value["linked_controls"] == ["AU-2"]


def test_non_assessor_artifact_is_ignored() -> None:
    artifact = _artifact(artifact_kind="evidence_document", filename="policy.pdf.json")
    segment = ExtractedSegment(
        segment_index=0,
        text='{"summary":"policy"}',
        locator={"kind": "json_pointer", "json_pointer": ""},
        extraction_method="deterministic",
    )
    outcome = _outcome(detected_format="json", segments=(segment,))
    pending: dict[str, object] = {}
    assert not ingest_assessor_artifact(
        artifact=artifact,
        outcome=outcome,
        pending_writes=pending,
    )
    assert pending == {}
