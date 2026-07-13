"""Focused tests for PackageRevision revision_version PostgreSQL persistence."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import ato_service.db.models  # noqa: F401

from ato_service.db.base import Base
from ato_service.db.models import PackageRevision

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
REVISION_VERSION_MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "20260711_0003_package_revision_version.py"
)
JOBS_MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "20260711_0002_jobs_and_job_attempts.py"
)


def _domain_schema_property(def_name: str, property_name: str) -> dict[str, object]:
    schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema["$defs"][def_name]["properties"][property_name]


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


def _migration_op_sequence(
    path: Path, function_name: str
) -> list[tuple[str, tuple[object, ...], dict[str, object]]]:
    module = _load_migration_module(path)
    mock_op = MagicMock()
    module.op = mock_op
    getattr(module, function_name)()
    return [(call[0], call.args, call.kwargs) for call in mock_op.method_calls]


def test_package_revision_model_exposes_revision_version_column() -> None:
    column = PackageRevision.__table__.c.revision_version
    assert column.nullable is False
    assert str(column.server_default.arg) == "1"


def test_package_revision_columns_include_revision_version() -> None:
    columns = {column.name for column in _table("package_revisions").c}
    assert "revision_version" in columns


def test_revision_version_matches_contract_minimum() -> None:
    revision_version = _domain_schema_property("PackageRevision", "revision_version")
    assert revision_version["type"] == "integer"
    assert revision_version["minimum"] == 1


def test_revision_version_positive_check_in_model() -> None:
    table = _table("package_revisions")
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert "ck_package_revisions_revision_version_positive" in checks
    assert checks["ck_package_revisions_revision_version_positive"] == "revision_version >= 1"


def test_revision_version_ddl_has_server_default_and_positive_check() -> None:
    ddl = _compile_create_table("package_revisions")
    revision_version_line = next(
        line for line in ddl.splitlines() if "revision_version" in line and "CHECK" not in line
    )
    assert "revision_version INTEGER" in revision_version_line
    assert "DEFAULT 1" in revision_version_line
    assert "NOT NULL" in revision_version_line
    assert "revision_version >= 1" in ddl
    assert "ck_package_revisions_revision_version_positive" in _constraint_names(
        "package_revisions"
    )


def test_alembic_head_is_package_editor_persistence_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260715_0010"


def test_revision_version_migration_chains_from_jobs_migration() -> None:
    module = _load_migration_module(REVISION_VERSION_MIGRATION_PATH)
    assert module.revision == "20260711_0003"
    assert module.down_revision == "20260711_0002"


def test_revision_version_migration_is_explicit_and_additive() -> None:
    migration_source = REVISION_VERSION_MIGRATION_PATH.read_text(encoding="utf-8")
    jobs_source = JOBS_MIGRATION_PATH.read_text(encoding="utf-8")

    assert re.search(
        r'op\.add_column\(\s*\n?\s*["\']package_revisions["\']',
        migration_source,
    )
    assert "revision_version" in migration_source
    assert "ck_package_revisions_revision_version_positive" in migration_source
    assert "revision_version >= 1" in migration_source

    assert "create_all" not in migration_source
    assert "drop_all" not in migration_source
    assert "from ato_service.db.base import Base" not in migration_source
    assert "import ato_service.db.models" not in migration_source
    assert "revision_version" not in jobs_source


def test_revision_version_migration_upgrade_and_downgrade_operation_order() -> None:
    upgrade_ops = _migration_op_sequence(REVISION_VERSION_MIGRATION_PATH, "upgrade")
    downgrade_ops = _migration_op_sequence(REVISION_VERSION_MIGRATION_PATH, "downgrade")

    assert [op_name for op_name, _args, _kwargs in upgrade_ops] == [
        "add_column",
        "create_check_constraint",
    ]

    add_column_args, add_column_kwargs = upgrade_ops[0][1], upgrade_ops[0][2]
    assert add_column_args[0] == "package_revisions"
    column = add_column_args[1]
    assert column.name == "revision_version"
    assert column.nullable is False
    assert str(column.server_default.arg) == "1"

    create_check_args = upgrade_ops[1][1]
    assert create_check_args == (
        "ck_package_revisions_revision_version_positive",
        "package_revisions",
        "revision_version >= 1",
    )

    assert [op_name for op_name, _args, _kwargs in downgrade_ops] == [
        "drop_constraint",
        "drop_column",
    ]

    drop_constraint_args, drop_constraint_kwargs = (
        downgrade_ops[0][1],
        downgrade_ops[0][2],
    )
    assert drop_constraint_args == (
        "ck_package_revisions_revision_version_positive",
        "package_revisions",
    )
    assert drop_constraint_kwargs == {"type_": "check"}

    drop_column_args = downgrade_ops[1][1]
    assert drop_column_args == ("package_revisions", "revision_version")


def test_revision_version_migration_constraints_match_models() -> None:
    migration_source = REVISION_VERSION_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ck_package_revisions_revision_version_positive" in migration_source
    assert "revision_version >= 1" in migration_source
    assert "ck_package_revisions_revision_version_positive" in _constraint_names(
        "package_revisions"
    )
