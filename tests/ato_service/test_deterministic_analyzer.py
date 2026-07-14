"""Tests for deterministic analyzer matrix generation."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from ato_service.analysis_profile import expected_assessment_item_ids, load_pinned_fisma_synthetic_profile
from ato_service.deterministic_analyzer import (
    _build_matrix_rows,
    require_deterministic_analyzer_runtime,
)
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def test_build_matrix_rows_covers_all_profile_items_with_zero_llm_status() -> None:
    profile = load_pinned_fisma_synthetic_profile(project_root=ROOT)
    expected_ids = expected_assessment_item_ids(profile)
    rows = _build_matrix_rows(
        run_id=RUN_ID,
        profile=profile,
        assessment_item_ids=expected_ids,
    )

    assert len(rows) == len(expected_ids)
    assert {row["assessment_item_id"] for row in rows} == set(expected_ids)
    for row in rows:
        assert row["model_proposed_status"] == "insufficient_evidence"
        assert row["system_status"] == "insufficient_evidence"
        assert row["citations"] == []
        assert row["producing_run_id"] == str(RUN_ID).lower()


def test_require_deterministic_analyzer_runtime_rejects_non_dev_local(tmp_path: Path) -> None:
    from ato_service.deterministic_analyzer import DeterministicAnalyzerRuntimeError
    from ato_service.runtime_config import RuntimeConfigValidationError

    with pytest.raises((DeterministicAnalyzerRuntimeError, RuntimeConfigValidationError)):
        require_deterministic_analyzer_runtime(
            load_runtime_config_from_dict(
                {
                    "schema_version": "1.0.0",
                    "runtime_profile": "onprem",
                    "STORAGE_DATA_PATH": str(tmp_path / "storage"),
                },
                base_dir=tmp_path,
            )
        )
