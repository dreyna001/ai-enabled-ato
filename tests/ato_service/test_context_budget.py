"""Tests for shared context budgeting."""

from __future__ import annotations

import pytest

from ato_service.context_budget import (
    DEFAULT_CONTEXT_UTILIZATION_TARGET,
    INSTRUCTION_OVERHEAD_TOKENS,
    RankedPackEntry,
    compute_input_token_budget,
    estimate_tokens_from_object,
    estimate_tokens_from_text,
    pack_ranked_entries,
    resolve_context_budget,
    validate_utilization_target,
)
from ato_service.normalize_proposal.tokens import compute_input_token_budget as legacy_compute_input_token_budget


def _legacy_input_budget(
    *,
    context_tokens: int,
    max_output_tokens: int,
    instruction_overhead_tokens: int,
) -> int:
    return legacy_compute_input_token_budget(
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        instruction_overhead_tokens=instruction_overhead_tokens,
    )


def test_default_utilization_target_is_point_seven() -> None:
    assert DEFAULT_CONTEXT_UTILIZATION_TARGET == 0.70


def test_default_budget_uses_seventy_percent_utilization_cap() -> None:
    budget = resolve_context_budget(
        context_tokens=8192,
        max_output_tokens=1024,
        instruction_overhead_tokens=INSTRUCTION_OVERHEAD_TOKENS,
    )

    assert budget.utilization_target == 0.70
    assert budget.utilization_cap_tokens == 5734
    assert budget.reserve_cap_tokens == 5120
    assert budget.input_budget_tokens == 5120


def test_compute_input_token_budget_matches_locked_formula() -> None:
    assert compute_input_token_budget(
        context_tokens=8192,
        max_output_tokens=1024,
        instruction_overhead_tokens=2048,
        utilization_target=0.70,
    ) == max(
        0,
        min(
            5734,
            8192 - 1024 - 2048,
        ),
    )


def test_target_one_matches_legacy_reserve_only_behavior() -> None:
    cases = (
        (8192, 1024, 2048),
        (1000, 100, 2048),
        (4096, 4096, 2048),
        (500, 200, 100),
    )
    for context_tokens, max_output_tokens, instruction_overhead_tokens in cases:
        assert compute_input_token_budget(
            context_tokens=context_tokens,
            max_output_tokens=max_output_tokens,
            instruction_overhead_tokens=instruction_overhead_tokens,
            utilization_target=1.0,
        ) == _legacy_input_budget(
            context_tokens=context_tokens,
            max_output_tokens=max_output_tokens,
            instruction_overhead_tokens=instruction_overhead_tokens,
        )


def test_reserve_dominates_when_smaller_than_utilization_cap() -> None:
    budget = resolve_context_budget(
        context_tokens=8192,
        max_output_tokens=4096,
        instruction_overhead_tokens=2048,
        utilization_target=0.70,
    )

    assert budget.utilization_cap_tokens == 5734
    assert budget.reserve_cap_tokens == 2048
    assert budget.input_budget_tokens == 2048


def test_utilization_cap_dominates_when_reserve_is_larger() -> None:
    budget = resolve_context_budget(
        context_tokens=1000,
        max_output_tokens=100,
        instruction_overhead_tokens=128,
        utilization_target=0.70,
    )

    assert budget.utilization_cap_tokens == 700
    assert budget.reserve_cap_tokens == 772
    assert budget.input_budget_tokens == 700


def test_impossible_budget_returns_zero() -> None:
    assert (
        compute_input_token_budget(
            context_tokens=1000,
            max_output_tokens=900,
            instruction_overhead_tokens=2048,
            utilization_target=0.70,
        )
        == 0
    )


def test_validate_utilization_target_rejects_bounds_and_bool() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        validate_utilization_target(0)
    with pytest.raises(ValueError, match="at most 1"):
        validate_utilization_target(1.01)
    with pytest.raises(ValueError, match="must be a number"):
        validate_utilization_target(True)


def test_token_estimators_use_chars_per_token_four() -> None:
    assert estimate_tokens_from_text("") == 0
    assert estimate_tokens_from_text("abcd") == 1
    assert estimate_tokens_from_text("abcde") == 2
    assert estimate_tokens_from_object({"key": "abcd"}) == 2


def test_pack_ranked_entries_preserves_stable_order_and_reports_omissions() -> None:
    entries = (
        RankedPackEntry("b", 40),
        RankedPackEntry("a", 30),
        RankedPackEntry("c", 50),
    )
    result = pack_ranked_entries(
        entries=entries,
        input_budget=100,
        fixed_payload_tokens=20,
    )

    assert result.included_entry_ids == ("b", "a")
    assert result.omitted_entry_ids == ("c",)
    assert result.context_complete is False
    assert result.used_entry_tokens == 70
    assert result.remaining_tokens == 10


def test_pack_ranked_entries_marks_complete_when_all_entries_fit() -> None:
    entries = (
        RankedPackEntry("first", 10),
        RankedPackEntry("second", 15),
    )
    result = pack_ranked_entries(
        entries=entries,
        input_budget=50,
        fixed_payload_tokens=20,
    )

    assert result.included_entry_ids == ("first", "second")
    assert result.omitted_entry_ids == ()
    assert result.context_complete is True
    assert result.used_entry_tokens == 25
    assert result.remaining_tokens == 5


def test_pack_ranked_entries_omits_all_when_fixed_payload_exceeds_budget() -> None:
    entries = (RankedPackEntry("only", 10),)
    result = pack_ranked_entries(
        entries=entries,
        input_budget=10,
        fixed_payload_tokens=11,
    )

    assert result.included_entry_ids == ()
    assert result.omitted_entry_ids == ("only",)
    assert result.context_complete is False
    assert result.used_entry_tokens == 0
    assert result.remaining_tokens == 0
