"""Conservative deterministic token budgeting for sufficiency_matrix."""

from __future__ import annotations

from ato_service.sufficiency_matrix.constants import CHARS_PER_TOKEN


def estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_tokens_from_object(value: object) -> int:
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


def compute_input_token_budget(
    *,
    context_tokens: int,
    max_output_tokens: int,
    instruction_overhead_tokens: int,
) -> int:
    budget = context_tokens - max_output_tokens - instruction_overhead_tokens
    if budget < 1:
        return 0
    return budget
