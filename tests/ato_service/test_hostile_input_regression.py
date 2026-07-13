"""Hostile input and LLM refusal regression fixtures (Component J)."""

from __future__ import annotations

import json
from pathlib import Path

from ato_service.package_chat import chat_with_package

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "qualification" / "hostile-inputs" / "prompt-injection-fixtures.json"


def test_prompt_injection_fixtures_refuse() -> None:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for case in cases:
        response = chat_with_package(
            question=case["prompt"],
            sealed_document={"system": {"display_name": "Fixture"}},
            search_hits=[],
        )
        assert response["refused"] is True
        assert response["refusal_code"] == case["expected_refusal_code"]
