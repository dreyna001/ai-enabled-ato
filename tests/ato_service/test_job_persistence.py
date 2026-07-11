"""Focused tests for Job and JobAttempt PostgreSQL persistence."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, Uuid as UuidType
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import ato_service.db.models  # noqa: F401

from ato_service.db import enums as db_enums
from ato_service.db.base import Base
from ato_service.db.models import Job, JobAttempt

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
JOBS_MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "20260711_0002_jobs_and_job_attempts.py"
)
INITIAL_MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "20260710_0001_initial_p1_domain.py"
)

JOB_TABLES = frozenset({"jobs", "job_attempts"})

JOB_COLUMNS = frozenset(
    {
        "job_id",
        "run_id",
        "step_key",
        "step_idempotent",
        "status",
        "attempt_count",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "last_error_code",
    }
)

JOB_ATTEMPT_COLUMNS = frozenset(
    {
        "attempt_id",
        "job_id",
        "run_id",
        "step_key",
        "attempt_number",
        "status",
        "lease_owner",
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


def test_metadata_includes_jobs_and_job_attempts() -> None:
    assert JOB_TABLES <= set(Base.metadata.tables)


def test_model_classes_map_to_expected_tables() -> None:
    assert Job.__tablename__ == "jobs"
    assert JobAttempt.__tablename__ == "job_attempts"


def test_primary_keys_use_uuid_columns() -> None:
    for table_name in JOB_TABLES:
        pk_columns = _table(table_name).primary_key.columns
        assert len(pk_columns) == 1
        assert isinstance(pk_columns[0].type, UuidType)


def test_job_columns_match_contract() -> None:
    assert {column.name for column in _table("jobs").c} == JOB_COLUMNS


def test_job_attempt_columns_match_contract() -> None:
    assert {column.name for column in _table("job_attempts").c} == JOB_ATTEMPT_COLUMNS


def test_jobs_has_unique_run_id_step_key_constraint() -> None:
    table = _table("jobs")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_jobs_run_id_step_key" in unique


def test_jobs_has_parent_alignment_unique_constraint() -> None:
    table = _table("jobs")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_jobs_job_id_run_id_step_key" in unique


def test_job_attempts_has_unique_job_id_attempt_number_constraint() -> None:
    table = _table("job_attempts")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_job_attempts_job_id_attempt_number" in unique


def test_job_attempts_composite_foreign_key_aligns_with_parent_job() -> None:
    table = _table("job_attempts")
    composite_fks = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert len(composite_fks) == 1
    fk = composite_fks[0]
    assert fk.name == "fk_job_attempts_jobs_job_id_run_id_step_key"
    assert fk.column_keys == ["job_id", "run_id", "step_key"]
    assert list(fk.elements)[0].target_fullname == "jobs.job_id"
    assert list(fk.elements)[1].target_fullname == "jobs.run_id"
    assert list(fk.elements)[2].target_fullname == "jobs.step_key"


def test_jobs_foreign_key_targets_analysis_runs() -> None:
    table = _table("jobs")
    foreign_keys = {
        (
            constraint.name,
            constraint.column_keys[0],
            list(constraint.elements)[0].target_fullname,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (None, "run_id", "analysis_runs.run_id") in foreign_keys


def test_job_relationships_are_explicit() -> None:
    assert Job.analysis_run.property.back_populates == "jobs"
    assert Job.attempts.property.back_populates == "job"
    assert JobAttempt.job.property.back_populates == "attempts"


def test_job_status_check_syncs_with_domain_schema() -> None:
    assert set(db_enums.JOB_STATUS_VALUES) == _domain_enum_values("Job", "status")
    ddl = _compile_create_table("jobs")
    for value in db_enums.JOB_STATUS_VALUES:
        assert f"'{value}'" in ddl


def test_job_attempt_status_check_syncs_with_domain_schema() -> None:
    assert set(db_enums.JOB_ATTEMPT_STATUS_VALUES) == _domain_enum_values(
        "JobAttempt",
        "status",
    )
    ddl = _compile_create_table("job_attempts")
    for value in db_enums.JOB_ATTEMPT_STATUS_VALUES:
        assert f"'{value}'" in ddl


def test_job_lease_fields_match_status_in_model() -> None:
    table = _table("jobs")
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert "ck_jobs_lease_fields_match_status" in checks
    assert "status = 'leased'" in checks["ck_jobs_lease_fields_match_status"]
    assert "lease_owner IS NULL" in checks["ck_jobs_lease_fields_match_status"]


def test_job_attempt_status_fields_match_lifecycle_in_model() -> None:
    table = _table("job_attempts")
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert "ck_job_attempts_status_fields" in checks
    expression = checks["ck_job_attempts_status_fields"]
    assert "status = 'active'" in expression
    assert "status = 'succeeded'" in expression
    assert "status = 'failed'" in expression
    assert "error_retryable IS NOT NULL" in expression


def test_job_attempt_count_and_number_bounds() -> None:
    jobs_ddl = _compile_create_table("jobs")
    attempts_ddl = _compile_create_table("job_attempts")
    assert "attempt_count >= 0" in jobs_ddl
    assert "attempt_number >= 1" in attempts_ddl


def test_job_attempt_count_server_default_starts_at_zero() -> None:
    column = _table("jobs").c.attempt_count
    assert column.server_default is not None
    assert str(column.server_default.arg) == "0"

    ddl = _compile_create_table("jobs")
    assert "DEFAULT 0" in ddl.upper()

    migration_source = JOBS_MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'server_default="0"' in migration_source or "server_default='0'" in migration_source


def test_error_code_regex_constraints_present() -> None:
    jobs_ddl = _compile_create_table("jobs")
    attempts_ddl = _compile_create_table("job_attempts")
    assert (
        "last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{2,127}$'"
        in jobs_ddl
    )
    assert (
        "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{2,127}$'"
        in attempts_ddl
    )


def test_step_key_regex_constraints_present() -> None:
    for table_name in JOB_TABLES:
        ddl = _compile_create_table(table_name)
        assert "step_key ~ '^[a-z][a-z0-9_]{1,63}$'" in ddl


def test_job_indexes_support_claim_run_status_and_lease_expiry() -> None:
    table = _table("jobs")
    index_names = {index.name for index in table.indexes}
    assert index_names == {
        "ix_jobs_run_id",
        "ix_jobs_status",
        "ix_jobs_status_available_at",
        "ix_jobs_lease_expires_at",
    }

    dialect = postgresql.dialect()
    for index in table.indexes:
        index_sql = str(CreateIndex(index).compile(dialect=dialect))
        assert index_sql.startswith("CREATE INDEX")


def test_job_attempt_indexes_include_lookup_and_one_active_invariant() -> None:
    table = _table("job_attempts")
    index_names = {index.name for index in table.indexes}
    assert index_names == {
        "ix_job_attempts_job_id",
        "uq_job_attempts_one_active_per_job",
    }


def test_job_attempt_one_active_per_job_partial_unique_index_ddl() -> None:
    table = _table("job_attempts")
    partial_index = next(
        index
        for index in table.indexes
        if index.name == "uq_job_attempts_one_active_per_job"
    )
    assert partial_index.unique is True
    assert list(partial_index.columns.keys()) == ["job_id"]

    dialect = postgresql.dialect()
    index_sql = str(CreateIndex(partial_index).compile(dialect=dialect))
    assert "CREATE UNIQUE INDEX uq_job_attempts_one_active_per_job" in index_sql
    assert "ON job_attempts (job_id)" in index_sql
    assert "WHERE (status = 'active')" in index_sql or "WHERE status = 'active'" in index_sql


def test_postgresql_ddl_compiles_for_job_tables() -> None:
    for table_name in sorted(JOB_TABLES):
        ddl = _compile_create_table(table_name)
        assert f"CREATE TABLE {table_name}" in ddl
        assert "TIMESTAMP WITH TIME ZONE" in ddl


def test_alembic_head_is_jobs_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260711_0002"


def test_jobs_migration_chains_from_initial_migration() -> None:
    module = _load_migration_module(JOBS_MIGRATION_PATH)
    assert module.revision == "20260711_0002"
    assert module.down_revision == "20260710_0001"


def test_jobs_migration_is_explicit_and_additive() -> None:
    migration_source = JOBS_MIGRATION_PATH.read_text(encoding="utf-8")
    initial_source = INITIAL_MIGRATION_PATH.read_text(encoding="utf-8")

    for table_name in JOB_TABLES:
        assert re.search(
            rf"op\.create_table\(\s*\n?\s*[\"']{table_name}[\"']",
            migration_source,
        )

    assert "create_all" not in migration_source
    assert "drop_all" not in migration_source
    assert "from ato_service.db.base import Base" not in migration_source
    assert "import ato_service.db.models" not in migration_source
    assert "jobs" not in initial_source or "intentionally omitted" in initial_source


def test_jobs_migration_upgrade_and_downgrade_operation_order() -> None:
    upgrade_ops = _migration_op_sequence(JOBS_MIGRATION_PATH, "upgrade")
    downgrade_ops = _migration_op_sequence(JOBS_MIGRATION_PATH, "downgrade")

    upgrade_create_tables = [
        args[0]
        for op_name, args, _kwargs in upgrade_ops
        if op_name == "create_table"
    ]
    assert upgrade_create_tables == ["jobs", "job_attempts"]

    downgrade_drop_tables = [
        args[0]
        for op_name, args, _kwargs in downgrade_ops
        if op_name == "drop_table"
    ]
    assert downgrade_drop_tables == ["job_attempts", "jobs"]

    upgrade_indexes = [
        op_name for op_name, _args, _kwargs in upgrade_ops if op_name == "create_index"
    ]
    assert upgrade_indexes == ["create_index"] * 6

    downgrade_index_names = [
        args[0]
        for op_name, args, kwargs in downgrade_ops
        if op_name == "drop_index"
    ]
    assert downgrade_index_names == [
        "uq_job_attempts_one_active_per_job",
        "ix_job_attempts_job_id",
        "ix_jobs_lease_expires_at",
        "ix_jobs_status_available_at",
        "ix_jobs_status",
        "ix_jobs_run_id",
    ]

    downgrade_index_tables = [
        kwargs["table_name"]
        for op_name, _args, kwargs in downgrade_ops
        if op_name == "drop_index"
    ]
    assert downgrade_index_tables == [
        "job_attempts",
        "job_attempts",
        "jobs",
        "jobs",
        "jobs",
        "jobs",
    ]


def test_jobs_migration_constraints_match_models() -> None:
    migration_source = JOBS_MIGRATION_PATH.read_text(encoding="utf-8")
    for constraint_name in (
        "uq_jobs_run_id_step_key",
        "uq_jobs_job_id_run_id_step_key",
        "uq_job_attempts_job_id_attempt_number",
        "uq_job_attempts_one_active_per_job",
        "fk_job_attempts_jobs_job_id_run_id_step_key",
        "ck_jobs_lease_fields_match_status",
        "ck_jobs_attempt_count_non_negative",
        "ck_jobs_last_error_code",
        "ck_job_attempts_status_fields",
        "ck_job_attempts_attempt_number_positive",
        "ck_job_attempts_error_code",
        "ck_job_attempts_lease_owner_min_length",
    ):
        assert constraint_name in migration_source

    for table_name in JOB_TABLES:
        for constraint_name in _constraint_names(table_name):
            assert constraint_name in migration_source
