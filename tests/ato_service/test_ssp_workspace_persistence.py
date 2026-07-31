"""Persistence structure and transition tests for SSP workspaces."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import ato_service.db.models  # noqa: F401
from ato_service.db.base import Base
from ato_service.ssp_workspace.contracts import (
    FactContent,
    Provenance,
    RevisionContent,
    revision_content_sha256,
)
from ato_service.ssp_workspace.persistence import (
    IllegalWorkspaceStateError,
    RevisionIntegrityError,
    StaleWorkspaceRevisionError,
    approve_current_revision,
    archive_workspace,
    save_revision,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "migrations/versions/20260727_0014_ssp_workspace_foundation.py"
)


def _session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    return session


def test_migration_is_single_head_and_declares_workspace_tables() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_current_head() == "20260728_0016"
    source = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "ssp_profile_versions",
        "ssp_workspaces",
        "ssp_workspace_revisions",
        "ssp_evidence_artifacts",
        "ssp_system_facts",
        "ssp_sections",
        "ssp_control_statements",
        "ssp_questions",
        "ssp_evidence_links",
        "ssp_agent_patches",
        "ssp_approval_snapshots",
    ):
        assert f'"{table}"' in source


def test_direct_evidence_has_storage_and_extraction_contract() -> None:
    table = Base.metadata.tables["ssp_evidence_artifacts"]
    assert {
        "storage_key",
        "size_bytes",
        "detected_format",
        "extracted_segments",
        "removed_at",
        "removed_by",
    } <= set(table.c.keys())
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "ck_ssp_evidence_artifacts_storage_key_matches_sha256" in ddl
    assert "ck_ssp_evidence_artifacts_status_fields" in ddl
    assert "ck_ssp_evidence_artifacts_segments_array" in ddl


def test_workspace_current_revision_has_same_workspace_foreign_key() -> None:
    table = Base.metadata.tables["ssp_workspaces"]
    foreign_keys = {
        constraint.name: tuple(constraint.column_keys)
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["fk_ssp_workspaces_current_revision_workspace"] == (
        "current_revision_id",
        "workspace_id",
    )


def test_archived_workspace_cannot_transition_again(monkeypatch) -> None:
    session = _session()
    workspace = SimpleNamespace(status="archived", archived_at=datetime.now(UTC))
    monkeypatch.setattr(
        "ato_service.ssp_workspace.persistence._load_workspace_for_update",
        AsyncMock(return_value=workspace),
    )

    with pytest.raises(IllegalWorkspaceStateError):
        asyncio.run(
            archive_workspace(
                session,
                workspace_id=uuid.uuid4(),
                now=datetime.now(UTC),
            )
        )

    session.flush.assert_not_awaited()


def test_stale_save_fails_before_any_write(monkeypatch) -> None:
    session = _session()
    workspace = SimpleNamespace(
        status="working",
        current_revision_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.persistence._load_workspace_for_update",
        AsyncMock(return_value=workspace),
    )

    with pytest.raises(StaleWorkspaceRevisionError):
        asyncio.run(
            save_revision(
                session,
                workspace_id=uuid.uuid4(),
                content=RevisionContent(),
                created_by="isso@example.gov",
                now=datetime.now(UTC),
                expected_revision_id=uuid.uuid4(),
            )
        )

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


def test_approval_snapshot_binds_to_verified_revision_hash(monkeypatch) -> None:
    session = _session()
    workspace_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.name",
                value="Atlas",
                provenance=Provenance.ISSO_ENTERED,
            ),
        )
    )
    workspace = SimpleNamespace(
        status="working",
        current_revision_id=revision_id,
    )
    revision = SimpleNamespace(
        revision_id=revision_id,
        workspace_id=workspace_id,
        status="working",
        content=content.model_dump(mode="json"),
        content_sha256=revision_content_sha256(content),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = revision
    session.execute.return_value = result
    monkeypatch.setattr(
        "ato_service.ssp_workspace.persistence._load_workspace_for_update",
        AsyncMock(return_value=workspace),
    )

    approval = asyncio.run(
        approve_current_revision(
            session,
            workspace_id=workspace_id,
            revision_id=revision_id,
            approved_by="isso@example.gov",
            now=datetime.now(UTC),
        )
    )

    assert approval.revision_id == revision_id
    assert approval.revision_sha256 == revision.content_sha256
    assert revision.status == "approved"
    session.add.assert_called_once_with(approval)
    session.flush.assert_awaited_once()


def test_tampered_revision_is_not_approved(monkeypatch) -> None:
    session = _session()
    workspace_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    content = RevisionContent()
    workspace = SimpleNamespace(status="working", current_revision_id=revision_id)
    revision = SimpleNamespace(
        revision_id=revision_id,
        workspace_id=workspace_id,
        status="working",
        content=content.model_dump(mode="json"),
        content_sha256="0" * 64,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = revision
    session.execute.return_value = result
    monkeypatch.setattr(
        "ato_service.ssp_workspace.persistence._load_workspace_for_update",
        AsyncMock(return_value=workspace),
    )

    with pytest.raises(RevisionIntegrityError):
        asyncio.run(
            approve_current_revision(
                session,
                workspace_id=workspace_id,
                revision_id=revision_id,
                approved_by="isso@example.gov",
                now=datetime.now(UTC),
            )
        )

    assert revision.status == "working"
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
