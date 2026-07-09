"""Deterministic parsers for known evidence formats (Block 3)."""

from __future__ import annotations

from typing import Any

from ato_analysis.models.package_schema import PackageModel


def try_deterministic_normalize(
    raw: dict[str, Any] | str,
    package_id: str,
) -> PackageModel | None:
    """Return a canonical package when a known format parser applies.

    Block 1 always returns None; deterministic parsers are added in Block 3.
    """
    _ = (raw, package_id)
    return None
