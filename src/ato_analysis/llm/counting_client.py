"""LLM client wrapper that tracks completion call count."""

from __future__ import annotations

from typing import Any

from ato_analysis.llm.client import LLMClient


class CountingLLMClient:
    """Delegate to an inner client while counting complete_json invocations."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.call_count = 0

    def complete_json(
        self, *, system: str, user: str, schema_hint: str
    ) -> dict[str, Any]:
        self.call_count += 1
        return self._inner.complete_json(
            system=system, user=user, schema_hint=schema_hint
        )
