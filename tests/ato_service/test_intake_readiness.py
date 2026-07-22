"""Unit tests for deterministic intake readiness report assembly."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from ato_service.intake_readiness import (
    DefaultIntakeMergeAdapter,
    IntakeFieldConflict,
    IntakeMergeSnapshot,
    IntakeReportContext,
    IntakeReportStateError,
    OmittedChunkRef,
    build_intake_report,
)
from ato_service.lifecycle_transitions import PackageRevisionStatus

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"

PACKAGE_REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
STEP_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
GENERATED_AT = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)


def _revision(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "package_revision_id": PACKAGE_REVISION_ID,
        "revision_version": 2,
        "status": PackageRevisionStatus.AWAITING_CONFIRMATION.value,
        "content_manifest_sha256": "a" * 64,
        "profile_id": "fisma_agency_security",
        "certification_class": None,
        "impact_level": "moderate",
        "data_origin": None,
        "sensitivity": None,
        "effective_data_labels": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _artifact(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "artifact_id": ARTIFACT_ID,
        "package_revision_id": PACKAGE_REVISION_ID,
        "display_filename": "evidence.json",
        "storage_key": "ab/" + ("a" * 62),
        "sha256": "b" * 64,
        "size_bytes": 128,
        "declared_media_type": "application/json",
        "detected_media_type": "application/json",
        "artifact_kind": "evidence_document",
        "malware_scan_status": "clean",
        "extraction_status": "succeeded",
        "source_date": None,
        "uploaded_at": GENERATED_AT,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _normalization_step(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "step_id": STEP_ID,
        "step_key": "imap_package_metadata",
        "status": "completed",
        "validation_outcome": "accepted",
        "fact_bundle_sha256": "c" * 64,
        "response_sha256": "d" * 64,
        "prompt_sha256": "e" * 64,
        "llm_call_count": 1,
        "error_code": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _context(**overrides: Any) -> IntakeReportContext:
    defaults: dict[str, Any] = {
        "package_revision": _revision(),
        "system": SimpleNamespace(owner_group="owners", viewer_groups=("viewers",)),
        "source_artifacts": (_artifact(),),
        "intake_work": (),
        "normalization_steps": (),
        "draft_document": {
            "package": {
                "profile_id": "fisma_agency_security",
                "impact_level": "moderate",
            },
            "system": {"impact_level": "moderate"},
        },
        "field_provenance": {
            "/system/impact_level": {
                "source_artifact_id": str(ARTIFACT_ID).lower(),
                "source_sha256": "b" * 64,
                "source_locator": {"segment_index": 0},
                "extraction_method": "llm_normalize",
                "model_step_id": str(STEP_ID).lower(),
            }
        },
        "pending_fact_proposals": False,
    }
    defaults.update(overrides)
    return IntakeReportContext(**defaults)


def _snapshot_from_extensions(
    extensions: dict[str, Any] | None,
) -> IntakeMergeSnapshot:
    draft_document: dict[str, Any] = {"package": {}, "system": {}}
    if extensions is not None:
        draft_document["extensions"] = extensions
    return DefaultIntakeMergeAdapter().load_merge_snapshot(
        package_revision_id=PACKAGE_REVISION_ID,
        draft_document=draft_document,
        field_provenance={},
    )


def test_build_intake_report_does_not_treat_draft_values_as_suggestions() -> None:
    payload = build_intake_report(
        _context(),
        merge_snapshot=IntakeMergeSnapshot(),
        generated_at=GENERATED_AT,
    )

    assert payload["object_type"] == "intake_report"
    assert payload["intake_stage"] == "awaiting_human_review"
    assert payload["human_attestation"] == {
        "data_origin": "missing",
        "sensitivity": "missing",
    }
    assert payload["suggested_metadata"] == {
        "profile_id": None,
        "certification_class": None,
        "impact_level": None,
    }
    assert payload["suggestion_sources"] == []
    assert payload["confirmation"]["allowed"] is False
    assert "human_data_origin_missing" in payload["confirmation"]["blockers"]
    assert "prompt_storage_key" not in str(payload)
    assert "prompt_storage_key" not in payload["map_steps"]


def test_default_adapter_reads_complete_reduce_extensions() -> None:
    snapshot = _snapshot_from_extensions(
        {
            "intake_conflicts": [
                {
                    "conflict_id": "conflict-1",
                    "target_pointer": "/system/mission_summary",
                    "resolution": "unresolved",
                    "candidates": [
                        {
                            "candidate_id": "candidate-b",
                            "value": "Beta",
                            "source_artifact_id": str(ARTIFACT_ID),
                            "source_sha256": "b" * 64,
                            "chunk_id": "chunk-b",
                            "step_key": "imap_system",
                            "raw_response": "must not escape",
                            "storage_key": "must/not/escape",
                        },
                        {
                            "candidate_id": "candidate-a",
                            "value": "Alpha",
                            "source_artifact_id": str(ARTIFACT_ID),
                            "chunk_id": "chunk-a",
                            "step_key": "imap_system",
                        },
                    ],
                }
            ],
            "intake_gaps": [
                {
                    "target_pointer": "/system/authorization_boundary",
                    "reason": "customer narrative included secret-looking text",
                    "step_key": "imap_system",
                }
            ],
            "intake_omitted_chunks": [],
            "intake_context_complete": True,
        }
    )

    assert snapshot.context_complete is True
    assert snapshot.conflicts[0].field == "/system/mission_summary"
    assert [candidate["value"] for candidate in snapshot.conflicts[0].values] == [
        "Alpha",
        "Beta",
    ]
    assert "raw_response" not in snapshot.conflicts[0].values[1]
    assert "storage_key" not in snapshot.conflicts[0].values[1]
    assert len(snapshot.extra_gaps) == 1
    assert "secret-looking text" not in snapshot.extra_gaps[0].message
    assert snapshot.extra_gaps[0].message == (
        "Intake gap at /system/authorization_boundary "
        "from MAP step imap_system."
    )


def test_default_adapter_missing_extensions_fails_closed() -> None:
    adapter = DefaultIntakeMergeAdapter()
    without_draft = adapter.load_merge_snapshot(
        package_revision_id=PACKAGE_REVISION_ID,
        draft_document=None,
        field_provenance=None,
    )
    without_extensions = _snapshot_from_extensions(None)

    for snapshot in (without_draft, without_extensions):
        assert snapshot.context_complete is None
        assert snapshot.conflicts == ()
        assert snapshot.extra_gaps == ()
        assert snapshot.omitted_chunk_refs == ()


@pytest.mark.parametrize(
    "extensions",
    [
        {"intake_context_complete": "true"},
        {"intake_conflicts": {}},
        {
            "intake_conflicts": [
                {
                    "conflict_id": "conflict-1",
                    "target_pointer": "not-a-pointer",
                    "resolution": "unresolved",
                    "candidates": [{"value": "a"}, {"value": "b"}],
                }
            ]
        },
        {
            "intake_gaps": [
                {
                    "target_pointer": "/system/mission_summary",
                    "reason": "",
                }
            ]
        },
        {
            "intake_omitted_chunks": [
                {
                    "artifact_id": "not-a-uuid",
                    "chunk_id": "chunk-1",
                    "step_key": "imap_system",
                }
            ]
        },
    ],
)
def test_default_adapter_rejects_malformed_present_extensions(
    extensions: dict[str, Any],
) -> None:
    with pytest.raises(IntakeReportStateError) as exc_info:
        _snapshot_from_extensions(extensions)

    assert exc_info.value.error_code == "state_artifact_inconsistent"


def test_default_adapter_dedupes_and_orders_extension_records() -> None:
    conflict_a = {
        "conflict_id": "conflict-a",
        "target_pointer": "/system/a",
        "resolution": "unresolved",
        "candidates": [{"value": "2"}, {"value": "1"}],
    }
    conflict_b = {
        "conflict_id": "conflict-b",
        "target_pointer": "/system/b",
        "resolution": "unresolved",
        "candidates": [{"value": "b"}, {"value": "a"}],
    }
    gap_a = {"target_pointer": "/system/a", "reason": "missing"}
    gap_b = {
        "target_pointer": "/system/b",
        "reason": "conflict",
        "step_key": "imap_system",
    }
    omitted_a = {
        "artifact_id": str(ARTIFACT_ID),
        "chunk_id": "chunk-a",
        "step_key": "imap_system",
    }
    omitted_b = {
        "artifact_id": str(
            uuid.UUID("55555555-5555-4555-8555-555555555555")
        ),
        "chunk_id": "chunk-b",
        "step_key": "imap_system",
    }
    snapshot = _snapshot_from_extensions(
        {
            "intake_conflicts": [conflict_b, conflict_a, conflict_b],
            "intake_gaps": [gap_b, gap_a, gap_b],
            "intake_omitted_chunks": [omitted_b, omitted_a, omitted_b],
            "intake_context_complete": False,
        }
    )

    assert [conflict.field for conflict in snapshot.conflicts] == [
        "/system/a",
        "/system/b",
    ]
    assert [candidate["value"] for candidate in snapshot.conflicts[0].values] == [
        "1",
        "2",
    ]
    assert len(snapshot.extra_gaps) == 2
    assert list(snapshot.omitted_chunk_refs) == [
        OmittedChunkRef(artifact_id=ARTIFACT_ID, segment_id="chunk-a"),
        OmittedChunkRef(
            artifact_id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
            segment_id="chunk-b",
        ),
    ]


def test_report_always_returns_empty_suggested_metadata() -> None:
    snapshot = _snapshot_from_extensions(
        {
            "intake_metadata_suggestions": {
                "profile_id": "fedramp_20x_program",
                "certification_class": "B",
                "impact_level": None,
            }
        }
    )
    payload = build_intake_report(
        _context(),
        merge_snapshot=snapshot,
        generated_at=GENERATED_AT,
    )

    assert payload["suggested_metadata"] == {
        "profile_id": None,
        "certification_class": None,
        "impact_level": None,
    }
    assert payload["suggestion_sources"] == []


@pytest.mark.parametrize(
    "extensions",
    [
        {
            "intake_conflicts": [
                {
                    "conflict_id": "conflict-1",
                    "target_pointer": "/package/sensitivity",
                    "resolution": "unresolved",
                    "candidates": [{"value": "public"}, {"value": "cui"}],
                }
            ]
        },
        {
            "intake_conflicts": [
                {
                    "conflict_id": "conflict-1",
                    "target_pointer": "/system/mission_summary",
                    "resolution": "unresolved",
                    "candidates": [
                        {"value": "a", "data_origin": "synthetic"},
                        {"value": "b"},
                    ],
                }
            ]
        },
    ],
)
def test_default_adapter_rejects_human_only_intake_fields(
    extensions: dict[str, Any],
) -> None:
    with pytest.raises(IntakeReportStateError) as exc_info:
        _snapshot_from_extensions(extensions)

    assert exc_info.value.error_code == "state_artifact_inconsistent"


def test_awaiting_confirmation_draft_without_map_evidence_is_incomplete() -> None:
    payload = build_intake_report(
        _context(),
        merge_snapshot=IntakeMergeSnapshot(),
        generated_at=GENERATED_AT,
    )

    assert payload["context_complete"] is False
    assert any(gap["code"] == "context_incomplete" for gap in payload["gaps"])


def test_legacy_normalization_does_not_masquerade_as_intake_map() -> None:
    payload = build_intake_report(
        _context(
            normalization_steps=(
                _normalization_step(
                    step_key="normalize_proposal",
                    status="running",
                    validation_outcome=None,
                ),
            ),
        ),
        merge_snapshot=IntakeMergeSnapshot(),
        generated_at=GENERATED_AT,
    )

    assert payload["intake_stage"] == "awaiting_human_review"
    assert payload["map_steps"] == []
    assert payload["context_complete"] is False
    assert not any(
        gap["code"] == "normalization_in_progress" for gap in payload["gaps"]
    )


def test_explicit_complete_reduce_snapshot_reports_complete_context() -> None:
    payload = build_intake_report(
        _context(),
        merge_snapshot=IntakeMergeSnapshot(context_complete=True),
        generated_at=GENERATED_AT,
    )

    assert payload["context_complete"] is True
    assert not any(gap["code"] == "context_incomplete" for gap in payload["gaps"])


def test_incomplete_context_is_gap_not_confirmation_blocker() -> None:
    payload = build_intake_report(
        _context(
            package_revision=_revision(
                data_origin="synthetic",
                sensitivity="internal_unclassified",
                effective_data_labels=["internal_unclassified", "synthetic"],
            ),
        ),
        merge_snapshot=IntakeMergeSnapshot(),
        generated_at=GENERATED_AT,
    )

    assert payload["context_complete"] is False
    assert any(gap["code"] == "context_incomplete" for gap in payload["gaps"])
    assert "context_incomplete" not in payload["confirmation"]["blockers"]
    assert payload["confirmation"]["allowed"] is True


@pytest.mark.parametrize(
    ("step_overrides",),
    [
        ({"status": "policy_blocked", "validation_outcome": "rejected_policy"},),
        ({"status": "completed", "validation_outcome": "model_not_configured"},),
        ({"status": "completed", "validation_outcome": "skipped_no_targets"},),
        ({"status": "completed", "validation_outcome": "rejected_context_limit"},),
    ],
)
def test_incomplete_map_outcomes_override_complete_reduce_claim(
    step_overrides: dict[str, Any],
) -> None:
    snapshot = _snapshot_from_extensions({"intake_context_complete": True})
    payload = build_intake_report(
        _context(normalization_steps=(_normalization_step(**step_overrides),)),
        merge_snapshot=snapshot,
        generated_at=GENERATED_AT,
    )

    assert payload["context_complete"] is False


def test_build_intake_report_no_artifacts_stage() -> None:
    payload = build_intake_report(
        _context(
            package_revision=_revision(status=PackageRevisionStatus.UPLOADING.value),
            source_artifacts=(),
            draft_document=None,
            field_provenance=None,
        ),
        merge_snapshot=IntakeMergeSnapshot(),
        generated_at=GENERATED_AT,
    )

    assert payload["intake_stage"] == "no_artifacts"
    assert payload["files"] == []
    assert any(gap["code"] == "no_source_artifacts" for gap in payload["gaps"])


def test_build_intake_report_reflects_merge_adapter_conflicts() -> None:
    merge_snapshot = IntakeMergeSnapshot(
        conflicts=(
            IntakeFieldConflict(
                field="/system/mission_summary",
                values=(
                    {"value": "Alpha", "source_artifact_id": str(ARTIFACT_ID).lower()},
                    {"value": "Beta", "source_artifact_id": str(ARTIFACT_ID).lower()},
                ),
            ),
        ),
        omitted_chunk_refs=(
            OmittedChunkRef(artifact_id=ARTIFACT_ID, segment_id="artifact:0:tail"),
        ),
        context_complete=False,
    )
    payload = build_intake_report(
        _context(),
        merge_snapshot=merge_snapshot,
        generated_at=GENERATED_AT,
    )

    assert payload["context_complete"] is False
    assert payload["conflicts"][0]["field"] == "/system/mission_summary"
    assert payload["omitted_chunks"] == [
        {
            "artifact_id": str(ARTIFACT_ID).lower(),
            "segment_id": "artifact:0:tail",
        }
    ]
    assert payload["intake_stage"] == "intake_reduce"
    assert any(gap["code"] == "merge_conflicts_present" for gap in payload["gaps"])


def test_build_intake_report_fails_closed_on_draft_before_awaiting_confirmation() -> None:
    with pytest.raises(IntakeReportStateError):
        build_intake_report(
            _context(
                package_revision=_revision(status=PackageRevisionStatus.SCANNING.value),
            ),
            merge_snapshot=IntakeMergeSnapshot(),
            generated_at=GENERATED_AT,
        )


def test_build_intake_report_confirmed_stage() -> None:
    payload = build_intake_report(
        _context(
            package_revision=_revision(
                status=PackageRevisionStatus.READY.value,
                data_origin="synthetic",
                sensitivity="internal_unclassified",
                effective_data_labels=["internal_unclassified", "synthetic"],
            ),
        ),
        merge_snapshot=IntakeMergeSnapshot(),
        generated_at=GENERATED_AT,
    )

    assert payload["intake_stage"] == "confirmed"
    assert payload["confirmation"]["allowed"] is False
    assert "revision_not_awaiting_confirmation" in payload["confirmation"]["blockers"]


def test_intake_report_payload_validates_against_domain_schema() -> None:
    import json

    domain_schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        {**domain_schema["$defs"]["IntakeReport"], "$defs": domain_schema["$defs"]}
    )
    payload = build_intake_report(
        _context(
            package_revision=_revision(
                data_origin="synthetic",
                sensitivity="internal_unclassified",
                effective_data_labels=["internal_unclassified", "synthetic"],
            ),
        ),
        merge_snapshot=IntakeMergeSnapshot(),
        generated_at=GENERATED_AT,
    )
    validator.validate(payload)
