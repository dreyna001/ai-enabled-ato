"""Focused tests for idempotency headers and source artifact uniqueness migration."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import ato_service.db.models  # noqa: F401

from ato_service.db.base import Base

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "20260711_0004_idempotency_headers_artifact_uniq.py"
)
REVISION_VERSION_MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "20260711_0003_package_revision_version.py"
)


def _table(name: str):
    return Base.metadata.tables[name]


def _compile_create_table(table_name: str) -> str:
    return str(
        CreateTable(_table(table_name)).compile(dialect=postgresql.dialect())
    )


def _load_migration_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_op_sequence(path: Path, function_name: str) -> list[tuple[str, tuple, dict]]:
    module = _load_migration_module(path)
    mock_op = MagicMock()
    module.op = mock_op
    getattr(module, function_name)()
    return [(call[0], call.args, call.kwargs) for call in mock_op.method_calls]


def test_alembic_head_is_idempotency_headers_artifact_uniq_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260711_0005"


def test_migration_chains_from_revision_version_migration() -> None:
    module = _load_migration_module(MIGRATION_PATH)
    assert module.revision == "20260711_0004"
    assert module.down_revision == "20260711_0003"


def test_migration_is_explicit_and_additive() -> None:
    migration_source = MIGRATION_PATH.read_text(encoding="utf-8")
    revision_version_source = REVISION_VERSION_MIGRATION_PATH.read_text(encoding="utf-8")

    assert re.search(
        r'op\.add_column\(\s*\n?\s*["\']idempotency_records["\']',
        migration_source,
    )
    assert "response_headers" in migration_source
    assert "'{}'::jsonb" in migration_source
    assert "uq_source_artifacts_revision_sha256" in migration_source
    assert "HAVING count(*) > 1" in migration_source
    assert "reconcile before migration" in migration_source
    assert re.search(
        r'op\.create_unique_constraint\(\s*\n?\s*["\']uq_source_artifacts_revision_sha256["\']',
        migration_source,
    )

    assert "create_all" not in migration_source
    assert "drop_all" not in migration_source
    assert "from ato_service.db.base import Base" not in migration_source
    assert "import ato_service.db.models" not in migration_source
    assert "response_headers" not in revision_version_source
    assert "uq_source_artifacts_revision_sha256" not in revision_version_source


def test_migration_upgrade_and_downgrade_operation_order() -> None:
    upgrade_ops = _migration_op_sequence(MIGRATION_PATH, "upgrade")
    downgrade_ops = _migration_op_sequence(MIGRATION_PATH, "downgrade")

    assert [op_name for op_name, _args, _kwargs in upgrade_ops] == [
        "add_column",
        "execute",
        "create_unique_constraint",
    ]

    add_column_args = upgrade_ops[0][1]
    assert add_column_args[0] == "idempotency_records"
    column = add_column_args[1]
    assert column.name == "response_headers"
    assert column.nullable is False
    assert column.server_default is not None

    preflight_statement = upgrade_ops[1][1][0]
    assert "GROUP BY package_revision_id, sha256" in str(preflight_statement)

    unique_args, unique_kwargs = upgrade_ops[2][1], upgrade_ops[2][2]
    assert unique_args[0] == "uq_source_artifacts_revision_sha256"
    assert unique_args[1] == "source_artifacts"
    assert unique_args[2] == ["package_revision_id", "sha256"]

    assert [op_name for op_name, _args, _kwargs in downgrade_ops] == [
        "drop_constraint",
        "drop_column",
    ]


def test_postgresql_ddl_includes_response_headers_and_unique_constraint() -> None:
    idempotency_ddl = _compile_create_table("idempotency_records")
    assert "response_headers" in idempotency_ddl
    assert "JSONB" in idempotency_ddl

    source_artifacts_ddl = _compile_create_table("source_artifacts")
    assert "uq_source_artifacts_revision_sha256" in source_artifacts_ddl
    assert "package_revision_id" in source_artifacts_ddl
    assert "sha256" in source_artifacts_ddl
