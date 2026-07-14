"""Focused tests for package revision intake work PostgreSQL persistence."""

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
from sqlalchemy.schema import CreateIndex, CreateTable

import ato_service.db.models  # noqa: F401

from ato_service.db import enums as db_enums
from ato_service.db.base import Base
from ato_service.db.models import PackageRevisionIntakeAttempt, PackageRevisionIntakeWork

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
INTAKE_WORK_MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "20260714_0008_package_revision_intake_work.py"
)

INTAKE_TABLES = frozenset(
    {"package_revision_intake_work", "package_revision_intake_attempts"}
)

WORK_COLUMNS = frozenset(
    {
        "package_revision_id",
        "work_phase",
        "status",
        "attempt_count",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "fence_token",
        "expected_revision_version",
        "last_error_code",
    }
)

ATTEMPT_COLUMNS = frozenset(
    {
        "attempt_id",
        "package_revision_id",
        "work_phase",
        "attempt_number",
        "status",
        "lease_owner",
        "fence_token",
        "expected_revision_version",
        "started_at",
        "completed_at",
        "error_code",
        "error_retryable",
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


def test_metadata_includes_intake_work_tables() -> None:
    assert INTAKE_TABLES <= set(Base.metadata.tables)


def test_model_classes_map_to_expected_tables() -> None:
    assert PackageRevisionIntakeWork.__tablename__ == "package_revision_intake_work"
    assert PackageRevisionIntakeAttempt.__tablename__ == "package_revision_intake_attempts"


def test_intake_attempt_primary_key_uses_uuid() -> None:
    pk_columns = _table("package_revision_intake_attempts").primary_key.columns
    assert len(pk_columns) == 1
    assert isinstance(pk_columns[0].type, UuidType)


def test_intake_work_composite_primary_key() -> None:
    pk_columns = list(_table("package_revision_intake_work").primary_key.columns)
    assert [column.name for column in pk_columns] == [
        "package_revision_id",
        "work_phase",
    ]


def test_intake_work_columns_match_contract() -> None:
    assert {column.name for column in _table("package_revision_intake_work").c} == WORK_COLUMNS


def test_intake_attempt_columns_match_contract() -> None:
    assert {column.name for column in _table("package_revision_intake_attempts").c} == ATTEMPT_COLUMNS


def test_intake_work_status_enum_matches_db_enums() -> None:
    assert set(db_enums.INTAKE_WORK_STATUS_VALUES) == set(
        _domain_enum_values("PackageRevisionIntakeWork", "status")
    )


def test_intake_work_phase_enum_matches_db_enums() -> None:
    assert set(db_enums.INTAKE_WORK_PHASE_VALUES) == set(
        _domain_enum_values("PackageRevisionIntakeWork", "work_phase")
    )


def test_intake_attempt_status_enum_matches_db_enums() -> None:
    assert set(db_enums.INTAKE_ATTEMPT_STATUS_VALUES) == set(
        _domain_enum_values("PackageRevisionIntakeAttempt", "status")
    )


def test_intake_work_lease_fields_match_status_in_model() -> None:
    table = _table("package_revision_intake_work")
    lease_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_package_revision_intake_work_lease_fields_match_status"
    ]
    assert len(lease_constraints) == 1
    ddl = _compile_create_table("package_revision_intake_work")
    assert "fence_token IS NOT NULL" in ddl
    assert "fence_token IS NULL" in ddl


def test_intake_attempt_one_active_per_work_partial_unique_index_ddl() -> None:
    table = _table("package_revision_intake_attempts")
    partial_index = next(
        index
        for index in table.indexes
        if index.name == "uq_package_revision_intake_attempts_one_active_per_work"
    )
    assert partial_index.unique is True
    assert list(partial_index.columns.keys()) == ["package_revision_id", "work_phase"]

    dialect = postgresql.dialect()
    index_sql = str(CreateIndex(partial_index).compile(dialect=dialect))
    assert (
        "CREATE UNIQUE INDEX uq_package_revision_intake_attempts_one_active_per_work"
        in index_sql
    )
    assert "WHERE (status = 'active')" in index_sql or "WHERE status = 'active'" in index_sql


def test_intake_work_foreign_key_to_package_revisions() -> None:
    table = _table("package_revision_intake_work")
    fks = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert any(
        fk.name is None or "package_revisions" in str(fk.elements[0].column)
        for fk in fks
    )


def test_intake_attempt_foreign_key_to_work_row() -> None:
    table = _table("package_revision_intake_attempts")
    composite_fk = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_package_revision_intake_attempts_work"
    )
    assert composite_fk is not None


def test_intake_attempt_unique_attempt_number_per_work() -> None:
    table = _table("package_revision_intake_attempts")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_pr_intake_attempt_revision_phase_number" in unique


def test_alembic_head_is_package_revision_intake_work_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260716_0011"


def test_intake_work_migration_chains_from_package_editor_persistence() -> None:
    module = _load_migration_module(INTAKE_WORK_MIGRATION_PATH)
    assert module.revision == "20260714_0008"
    assert module.down_revision == "20260713_0007"


def test_intake_work_migration_is_explicit_and_additive() -> None:
    migration_source = INTAKE_WORK_MIGRATION_PATH.read_text(encoding="utf-8")
    for table_name in INTAKE_TABLES:
        assert re.search(
            rf"op\.create_table\(\s*\n?\s*[\"']{table_name}[\"']",
            migration_source,
        )


def test_intake_work_migration_constraints_match_models() -> None:
    migration_source = INTAKE_WORK_MIGRATION_PATH.read_text(encoding="utf-8")
    for table_name in INTAKE_TABLES:
        for constraint_name in _constraint_names(table_name):
            assert constraint_name in migration_source
