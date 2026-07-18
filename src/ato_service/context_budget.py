"""Shared deterministic context budgeting for model-assisted steps."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

CHARS_PER_TOKEN = 4
DEFAULT_CONTEXT_UTILIZATION_TARGET = 0.70
INSTRUCTION_OVERHEAD_TOKENS = 2048


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Resolved input-token budget for one model call."""

    utilization_target: float
    context_tokens: int
    max_output_tokens: int
    instruction_overhead_tokens: int
    utilization_cap_tokens: int
    reserve_cap_tokens: int
    input_budget_tokens: int


@dataclass(frozen=True, slots=True)
class RankedPackEntry:
    """One ranked candidate entry with a precomputed token estimate."""

    entry_id: str
    token_estimate: int


@dataclass(frozen=True, slots=True)
class RankedPackResult:
    """Deterministic ranked packing outcome."""

    included_entry_ids: tuple[str, ...]
    omitted_entry_ids: tuple[str, ...]
    context_complete: bool
    used_entry_tokens: int
    remaining_tokens: int


def estimate_tokens_from_text(text: str) -> int:
    """Estimate token count using fixed character-to-token ratio."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_tokens_from_object(value: object) -> int:
    """Estimate token count for JSON-serializable prompt payload fragments."""
    if value is None:
        return 1
    if isinstance(value, str):
        return estimate_tokens_from_text(value)
    if isinstance(value, (bool, int, float)):
        return 1
    if isinstance(value, list):
        return sum(estimate_tokens_from_object(item) for item in value) or 1
    if isinstance(value, dict):
        total = 0
        for key, item in value.items():
            total += estimate_tokens_from_text(str(key)) + estimate_tokens_from_object(item)
        return total or 1
    return estimate_tokens_from_text(str(value))


def validate_utilization_target(value: object) -> float:
    """Validate a configured utilization target in the open interval (0, 1]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("CONTEXT_UTILIZATION_TARGET must be a number")
    target = float(value)
    if target <= 0.0 or target > 1.0:
        raise ValueError("CONTEXT_UTILIZATION_TARGET must be greater than 0 and at most 1")
    return target


def compute_input_token_budget(
    *,
    context_tokens: int,
    max_output_tokens: int,
    instruction_overhead_tokens: int,
    utilization_target: float = DEFAULT_CONTEXT_UTILIZATION_TARGET,
) -> int:
    """Compute the maximum input-token budget for one model call."""
    validate_utilization_target(utilization_target)
    utilization_cap = floor(context_tokens * utilization_target)
    reserve_cap = context_tokens - max_output_tokens - instruction_overhead_tokens
    return max(0, min(utilization_cap, reserve_cap))


def resolve_context_budget(
    *,
    context_tokens: int,
    max_output_tokens: int,
    instruction_overhead_tokens: int = INSTRUCTION_OVERHEAD_TOKENS,
    utilization_target: float = DEFAULT_CONTEXT_UTILIZATION_TARGET,
) -> ContextBudget:
    """Resolve the full budget breakdown for observability and call-site checks."""
    target = validate_utilization_target(utilization_target)
    utilization_cap = floor(context_tokens * target)
    reserve_cap = context_tokens - max_output_tokens - instruction_overhead_tokens
    input_budget = max(0, min(utilization_cap, reserve_cap))
    return ContextBudget(
        utilization_target=target,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        instruction_overhead_tokens=instruction_overhead_tokens,
        utilization_cap_tokens=utilization_cap,
        reserve_cap_tokens=reserve_cap,
        input_budget_tokens=input_budget,
    )


def pack_ranked_entries(
    *,
    entries: tuple[RankedPackEntry, ...],
    input_budget: int,
    fixed_payload_tokens: int,
) -> RankedPackResult:
    """Pack pre-ranked entries deterministically into the remaining input budget."""
    if fixed_payload_tokens < 0:
        raise ValueError("fixed_payload_tokens must be non-negative")
    if input_budget < fixed_payload_tokens:
        return RankedPackResult(
            included_entry_ids=(),
            omitted_entry_ids=tuple(entry.entry_id for entry in entries),
            context_complete=False,
            used_entry_tokens=0,
            remaining_tokens=0,
        )

    remaining = input_budget - fixed_payload_tokens
    included: list[str] = []
    omitted: list[str] = []
    used_entry_tokens = 0

    for entry in entries:
        if entry.token_estimate <= remaining:
            included.append(entry.entry_id)
            used_entry_tokens += entry.token_estimate
            remaining -= entry.token_estimate
            continue
        omitted.append(entry.entry_id)

    return RankedPackResult(
        included_entry_ids=tuple(included),
        omitted_entry_ids=tuple(omitted),
        context_complete=not omitted,
        used_entry_tokens=used_entry_tokens,
        remaining_tokens=remaining,
    )
