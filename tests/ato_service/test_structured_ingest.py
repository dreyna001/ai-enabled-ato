"""Tests for structured scanner/OSCAL ingest (Component A Diff 8)."""

from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace

from ato_service.extraction.types import ExtractionOutcome, ExtractedSegment
from ato_service.structured_ingest import ingest_structured_artifact


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


def _artifact(*, artifact_kind: str, artifact_id: uuid.UUID | None = None) -> SimpleNamespace:
    artifact_uuid = artifact_id or uuid.uuid4()
    content = b"{}"
    digest = hashlib.sha256(content).hexdigest()
    return SimpleNamespace(
        artifact_id=artifact_uuid,
        artifact_kind=artifact_kind,
        sha256=digest,
        size_bytes=len(content),
        display_filename="upload.json",
    )


def test_scanner_export_sarif_links_evidence_and_controls() -> None:
    artifact = _artifact(artifact_kind="scanner_export")
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "AC-2",
                        "message": {"text": "Account management finding"},
                    }
                ]
            }
        ],
    }
    segment = ExtractedSegment(
        segment_index=0,
        text=json.dumps(sarif),
        locator={"kind": "json_pointer", "json_pointer": ""},
        extraction_method="deterministic",
    )
    outcome = _outcome(detected_format="sarif_json", segments=(segment,))
    pending: dict[str, object] = {}
    assert ingest_structured_artifact(
        artifact=artifact,
        outcome=outcome,
        pending_writes=pending,
    )
    evidence_write = pending["/evidence"]
    controls_write = pending["/security_controls"]
    assert "AC-2" in controls_write.value
    assert controls_write.value["AC-2"]["evidence_links"]
    assert evidence_write.value


def test_oscal_catalog_populates_control_set_source() -> None:
    artifact = _artifact(artifact_kind="oscal")
    catalog = {
        "catalog": {
            "groups": [
                {
                    "controls": [{"id": "au-2"}],
                }
            ]
        }
    }
    segment = ExtractedSegment(
        segment_index=0,
        text=json.dumps(catalog),
        locator={"kind": "json_pointer", "json_pointer": ""},
        extraction_method="deterministic",
    )
    outcome = _outcome(detected_format="oscal_json", segments=(segment,))
    pending: dict[str, object] = {}
    assert ingest_structured_artifact(
        artifact=artifact,
        outcome=outcome,
        pending_writes=pending,
    )
    assert "/control_set/source" in pending
    assert pending["/security_controls"].value["AU-2"]["evidence_links"]
