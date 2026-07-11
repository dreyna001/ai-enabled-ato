"""Focused tests for the PostgreSQL persistence foundation."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import json
import os
import re
import stat
import warnings
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, Uuid as UuidType
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from ato_service.db import enums as db_enums
from ato_service.db.base import Base
from ato_service.db.models import (
    AnalysisRun,
    AuditEvent,
    FactProposal,
    IdempotencyRecord,
    Job,
    JobAttempt,
    PackageRevision,
    RunStep,
    SourceArtifact,
    System,
)
from ato_service.db.dsn import (
    CREDENTIALS_DIRECTORY_ENV_VAR,
    DATABASE_DSN_FILE_ENV_VAR,
    DatabaseDsnError,
    read_database_dsn_from_file,
    require_database_dsn_from_env,
    resolve_database_dsn_from_credential_reference,
)
from ato_service.db.session import (
    DatabaseConfigurationError,
    create_async_engine_from_url,
    create_session_factory,
    require_postgresql_url,
)
from ato_service.lifecycle_transitions import AnalysisRunStatus, PackageRevisionStatus
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity

import ato_service.db.models  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
INITIAL_MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "20260710_0001_initial_p1_domain.py"
)
ENV_PATH = ROOT / "migrations" / "env.py"
ALEMBIC_INI_PATH = ROOT / "alembic.ini"

INITIAL_MIGRATION_TABLES = frozenset(
    {
        "systems",
        "package_revisions",
        "source_artifacts",
        "fact_proposals",
        "analysis_runs",
        "run_steps",
        "idempotency_records",
        "audit_events",
    }
)

EXPECTED_TABLES = INITIAL_MIGRATION_TABLES | frozenset({"jobs", "job_attempts"})

FK_SAFE_UPGRADE_TABLE_ORDER = (
    "systems",
    "package_revisions",
    "source_artifacts",
    "analysis_runs",
    "run_steps",
    "fact_proposals",
    "idempotency_records",
    "audit_events",
)

POSTGRES_URL = "postgresql+asyncpg://ato:secret@localhost:5432/ato_test"
SECRET_POSTGRES_URL = "postgresql+asyncpg://ato:supersecret@localhost:5432/ato"


def _posix_file_stat_result(
    *,
    uid: int = 0,
    mode: int = 0o100640,
) -> os.stat_result:
    return os.stat_result((mode, 0, 0, 0, uid, 0, 0, 0, 0, 0))


def _enable_posix_metadata_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ato_service.db.dsn.os.getuid", lambda: 0, raising=False)


def _install_lstat_mock(
    monkeypatch: pytest.MonkeyPatch,
    stat_result: os.stat_result,
) -> None:
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: stat_result,
    )


def _migration_source() -> str:
    return INITIAL_MIGRATION_PATH.read_text(encoding="utf-8")


def _load_initial_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "initial_p1_domain_migration",
        INITIAL_MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_op_sequence(function_name: str) -> list[tuple[str, tuple[object, ...]]]:
    module = _load_initial_migration_module()
    mock_op = MagicMock()
    module.op = mock_op
    getattr(module, function_name)()
    return [(call[0], call.args) for call in mock_op.method_calls]


def _require_database_dsn(monkeypatch: pytest.MonkeyPatch, env_value: str | None) -> str:
    if env_value is None:
        monkeypatch.delenv(DATABASE_DSN_FILE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, env_value)

    return require_database_dsn_from_env()


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
    names: set[str] = set()
    for constraint in table.constraints:
        if constraint.name:
            names.add(constraint.name)
    return names


def test_metadata_exposes_expected_tables_only() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_metadata_has_no_tenant_columns() -> None:
    for table in Base.metadata.tables.values():
        assert "tenant_id" not in table.c


def test_model_classes_map_to_expected_tables() -> None:
    assert System.__tablename__ == "systems"
    assert PackageRevision.__tablename__ == "package_revisions"
    assert SourceArtifact.__tablename__ == "source_artifacts"
    assert FactProposal.__tablename__ == "fact_proposals"
    assert AnalysisRun.__tablename__ == "analysis_runs"
    assert RunStep.__tablename__ == "run_steps"
    assert IdempotencyRecord.__tablename__ == "idempotency_records"
    assert AuditEvent.__tablename__ == "audit_events"
    assert Job.__tablename__ == "jobs"
    assert JobAttempt.__tablename__ == "job_attempts"


def test_primary_keys_use_uuid_columns() -> None:
    for table_name in EXPECTED_TABLES:
        pk_columns = _table(table_name).primary_key.columns
        assert len(pk_columns) == 1
        assert isinstance(pk_columns[0].type, UuidType)


def test_package_revision_columns_match_contract() -> None:
    columns = {column.name for column in _table("package_revisions").c}
    assert columns == {
        "package_revision_id",
        "system_id",
        "parent_revision_id",
        "profile_id",
        "certification_class",
        "impact_level",
        "data_origin",
        "sensitivity",
        "effective_data_labels",
        "authority_manifest_id",
        "content_manifest_sha256",
        "revision_version",
        "status",
        "created_by",
        "created_at",
    }


def test_run_steps_has_unique_run_id_step_key_constraint() -> None:
    table = _table("run_steps")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_run_steps_run_id_step_key" in unique


def test_idempotency_records_has_principal_operation_key_unique_constraint() -> None:
    table = _table("idempotency_records")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_idempotency_records_principal_operation_key" in unique


def test_idempotency_records_has_response_headers_jsonb_column() -> None:
    column = _table("idempotency_records").c.response_headers
    assert not column.nullable
    assert column.server_default is not None


def test_source_artifacts_has_revision_sha256_unique_constraint() -> None:
    table = _table("source_artifacts")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_source_artifacts_revision_sha256" in unique


def test_foreign_keys_cover_expected_relationships() -> None:
    foreign_keys = {
        (
            constraint.parent.name,
            constraint.column_keys[0],
            list(constraint.elements)[0].target_fullname,
        )
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (
        "package_revisions",
        "system_id",
        "systems.system_id",
    ) in foreign_keys
    assert (
        "analysis_runs",
        "package_revision_id",
        "package_revisions.package_revision_id",
    ) in foreign_keys
    assert (
        "run_steps",
        "run_id",
        "analysis_runs.run_id",
    ) in foreign_keys
    assert (
        "jobs",
        "run_id",
        "analysis_runs.run_id",
    ) in foreign_keys
    assert (
        "fact_proposals",
        "model_step_id",
        "run_steps.step_id",
    ) in foreign_keys


def test_job_mappers_configure_without_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        configure_mappers()

    mapper_warnings = [
        warning
        for warning in caught
        if "Job" in str(warning.message) or "JobAttempt" in str(warning.message)
    ]
    assert mapper_warnings == []

    assert Job.analysis_run.property.back_populates == "jobs"
    assert Job.attempts.property.back_populates == "job"
    assert JobAttempt.job.property.back_populates == "attempts"
    assert {
        column.key for column in JobAttempt.job.property._user_defined_foreign_keys
    } == {"job_id", "run_id", "step_key"}


def test_postgresql_ddl_compiles_for_all_tables() -> None:
    dialect = postgresql.dialect()
    for table_name in sorted(EXPECTED_TABLES):
        ddl = _compile_create_table(table_name)
        assert f"CREATE TABLE {table_name}" in ddl
        assert "TIMESTAMP WITH TIME ZONE" in ddl or table_name == "source_artifacts"
        for index in _table(table_name).indexes:
            index_sql = str(CreateIndex(index).compile(dialect=dialect))
            assert index_sql.startswith("CREATE INDEX") or index_sql.startswith(
                "CREATE UNIQUE INDEX"
            )


def test_package_revision_status_check_syncs_with_lifecycle_transitions() -> None:
    assert set(db_enums.PACKAGE_REVISION_STATUS_VALUES) == {
        status.value for status in PackageRevisionStatus
    }
    assert set(db_enums.PACKAGE_REVISION_STATUS_VALUES) == _domain_enum_values(
        "PackageRevision",
        "status",
    )
    ddl = _compile_create_table("package_revisions")
    for value in db_enums.PACKAGE_REVISION_STATUS_VALUES:
        assert f"'{value}'" in ddl


def test_analysis_run_status_check_syncs_with_lifecycle_transitions() -> None:
    assert set(db_enums.ANALYSIS_RUN_STATUS_VALUES) == {
        status.value for status in AnalysisRunStatus
    }
    assert set(db_enums.ANALYSIS_RUN_STATUS_VALUES) == _domain_enum_values(
        "AnalysisRun",
        "status",
    )


def test_analysis_run_policy_blocked_requires_zero_llm_calls_in_model() -> None:
    table = _table("analysis_runs")
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }

    assert "ck_analysis_runs_policy_blocked_requires_zero_llm_calls" in checks
    assert (
        checks["ck_analysis_runs_policy_blocked_requires_zero_llm_calls"]
        == "status <> 'policy_blocked' OR llm_call_count = 0"
    )
    assert "ck_analysis_runs_llm_call_count_range" in checks
    assert (
        checks["ck_analysis_runs_llm_call_count_range"]
        == "llm_call_count >= 0 AND llm_call_count <= 120"
    )


def test_analysis_run_policy_blocked_requires_zero_llm_calls_in_migration() -> None:
    migration_source = _migration_source()
    assert (
        "status <> 'policy_blocked' OR llm_call_count = 0"
        in migration_source
    )
    assert (
        "ck_analysis_runs_policy_blocked_requires_zero_llm_calls"
        in migration_source
    )
    assert (
        "llm_call_count >= 0 AND llm_call_count <= 120"
        in migration_source
    )


def test_routing_label_checks_sync_with_model_routing() -> None:
    assert set(db_enums.DATA_ORIGIN_VALUES) == {origin.value for origin in DataOrigin}
    assert set(db_enums.SENSITIVITY_VALUES) == {
        sensitivity.value for sensitivity in Sensitivity
    }
    assert set(db_enums.ENDPOINT_PROFILE_VALUES) == {
        profile.value for profile in EndpointProfile
    }

    package_ddl = _compile_create_table("package_revisions")
    for value in db_enums.DATA_ORIGIN_VALUES:
        assert f"'{value}'" in package_ddl
    for value in db_enums.SENSITIVITY_VALUES:
        assert f"'{value}'" in package_ddl

    run_step_ddl = _compile_create_table("run_steps")
    for value in db_enums.ENDPOINT_PROFILE_VALUES:
        assert f"'{value}'" in run_step_ddl


def test_package_revision_content_manifest_digest_nullable_with_ready_guard() -> None:
    table = _table("package_revisions")
    digest_column = table.c.content_manifest_sha256
    assert digest_column.nullable is True

    ddl = _compile_create_table("package_revisions")
    assert (
        "content_manifest_sha256 IS NULL OR content_manifest_sha256 ~ '^[a-f0-9]{64}$'"
        in ddl
    )
    assert (
        "status <> 'ready' OR content_manifest_sha256 IS NOT NULL"
        in ddl
    )
    assert (
        "ck_package_revisions_ready_requires_content_manifest_sha256"
        in _constraint_names("package_revisions")
    )


def test_sha256_check_constraints_present_on_hash_columns() -> None:
    hash_tables = {
        "package_revisions": {"content_manifest_sha256"},
        "source_artifacts": {"sha256"},
        "analysis_runs": {
            "analysis_profile_sha256",
            "config_fingerprint",
            "prompt_bundle_sha256",
            "artifact_manifest_sha256",
        },
        "run_steps": {"prompt_sha256", "fact_bundle_sha256", "response_sha256"},
        "fact_proposals": {"source_sha256"},
        "idempotency_records": {"request_digest"},
        "audit_events": {"previous_event_hash", "event_hash"},
    }
    for table_name, columns in hash_tables.items():
        checks = {
            constraint
            for constraint in _table(table_name).constraints
            if isinstance(constraint, CheckConstraint)
        }
        ddl = _compile_create_table(table_name)
        for column_name in columns:
            assert any(column_name in str(check.sqltext) for check in checks)
            assert f"{column_name} ~ '^[a-f0-9]{{64}}$'" in ddl or (
                f"{column_name} IS NULL OR {column_name} ~ '^[a-f0-9]{{64}}$'" in ddl
            )


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///tmp/ato.db",
        "mysql+asyncmy://user:pass@localhost/ato",
        "",
        "   ",
    ],
)
def test_require_postgresql_url_rejects_non_postgresql_schemes(url: str) -> None:
    with pytest.raises(DatabaseConfigurationError):
        require_postgresql_url(url)


@pytest.mark.parametrize(
    "url",
    [
        POSTGRES_URL,
        "postgresql://ato:secret@localhost:5432/ato_test",
        "postgres+asyncpg://ato:secret@localhost:5432/ato_test",
    ],
)
def test_require_postgresql_url_accepts_postgresql_schemes(url: str) -> None:
    assert require_postgresql_url(url) == url.strip()


def test_create_session_factory_does_not_connect() -> None:
    engine = create_async_engine_from_url(POSTGRES_URL)
    session_factory = create_session_factory(engine)
    assert session_factory.kw.get("expire_on_commit") is False
    assert engine.pool._pre_ping is True
    assert engine.url.render_as_string(hide_password=False) == POSTGRES_URL


def test_alembic_head_is_idempotency_headers_artifact_uniq_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260711_0004"


def test_initial_migration_references_only_original_domain_tables() -> None:
    migration_source = _migration_source()
    for table_name in sorted(INITIAL_MIGRATION_TABLES):
        assert table_name in migration_source
    assert "job_attempts" not in migration_source
    assert not re.search(
        r'op\.create_table\(\s*\n?\s*["\']jobs["\']',
        migration_source,
    )


def test_initial_migration_is_explicit_and_immutable() -> None:
    migration_source = _migration_source()

    for table_name in INITIAL_MIGRATION_TABLES:
        assert re.search(
            rf"op\.create_table\(\s*\n?\s*[\"']{table_name}[\"']",
            migration_source,
        )

    assert "create_all" not in migration_source
    assert "drop_all" not in migration_source
    assert "from ato_service.db.base import Base" not in migration_source
    assert "import ato_service.db.models" not in migration_source

    module = _load_initial_migration_module()
    assert module.revision == "20260710_0001"
    assert module.down_revision is None


def test_initial_migration_upgrade_and_downgrade_operation_order() -> None:
    upgrade_ops = _migration_op_sequence("upgrade")
    downgrade_ops = _migration_op_sequence("downgrade")

    upgrade_create_tables = [
        args[0]
        for op_name, args in upgrade_ops
        if op_name == "create_table"
    ]
    assert upgrade_create_tables == list(FK_SAFE_UPGRADE_TABLE_ORDER)

    downgrade_drop_tables = [
        args[0]
        for op_name, args in downgrade_ops
        if op_name == "drop_table"
    ]
    assert downgrade_drop_tables == list(reversed(FK_SAFE_UPGRADE_TABLE_ORDER))

    upgrade_indexes = [op_name for op_name, _ in upgrade_ops if op_name == "create_index"]
    assert len(upgrade_indexes) == 16
    assert all(op_name in {"drop_index", "drop_table"} for op_name, _ in downgrade_ops)


def test_alembic_ini_documents_database_dsn_file_not_url_env_var() -> None:
    ini_source = ALEMBIC_INI_PATH.read_text(encoding="utf-8")
    assert "ATO_DATABASE_DSN_FILE" in ini_source
    assert "ATO_DATABASE_URL" not in ini_source


def test_alembic_uses_current_path_separator_and_preserves_version_separator() -> None:
    config = Config(str(ALEMBIC_INI_PATH))

    assert config.get_main_option("path_separator") == "os"
    assert config.get_main_option("version_path_separator") == "os"


def test_alembic_env_requires_database_dsn_file_not_url_env_var() -> None:
    env_source = ENV_PATH.read_text(encoding="utf-8")
    assert "require_database_dsn_from_env" in env_source
    assert "ATO_DATABASE_URL" not in env_source


@pytest.mark.parametrize("failure_stage", ["connect", "migration"])
def test_alembic_async_engine_is_disposed_after_failure(failure_stage: str) -> None:
    env_tree = ast.parse(ENV_PATH.read_text(encoding="utf-8"))
    function_node = next(
        node
        for node in env_tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run_async_migrations"
    )

    connection = MagicMock()
    connection.run_sync = AsyncMock(
        side_effect=RuntimeError("migration failed")
        if failure_stage == "migration"
        else None
    )
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(
        side_effect=RuntimeError("connect failed")
        if failure_stage == "connect"
        else None,
        return_value=connection,
    )
    connection_context.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = connection_context
    engine.dispose = AsyncMock()

    alembic_config = MagicMock()
    alembic_config.config_ini_section = "alembic"
    alembic_config.get_section.return_value = {}

    namespace: dict[str, object] = {
        "async_engine_from_config": MagicMock(return_value=engine),
        "config": alembic_config,
        "do_run_migrations": MagicMock(),
        "pool": MagicMock(NullPool=object()),
        "require_database_dsn_from_env": MagicMock(return_value=POSTGRES_URL),
    }
    function_module = ast.Module(body=[function_node], type_ignores=[])
    exec(compile(function_module, str(ENV_PATH), "exec"), namespace)
    run_async_migrations = namespace["run_async_migrations"]
    assert callable(run_async_migrations)

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        asyncio.run(run_async_migrations())

    engine.dispose.assert_awaited_once_with()


def test_require_database_dsn_reads_absolute_utf8_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text("  postgresql+asyncpg://ato:secret@localhost:5432/ato\n", encoding="utf-8")
    monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, str(dsn_file.resolve()))

    assert (
        _require_database_dsn(monkeypatch, str(dsn_file.resolve()))
        == "postgresql+asyncpg://ato:secret@localhost:5432/ato"
    )


@pytest.mark.parametrize(
    "env_value",
    [
        None,
        "",
        "   ",
        "relative/path.dsn",
    ],
)
def test_require_database_dsn_rejects_missing_or_invalid_env(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
) -> None:
    with pytest.raises(DatabaseDsnError):
        _require_database_dsn(monkeypatch, env_value)


def test_require_database_dsn_rejects_empty_or_unreadable_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_file = tmp_path / "empty.dsn"
    empty_file.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, str(empty_file.resolve()))

    with pytest.raises(DatabaseDsnError, match="non-empty"):
        require_database_dsn_from_env()

    missing_file = tmp_path / "missing.dsn"
    monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, str(missing_file.resolve()))
    with pytest.raises(DatabaseDsnError, match="readable"):
        require_database_dsn_from_env()


@pytest.mark.parametrize(
    "dsn",
    [
        "sqlite+aiosqlite:///tmp/ato.db",
        "mysql+asyncmy://user:pass@localhost/ato",
    ],
)
def test_read_database_dsn_from_file_rejects_non_postgresql_schemes(
    tmp_path: Path,
    dsn: str,
) -> None:
    dsn_file = tmp_path / "bad.dsn"
    dsn_file.write_text(dsn, encoding="utf-8")

    with pytest.raises(DatabaseDsnError):
        read_database_dsn_from_file(dsn_file.resolve())


def test_resolve_database_dsn_from_root_owned_file_reference(
    tmp_path: Path,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(POSTGRES_URL, encoding="utf-8")

    resolved = resolve_database_dsn_from_credential_reference(
        {
            "source": "root_owned_file",
            "path": str(dsn_file.resolve()),
        }
    )

    assert resolved == POSTGRES_URL


def test_read_database_dsn_skips_metadata_enforcement_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(POSTGRES_URL, encoding="utf-8")
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(
        monkeypatch,
        _posix_file_stat_result(uid=1000, mode=0o100644),
    )

    assert read_database_dsn_from_file(dsn_file.resolve()) == POSTGRES_URL


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        (stat.S_IFLNK | 0o777, "not a symlink"),
        (stat.S_IFDIR | 0o755, "regular file"),
        (0o100644, "0640 or stricter"),
    ],
)
def test_read_database_dsn_rejects_insecure_root_owned_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    message: str,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(SECRET_POSTGRES_URL, encoding="utf-8")
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(monkeypatch, _posix_file_stat_result(mode=mode))

    with pytest.raises(DatabaseDsnError, match=message) as exc_info:
        read_database_dsn_from_file(
            dsn_file.resolve(),
            enforce_root_owned_file_metadata=True,
        )

    error_message = str(exc_info.value)
    assert "supersecret" not in error_message
    assert SECRET_POSTGRES_URL not in error_message


def test_read_database_dsn_rejects_lstat_failure_when_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(SECRET_POSTGRES_URL, encoding="utf-8")
    _enable_posix_metadata_checks(monkeypatch)

    def _raise_lstat_error(self: Path) -> os.stat_result:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "lstat", _raise_lstat_error)

    with pytest.raises(DatabaseDsnError, match="metadata could not be verified") as exc_info:
        read_database_dsn_from_file(
            dsn_file.resolve(),
            enforce_root_owned_file_metadata=True,
        )

    error_message = str(exc_info.value)
    assert "supersecret" not in error_message
    assert SECRET_POSTGRES_URL not in error_message


def test_read_database_dsn_rejects_non_root_owner_when_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(SECRET_POSTGRES_URL, encoding="utf-8")
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(
        monkeypatch,
        _posix_file_stat_result(uid=1000, mode=0o100640),
    )

    with pytest.raises(DatabaseDsnError, match="owned by root") as exc_info:
        read_database_dsn_from_file(
            dsn_file.resolve(),
            enforce_root_owned_file_metadata=True,
        )

    error_message = str(exc_info.value)
    assert "supersecret" not in error_message
    assert SECRET_POSTGRES_URL not in error_message


@pytest.mark.parametrize("mode", [0o100640, 0o100600, 0o100400])
def test_read_database_dsn_accepts_secure_root_owned_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(POSTGRES_URL, encoding="utf-8")
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(monkeypatch, _posix_file_stat_result(mode=mode))

    assert (
        read_database_dsn_from_file(
            dsn_file.resolve(),
            enforce_root_owned_file_metadata=True,
        )
        == POSTGRES_URL
    )


def test_resolve_database_dsn_enforces_metadata_only_for_root_owned_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(POSTGRES_URL, encoding="utf-8")
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(
        monkeypatch,
        _posix_file_stat_result(uid=1000, mode=0o100644),
    )

    with pytest.raises(DatabaseDsnError, match="owned by root"):
        resolve_database_dsn_from_credential_reference(
            {
                "source": "root_owned_file",
                "path": str(dsn_file.resolve()),
            },
            enforce_root_owned_file_metadata=True,
        )


def test_resolve_database_dsn_systemd_reference_skips_root_owned_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    credential_file = cred_dir / "database-dsn"
    credential_file.write_text(POSTGRES_URL, encoding="utf-8")
    monkeypatch.setenv(CREDENTIALS_DIRECTORY_ENV_VAR, str(cred_dir.resolve()))
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(
        monkeypatch,
        _posix_file_stat_result(uid=1000, mode=0o100644),
    )

    resolved = resolve_database_dsn_from_credential_reference(
        {
            "source": "systemd_credential",
            "identifier": "database-dsn",
        },
        enforce_root_owned_file_metadata=True,
    )

    assert resolved == POSTGRES_URL


def test_resolve_database_dsn_from_systemd_credential_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    credential_file = cred_dir / "database-dsn"
    credential_file.write_text(POSTGRES_URL, encoding="utf-8")
    monkeypatch.setenv(CREDENTIALS_DIRECTORY_ENV_VAR, str(cred_dir.resolve()))

    resolved = resolve_database_dsn_from_credential_reference(
        {
            "source": "systemd_credential",
            "identifier": "database-dsn",
        }
    )

    assert resolved == POSTGRES_URL


@pytest.mark.parametrize(
    "reference",
    [
        {"source": "inline", "value": POSTGRES_URL},
        {"source": "systemd_credential"},
        {"source": "root_owned_file"},
        {"source": "systemd_credential", "identifier": ""},
        {"source": "root_owned_file", "path": ""},
    ],
)
def test_resolve_database_dsn_rejects_malformed_credential_reference(
    reference: dict[str, object],
) -> None:
    with pytest.raises(DatabaseDsnError):
        resolve_database_dsn_from_credential_reference(reference)  # type: ignore[arg-type]


def test_database_dsn_errors_never_include_secret_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_dsn = "postgresql+asyncpg://ato:supersecret@localhost:5432/ato"
    dsn_file = tmp_path / "ato.dsn"
    dsn_file.write_text(secret_dsn, encoding="utf-8")
    monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, str((tmp_path / "missing.dsn").resolve()))

    with pytest.raises(DatabaseDsnError) as exc_info:
        require_database_dsn_from_env()

    message = str(exc_info.value)
    assert "supersecret" not in message
    assert secret_dsn not in message


def test_db_module_docstring_documents_jobs_persistence() -> None:
    from ato_service import db as db_module

    doc = inspect.getdoc(db_module) or ""
    assert "jobs" in doc.lower()
    assert "job_attempts" in doc.lower()
    assert "omitted" not in doc.lower()
    assert "unresolved" not in doc.lower()
    assert "deferred" not in doc.lower()


@pytest.mark.integration
def test_database_connectivity_probe_against_optional_test_database() -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    from ato_service.db.session import probe_database_connectivity

    async def _probe() -> None:
        engine = create_async_engine_from_url(url)
        try:
            await probe_database_connectivity(engine)
        finally:
            await engine.dispose()

    asyncio.run(_probe())
