"""End-to-end tests for process_one runner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ato_analysis.config import load_settings
from ato_analysis.models.report_schema import ReportModel
from ato_analysis.runner import process_one_package


def _assert_matrix_status_bands(
    report: ReportModel,
    expected_golden: dict,
) -> None:
    expectations = expected_golden["integration_expectations"]
    matrix_by_control = {row.control_id: row for row in report.evidence_matrix}
    for control_id, expectation in expectations.items():
        row = matrix_by_control[control_id]
        allowed = expectation["sufficiency_status_in"]
        assert row.sufficiency_status in allowed, (
            f"{control_id}: got {row.sufficiency_status!r}, expected one of {allowed}"
        )


@pytest.mark.integration
def test_live_openai_golden_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_golden: dict,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    for name in ("incoming", "processed", "quarantine", "reports", "audit"):
        (tmp_path / name).mkdir()

    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("INCOMING_DIR", str(tmp_path / "incoming"))
    monkeypatch.setenv("PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))

    package_id = "golden_fisma_minimal"
    outcome = process_one_package(package_id, fixture=package_id, dry_run=False)

    assert outcome.status == "completed"
    assert outcome.llm_call_count >= 1
    assert outcome.report_json_path is not None
    assert outcome.report_json_path.is_file()
    assert outcome.audit_path is not None

    report = ReportModel.model_validate(
        json.loads(outcome.report_json_path.read_text(encoding="utf-8"))
    )
    assert len(report.evidence_matrix) == 5
    _assert_matrix_status_bands(report, expected_golden)


def test_dry_run_golden_e2e_no_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("incoming", "processed", "quarantine", "reports", "audit"):
        (tmp_path / name).mkdir()

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setenv("INCOMING_DIR", str(tmp_path / "incoming"))
    monkeypatch.setenv("PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))

    package_id = "golden_fisma_minimal"
    outcome = process_one_package(package_id, fixture=package_id, dry_run=True)

    assert outcome.status == "completed"
    assert outcome.llm_call_count == 0
    assert outcome.report_json_path is not None
    assert outcome.report_md_path is not None

    report = ReportModel.model_validate(
        json.loads(outcome.report_json_path.read_text(encoding="utf-8"))
    )
    assert report.evidence_matrix == []
    assert "DRY_RUN" in report.summary

    processed_raw = tmp_path / "processed" / package_id / "raw" / f"{package_id}.json"
    assert processed_raw.is_file()


def test_malformed_quarantined_zero_matrix_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("incoming", "processed", "quarantine", "reports", "audit"):
        (tmp_path / name).mkdir()

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setenv("INCOMING_DIR", str(tmp_path / "incoming"))
    monkeypatch.setenv("PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))

    package_id = "malformed_missing_control_id"
    outcome = process_one_package(package_id, fixture=package_id, dry_run=True)

    assert outcome.status == "quarantined"
    assert outcome.llm_call_count == 0
    assert outcome.report_json_path is None

    settings = load_settings()
    quarantine_file = settings.quarantine_dir / f"{package_id}.json"
    reason_file = settings.quarantine_dir / f"{package_id}.reason.json"
    assert quarantine_file.is_file()
    assert reason_file.is_file()

    reason = json.loads(reason_file.read_text(encoding="utf-8"))
    assert reason["reason"]["stage"] == "validate"
