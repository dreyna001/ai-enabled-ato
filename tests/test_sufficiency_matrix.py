"""Tests for evidence sufficiency matrix LLM step."""

from __future__ import annotations

import pytest

from ato_analysis.analysis.sufficiency_matrix import (
    MatrixValidationError,
    _build_control_fact_record,
    run_sufficiency_matrix,
)
from tests.conftest import MockLLMClient, matrix_batch_response, matrix_row_for_control


def test_control_fact_record_includes_stale_flags_and_package_context(
    golden_model,
) -> None:
    evidence_by_id = {item.evidence_id: item for item in golden_model.evidence_items}
    stale_set = {"EV-AC2-REVIEW"}
    control = next(c for c in golden_model.controls if c.control_id == "AC-2")
    fact = _build_control_fact_record(
        control,
        evidence_by_id,
        package=golden_model,
        stale_set=stale_set,
    )

    assert fact["package_context"]["assessment_date"] == "2026-06-15"
    assert fact["package_context"]["freshness_threshold_days"] == 365
    review = next(
        item for item in fact["linked_evidence"] if item["evidence_id"] == "EV-AC2-REVIEW"
    )
    assert review["is_stale"] is True
    policy = next(
        item for item in fact["linked_evidence"] if item["evidence_id"] == "EV-AC2-POLICY"
    )
    assert policy["is_stale"] is False


def test_matrix_validates_mocked_rows(golden_model, test_settings) -> None:
    stale_ids = ["EV-AC2-REVIEW"]
    response = matrix_batch_response(
        golden_model,
        status_by_control={"AU-6": "supported"},
        stale_ids=stale_ids,
    )
    client = MockLLMClient(responses=[response])

    rows = run_sufficiency_matrix(golden_model, stale_ids, client, test_settings)

    assert client.call_count == 1
    assert len(rows) == len(golden_model.controls)
    control_ids = {row.control_id for row in rows}
    assert control_ids == {c.control_id for c in golden_model.controls}

    au6 = next(row for row in rows if row.control_id == "AU-6")
    assert au6.sufficiency_status == "supported"
    assert len(au6.citations) >= 1

    ac2 = next(row for row in rows if row.control_id == "AC-2")
    assert "EV-AC2-REVIEW" in ac2.stale_evidence_ids


def test_matrix_repair_path_on_invalid_first_response(golden_model, test_settings) -> None:
    stale_ids = ["EV-AC2-REVIEW"]
    control = golden_model.controls[0]
    evidence = next(
        e for e in golden_model.evidence_items if e.evidence_id == control.linked_evidence_ids[0]
    )
    invalid_row = matrix_row_for_control(
        golden_model,
        control.control_id,
        sufficiency_status="supported",
    )
    invalid_row["citations"] = [
        {"evidence_id": evidence.evidence_id, "excerpt": "This text is not in the evidence."}
    ]
    invalid_response = {"rows": [invalid_row]}
    repaired_response = matrix_batch_response(golden_model, stale_ids=stale_ids)

    client = MockLLMClient(responses=[invalid_response, repaired_response])

    rows = run_sufficiency_matrix(golden_model, stale_ids, client, test_settings)

    assert client.call_count == 2
    assert len(rows) == len(golden_model.controls)


def test_matrix_raises_after_failed_repair(golden_model, test_settings) -> None:
    stale_ids: list[str] = []
    invalid_response = {"rows": [{"control_id": "UNKNOWN-99", "sufficiency_status": "partial"}]}
    client = MockLLMClient(responses=[invalid_response, invalid_response])

    with pytest.raises(MatrixValidationError):
        run_sufficiency_matrix(golden_model, stale_ids, client, test_settings)

    assert client.call_count == 2
