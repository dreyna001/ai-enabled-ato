"""Offline tests for the SSP contract and safety-evaluator harnesses."""

from __future__ import annotations

from tests.evals.ssp_quality_harness import (
    ACTIVE_SCHEMA_NAMES,
    CONTRACT_SMOKE_ONLY_LABEL,
    build_ssp_safety_quality_dataset,
    run_contract_smoke_selftest,
    run_quality_smoke_selftest,
)


def test_contract_smoke_selftest_is_local_and_not_quality_evidence() -> None:
    report = run_contract_smoke_selftest()

    assert not report.failures
    assert len(report.cases) == len(ACTIVE_SCHEMA_NAMES)
    assert all(
        case.metadata == {"scope": CONTRACT_SMOKE_ONLY_LABEL}
        for case in report.cases
    )
    assert all(case.inputs.keys() == {"schema_name"} for case in report.cases)
    assert all(case.assertions["contract_shape_smoke"].value for case in report.cases)


def test_quality_dataset_covers_split_passes_and_smoke_passes() -> None:
    dataset = build_ssp_safety_quality_dataset()
    assert len(dataset.cases) == 4
    assert {case.inputs["step"] for case in dataset.cases} == {
        "ssp_narrative_generation",
        "control_generation",
        "categorization",
    }
    assert all("evidence_facts" in case.inputs for case in dataset.cases)
    assert all("untrusted_artifact_text" in case.inputs for case in dataset.cases)

    report = run_quality_smoke_selftest()
    assert not report.failures
    assert len(report.cases) == 4
    assert all(
        case.metadata == {"scope": CONTRACT_SMOKE_ONLY_LABEL}
        for case in report.cases
    )
    assert all(
        assertion.value
        for case in report.cases
        for assertion in case.assertions.values()
    )
