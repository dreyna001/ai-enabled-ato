"""Persistence tests for matrix_rows migration."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations/versions/20260711_0006_matrix_rows.py"


def test_alembic_head_is_package_editor_persistence_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260715_0010"


def test_matrix_rows_migration_declares_table_and_constraints() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'op.create_table(\n        "matrix_rows"' in source
    assert "uq_matrix_rows_run_id_assessment_item_id" in source
    assert "assessment_item_ids" in source
    assert "ck_analysis_runs_assessment_item_ids_array" in source
