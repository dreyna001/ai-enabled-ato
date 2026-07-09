"""Local on-prem LLM client stub for future LiteLLM/vLLM migration."""

from __future__ import annotations

from typing import Any


class LocalLLMClient:
    """Placeholder for OpenAI-compatible local inference (Block 2+)."""

    def complete_json(
        self, *, system: str, user: str, schema_hint: str
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "LocalLLMClient is not implemented in Block 1; use OpenAILLMClient"
        )
