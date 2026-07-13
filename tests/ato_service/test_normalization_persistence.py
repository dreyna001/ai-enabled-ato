"""Focused tests for package normalization step PostgreSQL persistence."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, Uuid as UuidType
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import ato_service.db.models  # noqa: F401

from ato_service.db import enums as db_enums
from ato_service.db.base import Base
from ato_service.db.models import PackageNormalizationStep

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
NORMALIZATION_MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "20260714_0009_package_normalization_steps.py"
)

NORMALIZATION_TABLES = frozenset({"package_normalization_steps"})

STEP_COLUMNS = frozenset(
    {
        "step_id",
        "package_revision_id",
        "step_key",
        "status",
        "input_digest",
        "fact_bundle_sha256",
        "schema_id",
        "prompt_version",
        "prompt_sha256",
        "prompt_storage_key",
        "fact_bundle_storage_key",
        "response_storage_key",
        "endpoint_profile",
        "endpoint_host",
        "model_requested",
        "model_reported",
        "temperature",
        "input_limit",
        "output_limit",
        "timeout_seconds",
        "attempt",
        "provider_request_id",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "response_sha256",
        "validation_outcome",
        "llm_call_count",
        "repair_attempted",
        "error_code",
        "error_retryable",
        "created_at",
        "started_at",
        "completed_at",
    }
)


def _domain_enum_values(def_name: str, property_name: str) -> set[str]:
    schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    return set(schema["$defs"][def_name]["properties"][property_name]["enum"])


def _table(name: str):
    return Base.metadata.tables[name]


def _compile_create_table(table_name: str) -> str:
    return str(
        CreateTable(_table(table_name)).compile(dialect=postgresql.dialect())
    )


def _constraint_names(table_name: str) -> set[str]:
    table = _table(table_name)
    return {
        constraint.name
        for constraint in table.constraints
        if constraint.name
    }


def _load_migration_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_op_sequence(path: Path, function_name: str) -> list[tuple[str, tuple[object, ...], dict[str, object]]]:
    module = _load_migration_module(path)
    mock_op = MagicMock()
    module.op = mock_op
    getattr(module, function_name)()
    return [(call[0], call.args, call.kwargs) for call in mock_op.method_calls]


def test_metadata_includes_normalization_step_table() -> None:
    assert NORMALIZATION_TABLES <= set(Base.metadata.tables)


def test_model_class_maps_to_expected_table() -> None:
    assert PackageNormalizationStep.__tablename__ == "package_normalization_steps"


def test_normalization_step_primary_key_uses_uuid() -> None:
    pk_columns = _table("package_normalization_steps").primary_key.columns
    assert len(pk_columns) == 1
    assert isinstance(pk_columns[0].type, UuidType)


def test_normalization_step_columns_match_contract() -> None:
    assert {column.name for column in _table("package_normalization_steps").c} == STEP_COLUMNS


def test_normalization_step_status_enum_matches_db_enums() -> None:
    assert set(db_enums.NORMALIZATION_STEP_STATUS_VALUES) == set(
        _domain_enum_values("PackageNormalizationStep", "status")
    )


def test_normalization_step_unique_revision_step_key() -> None:
    table = _table("package_normalization_steps")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_pkg_norm_steps_revision_step_key" in unique


def test_normalization_step_foreign_key_to_package_revisions() -> None:
    table = _table("package_normalization_steps")
    fks = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert any(
        "package_revisions" in str(list(fk.elements)[0].column)
        for fk in fks
    )


def test_normalization_step_status_fields_constraint_in_model() -> None:
    table = _table("package_normalization_steps")
    status_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_pkg_norm_steps_status_fields"
    ]
    assert len(status_constraints) == 1
    ddl = _compile_create_table("package_normalization_steps")
    assert "status = 'policy_blocked'" in ddl
    assert "validation_outcome IS NOT NULL" in ddl
    assert "status = 'running'" in ddl
    assert "prompt_storage_key IS NOT NULL" in ddl
    assert "status = 'completed'" in ddl
    assert "response_sha256 IS NOT NULL" in ddl
    assert "model_requested IS NOT NULL" in ddl
    assert "model_reported IS NOT NULL" not in ddl


def test_normalization_step_repair_requires_two_calls_constraint() -> None:
    ddl = _compile_create_table("package_normalization_steps")
    assert "repair_attempted = false OR llm_call_count = 2" in ddl


def test_normalization_step_llm_call_count_range_constraint() -> None:
    ddl = _compile_create_table("package_normalization_steps")
    assert "llm_call_count >= 0 AND llm_call_count <= 2" in ddl


def test_alembic_head_is_package_normalization_steps_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260715_0010"


def test_normalization_migration_chains_from_intake_work() -> None:
    module = _load_migration_module(NORMALIZATION_MIGRATION_PATH)
    assert module.revision == "20260714_0009"
    assert module.down_revision == "20260714_0008"


def test_normalization_migration_is_explicit_and_additive() -> None:
    migration_source = NORMALIZATION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"op\.create_table\(\s*\n?\s*[\"']package_normalization_steps[\"']",
        migration_source,
    )


def test_normalization_migration_constraints_match_models() -> None:
    migration_source = NORMALIZATION_MIGRATION_PATH.read_text(encoding="utf-8")
    for constraint_name in _constraint_names("package_normalization_steps"):
        assert constraint_name in migration_source


def test_normalization_migration_downgrade_drops_table() -> None:
    calls = _migration_op_sequence(NORMALIZATION_MIGRATION_PATH, "downgrade")
    assert calls[-1][0] == "drop_table"
    assert calls[-1][1][0] == "package_normalization_steps"
