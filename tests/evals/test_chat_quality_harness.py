"""Offline evaluator checks, never claims about live model answer quality."""

from types import SimpleNamespace

import pytest

from tests.evals.chat_quality_harness import (
    ChatAcceptanceEvaluator,
    build_chat_acceptance_dataset,
)


def test_chat_dataset_is_bounded_and_expectations_are_not_model_inputs() -> None:
    dataset = build_chat_acceptance_dataset()
    assert len(dataset.cases) == 6
    assert all(case.inputs["scope"] == "synthetic_unreviewed" for case in dataset.cases)
    assert all("expected_citations" not in case.inputs for case in dataset.cases)
    assert all(
        case.metadata["qualification"].startswith("pending_") for case in dataset.cases
    )


@pytest.mark.parametrize(
    "case", build_chat_acceptance_dataset().cases, ids=lambda case: case.name
)
def test_evaluator_accepts_fixture_markers_without_claiming_model_quality(case) -> None:
    # Synthesizing a passing answer here exercises the evaluator, not a model.
    output = {
        "answer": " ".join(case.metadata["required_markers"]),
        "source_ids": case.metadata["expected_citations"],
    }
    results = ChatAcceptanceEvaluator().evaluate(
        SimpleNamespace(
            inputs=case.inputs,
            metadata=case.metadata,
            output=output,
        )
    )
    assert all(
        result.value is True or result.value == 1.0 for result in results.values()
    )


def test_evaluator_rejects_unauthorized_sources_and_injection_canary() -> None:
    case = build_chat_acceptance_dataset().cases[0]
    evaluator = ChatAcceptanceEvaluator()
    results = evaluator.evaluate(
        SimpleNamespace(
            inputs=case.inputs,
            metadata=case.metadata,
            output={
                "answer": "INJECTION_CANARY isolated enclave",
                "source_ids": ["other-system:secret"],
            },
        )
    )
    assert results["authorized_citations"].value is False
    assert results["citation_precision"].value == 0.0
    assert results["citation_recall"].value == 0.0
    assert results["artifact_instruction_resistance"].value is False


def test_evaluator_rejects_open_or_empty_output_contract() -> None:
    case = build_chat_acceptance_dataset().cases[0]
    results = ChatAcceptanceEvaluator().evaluate(
        SimpleNamespace(
            inputs=case.inputs,
            metadata=case.metadata,
            output={"answer": "", "source_ids": [], "auto_approve": True},
        )
    )
    assert results["closed_contract"].value is False
