"""Shared pytest fixtures for Block 1 tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ato_analysis.config import PROJECT_ROOT, Settings, load_settings
from ato_analysis.models.package_schema import PackageModel

FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


class MockLLMClient:
    """In-memory LLM client for unit tests (no network)."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    def complete_json(
        self, *, system: str, user: str, schema_hint: str
    ) -> dict[str, Any]:
        _ = (system, user, schema_hint)
        self.call_count += 1
        if not self._responses:
            raise RuntimeError("MockLLMClient has no remaining responses")
        return self._responses.pop(0)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def load_fixture() -> Callable[[str], dict[str, Any]]:
    def _load(name: str) -> dict[str, Any]:
        path = FIXTURES_DIR / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def golden_package(load_fixture: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    return load_fixture("golden_fisma_minimal")


@pytest.fixture
def golden_model(golden_package: dict[str, Any]) -> PackageModel:
    return PackageModel.model_validate(golden_package)


@pytest.fixture
def expected_golden(load_fixture: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    return load_fixture("golden_fisma_minimal.expected")


@pytest.fixture
def test_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Isolated data dirs with DRY_RUN enabled (no live OpenAI required)."""
    for name in ("incoming", "processed", "quarantine", "reports", "audit"):
        (tmp_path / name).mkdir()

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setenv("INCOMING_DIR", str(tmp_path / "incoming"))
    monkeypatch.setenv("PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("PREFLIGHT_BLOCK_THRESHOLD", "0.6")
    return load_settings()


def matrix_row_for_control(
    package: PackageModel,
    control_id: str,
    *,
    sufficiency_status: str = "partial",
    stale_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a valid matrix row dict with a verbatim citation excerpt."""
    control = next(c for c in package.controls if c.control_id == control_id)
    evidence_id = control.linked_evidence_ids[0]
    evidence = next(e for e in package.evidence_items if e.evidence_id == evidence_id)
    excerpt = evidence.text[:80].strip()
    if len(excerpt) < 20:
        excerpt = evidence.text.strip()

    row: dict[str, Any] = {
        "control_id": control_id,
        "sufficiency_status": sufficiency_status,
        "finding_summary": f"Review of [{evidence_id}] for {control_id}.",
        "gaps": [],
        "stale_evidence_ids": list(stale_ids or []),
        "assessor_questions": [],
        "citations": [],
    }
    if sufficiency_status == "supported":
        row["citations"] = [{"evidence_id": evidence_id, "excerpt": excerpt}]
    return row


def matrix_batch_response(
    package: PackageModel,
    *,
    status_by_control: dict[str, str] | None = None,
    stale_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a full matrix LLM response for all controls in a package."""
    status_by_control = status_by_control or {}
    rows = [
        matrix_row_for_control(
            package,
            control.control_id,
            sufficiency_status=status_by_control.get(control.control_id, "partial"),
            stale_ids=stale_ids if control.control_id == "AC-2" else [],
        )
        for control in package.controls
    ]
    return {"rows": rows}
