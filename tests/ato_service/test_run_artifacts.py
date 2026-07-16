"""Run artifact listing from durable artifact manifests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ato_service.artifact_manifests import write_artifact_manifest, write_run_output_file
from ato_service.db.models import AnalysisRun, PackageRevision, System
from ato_service.lifecycle_transitions import IllegalStateTransitionError
from ato_service.pagination import InvalidPaginationCursorError
from ato_service.run_artifacts import (
    RunArtifactDigestMismatchError,
    RunArtifactManifestMissingError,
    artifact_id_for_generated_path,
    list_run_artifacts,
    map_manifest_file_to_descriptor,
    media_type_for_generated_path,
    paginate_manifest_files,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _write_manifest(tmp_path: Path) -> str:
    alpha = write_run_output_file(
        storage_root=tmp_path,
        run_id=str(RUN_ID).lower(),
        relative_path="human/summary.md",
        payload=b"# summary",
    )
    beta = write_run_output_file(
        storage_root=tmp_path,
        run_id=str(RUN_ID).lower(),
        relative_path="machine/matrix.json",
        payload=b'{"rows":[]}',
    )
    manifest = write_artifact_manifest(
        run_id=str(RUN_ID).lower(),
        package_revision_id=str(REVISION_ID).lower(),
        authority_manifest_id="authority.v2",
        analysis_profile_sha256="a" * 64,
        config_fingerprint="b" * 64,
        prompt_bundle_sha256="c" * 64,
        completed_at=NOW,
        generated_files=[beta, alpha],
        storage_root=tmp_path,
        project_root=ROOT,
    )
    return manifest.sha256


def _system() -> System:
    return System(
        system_id=SYSTEM_ID,
        display_name="System",
        external_system_id=None,
        owner_group="owners",
        viewer_groups=["owners"],
        created_at=NOW,
    )


def _revision() -> PackageRevision:
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=SYSTEM_ID,
        parent_revision_id=None,
        profile_id="fedramp_20x_program",
        data_origin="synthetic",
        sensitivity="internal_unclassified",
        effective_data_labels=["internal_unclassified", "synthetic"],
        authority_manifest_id="authority.v2",
        content_manifest_sha256="a" * 64,
        package_content_sha256="b" * 64,
        revision_version=1,
        status="ready",
        created_by="tester",
        created_at=NOW,
    )


def _analysis_run(*, status: str, manifest_sha256: str | None) -> AnalysisRun:
    return AnalysisRun(
        run_id=RUN_ID,
        package_revision_id=REVISION_ID,
        parent_run_id=None,
        run_type="deterministic_only",
        status=status,
        requested_by="tester",
        requested_at=NOW,
        started_at=NOW,
        completed_at=NOW if status == "succeeded" else None,
        authority_manifest_id="authority.v2",
        analysis_profile_sha256="a" * 64,
        config_fingerprint="b" * 64,
        prompt_bundle_sha256="c" * 64,
        model_profile="deterministic",
        artifact_manifest_sha256=manifest_sha256,
        llm_call_count=0,
        assessment_item_ids=[],
        error_code=None,
        error_retryable=None,
    )


def _principal() -> Any:
    from ato_service.auth_context import AuthenticatedPrincipal

    return AuthenticatedPrincipal(
        actor_id="tester",
        groups=("owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


class _FakeResult:
    def __init__(self, row: tuple[Any, Any, Any] | None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[Any, Any, Any] | None:
        return self._row


class _FakeSession:
    def __init__(self, row: tuple[Any, Any, Any] | None) -> None:
        self._row = row
        self.execute = AsyncMock(return_value=_FakeResult(row))


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def test_descriptor_helpers_map_manifest_metadata() -> None:
    descriptor = map_manifest_file_to_descriptor(
        {
            "path": "machine/matrix.json",
            "sha256": "d" * 64,
            "size_bytes": 12,
        }
    )
    assert descriptor["artifact_id"] == "matrix"
    assert descriptor["media_type"] == "application/json"
    assert descriptor["official_schema_id"] is None
    assert artifact_id_for_generated_path("human/summary.md") == "summary"
    assert media_type_for_generated_path("human/summary.md") == "text/markdown"


def test_paginate_manifest_files_is_deterministic_and_cursor_stable() -> None:
    files = [
        {"path": "machine/matrix.json", "sha256": "a" * 64, "size_bytes": 2},
        {"path": "human/summary.md", "sha256": "b" * 64, "size_bytes": 3},
    ]
    first = paginate_manifest_files(files, cursor=None, limit=1)
    assert [item["path"] for item in first.items] == ["human/summary.md"]
    assert first.next_cursor is not None

    second = paginate_manifest_files(files, cursor=first.next_cursor, limit=1)
    assert [item["path"] for item in second.items] == ["machine/matrix.json"]
    assert second.next_cursor is None


def test_list_run_artifacts_reads_durable_manifest(tmp_path: Path) -> None:
    manifest_sha256 = _write_manifest(tmp_path)
    session = _FakeSession((_analysis_run(status="succeeded", manifest_sha256=manifest_sha256), _revision(), _system()))

    page = _run(
        list_run_artifacts(
            session,
            principal=_principal(),
            run_id=RUN_ID,
            cursor=None,
            limit=None,
            storage_root=tmp_path,
            project_root=ROOT,
        )
    )

    assert [item["path"] for item in page.items] == [
        "human/summary.md",
        "machine/matrix.json",
    ]
    assert page.items[1]["artifact_id"] == "matrix"
    assert page.next_cursor is None


def test_list_run_artifacts_rejects_non_succeeded_run(tmp_path: Path) -> None:
    session = _FakeSession((_analysis_run(status="running", manifest_sha256=None), _revision(), _system()))

    with pytest.raises(IllegalStateTransitionError):
        _run(
            list_run_artifacts(
                session,
                principal=_principal(),
                run_id=RUN_ID,
                cursor=None,
                limit=None,
                storage_root=tmp_path,
                project_root=ROOT,
            )
        )


def test_list_run_artifacts_reports_missing_manifest(tmp_path: Path) -> None:
    session = _FakeSession((_analysis_run(status="succeeded", manifest_sha256="f" * 64), _revision(), _system()))

    with pytest.raises(RunArtifactManifestMissingError):
        _run(
            list_run_artifacts(
                session,
                principal=_principal(),
                run_id=RUN_ID,
                cursor=None,
                limit=None,
                storage_root=tmp_path,
                project_root=ROOT,
            )
        )


def test_list_run_artifacts_reports_digest_mismatch(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    session = _FakeSession((_analysis_run(status="succeeded", manifest_sha256="f" * 64), _revision(), _system()))

    with pytest.raises(RunArtifactDigestMismatchError):
        _run(
            list_run_artifacts(
                session,
                principal=_principal(),
                run_id=RUN_ID,
                cursor=None,
                limit=None,
                storage_root=tmp_path,
                project_root=ROOT,
            )
        )


def test_malformed_cursor_raises_invalid_pagination_error() -> None:
    with pytest.raises(InvalidPaginationCursorError):
        paginate_manifest_files([], cursor="not-a-cursor", limit=10)
