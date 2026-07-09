"""LLM client protocol for structured JSON completions."""

from __future__ import annotations

from typing import Any, Protocol


class LLMClient(Protocol):
    """Transport-agnostic client for schema-bound JSON LLM steps."""

    def complete_json(
        self, *, system: str, user: str, schema_hint: str
    ) -> dict[str, Any]:
        """Return a parsed JSON object from the model response."""
        ...
