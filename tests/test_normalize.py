"""Tests for LLM and deterministic normalization."""

from __future__ import annotations

import copy

from ato_analysis.normalize.normalize_llm import normalize_to_canonical
from tests.conftest import MockLLMClient


def test_canonical_input_skips_llm(golden_package: dict, test_settings) -> None:
    client = MockLLMClient(responses=[])

    package = normalize_to_canonical(
        golden_package,
        "golden_fisma_minimal",
        client,
        test_settings,
    )

    assert client.call_count == 0
    assert package.package_id == "golden_fisma_minimal"
    assert len(package.controls) == 5


def test_messy_fixture_normalizes_with_mocked_llm(
    load_fixture,
    golden_package: dict,
    test_settings,
) -> None:
    messy = load_fixture("messy_grc_export")
    canonical_response = copy.deepcopy(golden_package)
    client = MockLLMClient(responses=[canonical_response])

    package = normalize_to_canonical(
        messy,
        "golden_fisma_minimal",
        client,
        test_settings,
    )

    assert client.call_count == 1
    assert package.package_id == "golden_fisma_minimal"
    assert package.authorization_path == "fisma_agency"
    assert len(package.controls) >= 5
    assert len(package.evidence_items) >= 8


def test_normalize_repair_path_on_invalid_llm_output(
    load_fixture,
    golden_package: dict,
    test_settings,
) -> None:
    messy = load_fixture("messy_grc_export")
    invalid = {"package_id": "golden_fisma_minimal", "controls": "not-a-list"}
    client = MockLLMClient(responses=[invalid, golden_package])

    package = normalize_to_canonical(
        messy,
        "golden_fisma_minimal",
        client,
        test_settings,
    )

    assert client.call_count == 2
    assert package.package_id == "golden_fisma_minimal"


def test_normalize_calls_llm_when_canonical_package_id_mismatch(
    golden_package: dict,
    test_settings,
) -> None:
    """Canonical fast-path requires package_id to match the filename stem."""
    client = MockLLMClient(responses=[golden_package])

    package = normalize_to_canonical(
        golden_package,
        "different_package_id",
        client,
        test_settings,
    )

    assert client.call_count == 1
    assert package.package_id == "golden_fisma_minimal"
