"""Persistence and contract tests for package editor Diff 1 tables."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

from ato_service.db.base import Base
from ato_service.db.models import (
    PackageRevision,
    PackageRevisionDraft,
    SealedPackageContent,
)
from ato_service.domain_mapping import (
    map_package_revision_draft_to_domain,
    map_system_context_snapshot_to_domain,
)

import ato_service.db.models  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT / "migrations/versions/20260713_0007_package_editor_persistence.py"
)
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
PACKAGE_DRAFT_SCHEMA_PATH = (
    ROOT / "docs" / "contracts" / "package-draft-document.schema.json"
)
_FORMAT_CHECKER = FormatChecker()


def _table(name: str):
    return Base.metadata.tables[name]


def _compile_create_table(table_name: str) -> str:
    return str(
        CreateTable(_table(table_name)).compile(dialect=postgresql.dialect())
    )


def _schema_registry() -> Registry:
    resources: list[tuple[str, Resource]] = []
    for schema_path in (
        DOMAIN_SCHEMA_PATH,
        PACKAGE_DRAFT_SCHEMA_PATH,
    ):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema_id = schema.get("$id")
        if isinstance(schema_id, str) and schema_id:
            resources.append((schema_id, Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _validator_for(def_name: str) -> Draft202012Validator:
    schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    object_schema = {
        **schema["$defs"][def_name],
        "$defs": schema["$defs"],
    }
    if "$id" not in object_schema:
        object_schema["$id"] = schema["$id"]
    return Draft202012Validator(
        object_schema,
        registry=_schema_registry(),
        format_checker=_FORMAT_CHECKER,
    )


def _package_draft_validator() -> Draft202012Validator:
    schema = json.loads(PACKAGE_DRAFT_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def test_alembic_head_is_package_editor_persistence_migration() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260717_0012"


def test_migration_declares_package_editor_tables_and_columns() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'op.create_table(\n        "system_context_snapshots"' in source
    assert 'op.create_table(\n        "package_revision_drafts"' in source
    assert 'op.create_table(\n        "sealed_package_contents"' in source
    assert "package_content_sha256" in source
    assert "system_context_snapshot_id" in source
    assert "uq_system_context_snapshots_system_id_version" in source
    assert "ck_package_revision_drafts_document_object" in source
    assert "ck_sealed_package_contents_field_provenance_object" in source
    assert "ck_package_revisions_ready_requires_content_manifest_sha256" not in source


def test_system_context_snapshot_version_uniqueness_constraint() -> None:
    table = _table("system_context_snapshots")
    unique = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_system_context_snapshots_system_id_version" in unique


def test_package_revision_draft_enforces_one_row_per_revision() -> None:
    table = _table("package_revision_drafts")
    pk_columns = {column.name for column in table.primary_key.columns}
    assert pk_columns == {"package_revision_id"}
    foreign_keys = {
        (
            constraint.parent.name,
            constraint.column_keys[0],
            list(constraint.elements)[0].target_fullname,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (
        "package_revision_drafts",
        "package_revision_id",
        "package_revisions.package_revision_id",
    ) in foreign_keys


def test_sealed_package_content_uses_revision_primary_key() -> None:
    table = _table("sealed_package_contents")
    pk_columns = {column.name for column in table.primary_key.columns}
    assert pk_columns == {"package_revision_id"}


def test_jsonb_object_checks_present_on_editor_tables() -> None:
    for table_name, constraint_name in (
        ("system_context_snapshots", "ck_system_context_snapshots_document_object"),
        ("package_revision_drafts", "ck_package_revision_drafts_document_object"),
        (
            "package_revision_drafts",
            "ck_package_revision_drafts_field_provenance_object",
        ),
        ("sealed_package_contents", "ck_sealed_package_contents_document_object"),
        (
            "sealed_package_contents",
            "ck_sealed_package_contents_field_provenance_object",
        ),
    ):
        ddl = _compile_create_table(table_name)
        assert constraint_name in ddl
        assert "jsonb_typeof" in ddl


def test_package_revision_does_not_require_sealed_content_for_ready() -> None:
    table = _table("package_revisions")
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert "ck_package_revisions_ready_requires_content_manifest_sha256" in checks
    assert "ready_requires_package_content" not in "".join(checks.values()).lower()
    assert "package_content_sha256 IS NOT NULL" not in "".join(checks.values())


def test_package_editor_mappers_configure_without_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        configure_mappers()

    relevant = [
        warning
        for warning in caught
        if any(
            name in str(warning.message)
            for name in (
                "SystemContextSnapshot",
                "PackageRevisionDraft",
                "SealedPackageContent",
                "PackageRevision",
            )
        )
    ]
    assert relevant == []

    assert PackageRevision.draft.property.back_populates == "package_revision"
    assert PackageRevision.sealed_content.property.back_populates == "package_revision"
    assert (
        PackageRevision.system_context_snapshot.property.back_populates
        == "package_revisions"
    )
    assert PackageRevisionDraft.package_revision.property.back_populates == "draft"
    assert SealedPackageContent.package_revision.property.back_populates == "sealed_content"


@pytest.mark.parametrize(
    ("fixture_name", "def_name", "mapper"),
    [
        (
            "domain.valid.system-context-snapshot.json",
            "SystemContextSnapshot",
            map_system_context_snapshot_to_domain,
        ),
        (
            "domain.valid.package-revision-draft.json",
            "PackageRevisionDraft",
            map_package_revision_draft_to_domain,
        ),
    ],
)
def test_domain_mappings_validate_against_schema(
    fixture_name: str,
    def_name: str,
    mapper,
) -> None:
    fixture = json.loads(
        (ROOT / "docs/contracts/fixtures" / fixture_name).read_text(encoding="utf-8")
    )

    class _Row:
        pass

    row = _Row()
    for key, value in fixture.items():
        if key in {"schema_version", "object_type"}:
            continue
        if key.endswith("_id") and value is not None and key != "document_schema_version":
            from uuid import UUID

            setattr(row, key, UUID(value))
            continue
        if key in {"created_at", "updated_at", "sealed_at"}:
            from datetime import datetime

            setattr(
                row,
                key,
                datetime.fromisoformat(value.replace("Z", "+00:00")),
            )
            continue
        setattr(row, key, value)

    payload = mapper(row)
    assert payload == fixture
    _validator_for(def_name).validate(payload)


def test_package_draft_document_fixtures_follow_schema() -> None:
    validator = _package_draft_validator()
    valid = json.loads(
        (
            ROOT
            / "docs/contracts/fixtures/package-draft-document.valid.fisma-minimal.json"
        ).read_text(encoding="utf-8")
    )
    invalid = json.loads(
        (
            ROOT
            / "docs/contracts/fixtures/"
            "package-draft-document.invalid.fedramp20x-null-section.json"
        ).read_text(encoding="utf-8")
    )
    assert not list(validator.iter_errors(valid))
    assert list(validator.iter_errors(invalid))


def test_package_draft_profile_sections_are_mutually_exclusive() -> None:
    validator = _package_draft_validator()
    document = json.loads(
        (
            ROOT
            / "docs/contracts/fixtures/package-draft-document.valid.fisma-minimal.json"
        ).read_text(encoding="utf-8")
    )
    document["package"]["profile_id"] = "fedramp_20x_program"
    document["system"]["impact_level"] = None
    document["fedramp_20x"] = {
        "cpo": {},
        "sdr": {},
        "ocr": {},
        "scg": {},
        "ksi_methods": [],
        "metric_history": [],
        "independent_assessment": {},
    }

    assert list(validator.iter_errors(document))

    document["fisma_agency_security"] = None
    assert not list(validator.iter_errors(document))

    document["system"]["impact_level"] = "moderate"
    assert list(validator.iter_errors(document))
