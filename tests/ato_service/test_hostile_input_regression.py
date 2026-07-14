"""Hostile input and LLM refusal regression fixtures (Component J)."""

from __future__ import annotations

import json
from pathlib import Path

from ato_service.package_chat import evaluate_refusal

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "qualification" / "hostile-inputs" / "prompt-injection-fixtures.json"


def test_prompt_injection_fixtures_refuse() -> None:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for case in cases:
        assert evaluate_refusal(question=case["prompt"]) == case["expected_refusal_code"]
