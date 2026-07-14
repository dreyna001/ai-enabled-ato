"""Tests for immutable validation drill records."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato_operator.drill_records import (
    DrillPathError,
    DrillRecordError,
    HardStopClaim,
    bind_record_digest,
    build_drill_record,
    compute_record_digest,
    contains_sensitive_material,
    drill_record_path,
    redact_drill_value,
    validate_drill_record_schema,
    validate_drill_record_semantics,
    write_drill_record,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "docs" / "contracts" / "fixtures"


def _sample_record(**overrides: object) -> dict:
    started = datetime(2026, 7, 14, 16, 0, 0, tzinfo=UTC)
    completed = datetime(2026, 7, 14, 16, 0, 1, tzinfo=UTC)
    record = build_drill_record(
        record_id=str(uuid.uuid4()),
        drill_id="model-routing-policy-block",
        drill_version="1.0.0",
        environment_type="dev_local",
        execution_mode="dry_run",
        started_at=started,
        completed_at=completed,
        application_digest="a" * 64,
        config_digest="b" * 64,
        fixture_digest="c" * 64,
        operator_identifier="operator@example.local",
        approver_identifier=None,
        outcome="pass",
        hard_stop_claims=(
            HardStopClaim("HS-003", "not_claimed"),
            HardStopClaim("HS-005", "not_claimed"),
            HardStopClaim("HS-008", "not_claimed"),
        ),
        results={
            "summary": "Deterministic routing blocks verified",
            "checks": [{"name": "classified_data_unsupported", "status": "pass"}],
            "preflight_status": "ready",
        },
    )
    record.update(overrides)
    return bind_record_digest(record)


def test_schema_accepts_valid_fixture() -> None:
    fixture = json.loads(
        (FIXTURES / "validation-drill-record.valid.minimal-dry-run.json").read_text(encoding="utf-8")
    )
    validate_drill_record_schema(fixture, project_root=ROOT)


def test_schema_rejects_invalid_fixtures() -> None:
    for name in (
        "validation-drill-record.invalid.missing-hard-stop-claims.json",
        "validation-drill-record.invalid.bad-outcome.json",
    ):
        fixture = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        with pytest.raises(DrillRecordError):
            validate_drill_record_schema(fixture, project_root=ROOT)


def test_redact_drill_value_strips_bearer_tokens() -> None:
    redacted = redact_drill_value(
        {"detail": "Authorization: Bearer secret-token-value", "checks": []}
    )
    assert "secret-token-value" not in json.dumps(redacted)
    assert contains_sensitive_material({"detail": "Authorization: Bearer secret-token-value"}) is True


def test_record_digest_is_deterministic() -> None:
    record = _sample_record()
    assert compute_record_digest(record) == record["record_digest"]


def test_write_refuses_duplicate_record(tmp_path: Path) -> None:
    record = _sample_record()
    write_drill_record(tmp_path, record, project_root=ROOT)
    with pytest.raises(DrillRecordError, match="already exists"):
        write_drill_record(tmp_path, record, project_root=ROOT)


def test_path_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(DrillPathError):
        drill_record_path(tmp_path, drill_id="../escape", record_id="record")


def test_semantic_validation_rejects_digest_mismatch() -> None:
    record = _sample_record()
    record["record_digest"] = "0" * 64
    with pytest.raises(DrillRecordError, match="record_digest"):
        validate_drill_record_semantics(record, project_root=ROOT)


def test_semantic_validation_rejects_unredacted_sensitive_results() -> None:
    record = _sample_record()
    record["results"]["detail"] = "Authorization: Bearer live-token"
    record = bind_record_digest(record)
    with pytest.raises(DrillRecordError, match="sensitive material"):
        validate_drill_record_semantics(record, project_root=ROOT)
