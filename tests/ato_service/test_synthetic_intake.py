"""Tests for the bounded dev-only synthetic JSON intake worker."""

from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.blobs import BlobStore
from ato_service.db.models import PackageRevision, PackageRevisionDraft, SourceArtifact, System
from ato_service.lifecycle_transitions import PackageRevisionStatus
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.source_artifacts import SourceArtifactStorageError
from ato_service.synthetic_intake import (
    SyntheticIntakeConfigurationError,
    SyntheticIntakeInvariantError,
    _eligible_revision_statement,
    process_next_synthetic_extraction,
    process_next_synthetic_intake,
    process_next_synthetic_scan,
    require_synthetic_intake_runtime,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 11, 17, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ARTIFACT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")


class _RecordingSession:
    def __init__(self, execute_results: list[Any]) -> None:
        self._execute_results = list(execute_results)
        self.execute_calls: list[Any] = []
        self.added: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.execute_calls.append(statement)
        if not self._execute_results:
            raise AssertionError("unexpected execute call")
        return self._execute_results.pop(0)

    def add(self, value: Any) -> None:
        self.added.append(value)


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _scalar_optional(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalar(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _scalars(values: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _compile_sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _system() -> System:
    return System(
        system_id=SYSTEM_ID,
        display_name="Synthetic System",
        external_system_id=None,
        owner_group="owners",
        viewer_groups=[],
        created_at=NOW,
        archived_at=None,
    )


def _revision(*, status: str, version: int) -> PackageRevision:
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=SYSTEM_ID,
        parent_revision_id=None,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="synthetic",
        sensitivity="internal_unclassified",
        effective_data_labels=["internal_unclassified", "synthetic"],
        authority_manifest_id="authority.v2",
        content_manifest_sha256="a" * 64,
        revision_version=version,
        status=status,
        created_by="owner@example.test",
        created_at=NOW,
    )


def _artifact(
    blob_store: BlobStore,
    content: bytes,
    *,
    artifact_id: uuid.UUID = ARTIFACT_ID,
    scan_status: str,
) -> SourceArtifact:
    stored = blob_store.store_stream(io.BytesIO(content), max_bytes=1024 * 1024)
    return SourceArtifact(
        artifact_id=artifact_id,
        package_revision_id=REVISION_ID,
        display_filename=f"{artifact_id}.json",
        storage_key=stored.storage_key,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        declared_media_type="application/json",
        detected_media_type="application/json",
        artifact_kind="manifest",
        malware_scan_status=scan_status,
        extraction_status="pending",
        source_date=None,
        uploaded_at=NOW,
    )


def test_claim_query_is_skip_locked_synthetic_and_json_only() -> None:
    sql = _compile_sql(_eligible_revision_statement(PackageRevisionStatus.SCANNING))
    assert "package_revisions.status = 'scanning'" in sql
    assert "package_revisions.data_origin = 'synthetic'" in sql
    assert "source_artifacts.declared_media_type != 'application/json'" in sql
    assert "source_artifacts.detected_media_type != 'application/json'" in sql
    assert "ORDER BY package_revisions.created_at ASC" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_runtime_gate_accepts_only_dev_local(tmp_path: Path) -> None:
    dev_config = load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/data",
        },
        base_dir=tmp_path,
    )
    require_synthetic_intake_runtime(dev_config)

    production_config = MagicMock(runtime_profile="onprem_production")
    with pytest.raises(SyntheticIntakeConfigurationError):
        require_synthetic_intake_runtime(production_config)


@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_scan_marks_artifacts_clean_and_advances_one_transition(
    mock_audit: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_audit.return_value = MagicMock()
    revision = _revision(status="scanning", version=2)
    artifact = _artifact(
        BlobStore(tmp_path),
        b'{"system_name":"Synthetic FISMA"}',
        scan_status="pending",
    )
    session = _RecordingSession(
        [_scalar_optional(revision), _scalars([artifact])]
    )

    result = _run(
        process_next_synthetic_scan(
            session,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is not None
    assert result.previous_status == "scanning"
    assert result.status == "extracting"
    assert result.revision_version == 3
    assert result.artifact_count == 1
    assert revision.status == "extracting"
    assert revision.revision_version == 3
    assert artifact.malware_scan_status == "clean"
    assert artifact.extraction_status == "pending"
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["actor_type"] == "service"
    assert mock_audit.await_args.kwargs["outcome"] == "succeeded"


@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_extraction_creates_schema_valid_draft_without_fact_proposals(
    mock_audit: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_audit.return_value = MagicMock()
    revision = _revision(status="extracting", version=3)
    artifact = _artifact(
        BlobStore(tmp_path),
        (
            b'{"package":{"title":"Synthetic FISMA Package","profile_id":"fisma_agency_security"},'
            b'"system":{"name":"Synthetic FISMA","description":"Demo system"},'
            b'"security_controls":{"AC-1":{"implementation_status":"implemented",'
            b'"summary":"Access control policy reviewed annually."}}}'
        ),
        scan_status="clean",
    )
    session = _RecordingSession(
        [
            _scalar_optional(revision),
            _scalar(False),
            _scalar_optional(_system()),
            _scalars([artifact]),
        ]
    )

    result = _run(
        process_next_synthetic_extraction(
            session,
            blob_store=BlobStore(tmp_path),
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is not None
    assert result.previous_status == "extracting"
    assert result.status == "awaiting_confirmation"
    assert result.revision_version == 4
    assert result.proposal_count == 0
    assert result.draft_inserted is True
    assert revision.status == "awaiting_confirmation"
    assert artifact.extraction_status == "succeeded"

    drafts = [item for item in session.added if isinstance(item, PackageRevisionDraft)]
    assert len(drafts) == 1
    assert drafts[0].document["system"]["display_name"] == "Synthetic FISMA"
    assert drafts[0].document["security_controls"]["AC-1"]["implementation_statement"].startswith(
        "Access control"
    )
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["metadata"]["segment_count"] >= 1


@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_invalid_json_transitions_to_invalid_without_partial_proposals(
    mock_audit: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_audit.return_value = MagicMock()
    revision = _revision(status="extracting", version=3)
    artifact = _artifact(
        BlobStore(tmp_path),
        b'{"broken":',
        scan_status="clean",
    )
    session = _RecordingSession(
        [
            _scalar_optional(revision),
            _scalar(False),
            _scalar_optional(_system()),
            _scalars([artifact]),
        ]
    )

    result = _run(
        process_next_synthetic_extraction(
            session,
            blob_store=BlobStore(tmp_path),
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is not None
    assert result.status == "invalid"
    assert result.proposal_count == 0
    assert revision.status == "invalid"
    assert revision.revision_version == 4
    assert artifact.extraction_status == "failed"
    assert session.added == []
    assert mock_audit.await_args.kwargs["outcome"] == "succeeded"
    assert mock_audit.await_args.kwargs["reason_code"] == "source_parse_failed"


@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_duplicate_canonical_pointers_invalidate_without_partial_drafts(
    mock_audit: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_audit.return_value = MagicMock()
    revision = _revision(status="extracting", version=3)
    blob_store = BlobStore(tmp_path)
    first = _artifact(
        blob_store,
        b'{"system":{"name":"first"}}',
        scan_status="clean",
    )
    second = _artifact(
        blob_store,
        b'{"system":{"name":"second"}}',
        artifact_id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
        scan_status="clean",
    )
    session = _RecordingSession(
        [
            _scalar_optional(revision),
            _scalar(False),
            _scalar_optional(_system()),
            _scalars([first, second]),
        ]
    )

    result = _run(
        process_next_synthetic_extraction(
            session,
            blob_store=blob_store,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is not None
    assert result.status == "invalid"
    assert first.extraction_status == "failed"
    assert second.extraction_status == "failed"
    assert session.added == []
    assert mock_audit.await_args.kwargs["reason_code"] == "duplicate_canonical_id"


@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_corrupt_blob_transitions_to_invalid_as_source_type_mismatch(
    mock_audit: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_audit.return_value = MagicMock()
    revision = _revision(status="extracting", version=3)
    blob_store = BlobStore(tmp_path)
    artifact = _artifact(
        blob_store,
        b'{"name":"good"}',
        scan_status="clean",
    )
    prefix, digest = artifact.storage_key.split("/", 1)
    (tmp_path / "blobs" / prefix / digest).write_bytes(
        b"x" * artifact.size_bytes
    )
    session = _RecordingSession(
        [
            _scalar_optional(revision),
            _scalar(False),
            _scalar_optional(_system()),
            _scalars([artifact]),
        ]
    )

    result = _run(
        process_next_synthetic_extraction(
            session,
            blob_store=blob_store,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is not None
    assert result.status == "invalid"
    assert artifact.extraction_status == "failed"
    assert session.added == []
    assert mock_audit.await_args.kwargs["reason_code"] == "source_type_mismatch"


@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_missing_blob_fails_visibly_without_changing_extraction_state(
    mock_audit: AsyncMock,
    tmp_path: Path,
) -> None:
    revision = _revision(status="extracting", version=3)
    blob_store = BlobStore(tmp_path)
    artifact = _artifact(
        blob_store,
        b'{"name":"good"}',
        scan_status="clean",
    )
    prefix, digest = artifact.storage_key.split("/", 1)
    (tmp_path / "blobs" / prefix / digest).unlink()
    session = _RecordingSession(
        [
            _scalar_optional(revision),
            _scalar(False),
            _scalar_optional(_system()),
            _scalars([artifact]),
        ]
    )

    with pytest.raises(SourceArtifactStorageError):
        _run(
            process_next_synthetic_extraction(
                session,
                blob_store=blob_store,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert revision.status == "extracting"
    assert revision.revision_version == 3
    assert artifact.extraction_status == "pending"
    assert session.added == []
    mock_audit.assert_not_awaited()


@pytest.mark.parametrize("content", [b"{}", b"[]"])
@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_json_without_addressable_fact_transitions_to_invalid(
    mock_audit: AsyncMock,
    content: bytes,
    tmp_path: Path,
) -> None:
    mock_audit.return_value = MagicMock()
    revision = _revision(status="extracting", version=3)
    blob_store = BlobStore(tmp_path)
    artifact = _artifact(blob_store, content, scan_status="clean")
    session = _RecordingSession(
        [
            _scalar_optional(revision),
            _scalar(False),
            _scalar_optional(_system()),
            _scalars([artifact]),
        ]
    )

    result = _run(
        process_next_synthetic_extraction(
            session,
            blob_store=blob_store,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is not None
    assert result.status == "invalid"
    assert mock_audit.await_args.kwargs["reason_code"] == "source_parse_failed"


@patch("ato_service.synthetic_intake.append_audit_event", new_callable=AsyncMock)
def test_defense_in_depth_rejects_non_synthetic_claim_without_mutation(
    mock_audit: AsyncMock,
    tmp_path: Path,
) -> None:
    revision = _revision(status="scanning", version=2)
    revision.data_origin = "customer_production"
    artifact = _artifact(
        BlobStore(tmp_path),
        b'{"name":"customer"}',
        scan_status="pending",
    )
    session = _RecordingSession(
        [_scalar_optional(revision), _scalars([artifact])]
    )

    with pytest.raises(SyntheticIntakeInvariantError):
        _run(
            process_next_synthetic_scan(
                session,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert revision.status == "scanning"
    assert revision.revision_version == 2
    assert artifact.malware_scan_status == "pending"
    mock_audit.assert_not_awaited()


def test_extraction_refuses_existing_draft_for_replay_safety(
    tmp_path: Path,
) -> None:
    revision = _revision(status="extracting", version=3)
    session = _RecordingSession(
        [
            _scalar_optional(revision),
            _scalar(True),
        ]
    )

    with pytest.raises(SyntheticIntakeInvariantError, match="draft"):
        _run(
            process_next_synthetic_extraction(
                session,
                blob_store=BlobStore(tmp_path),
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )
    assert revision.status == "extracting"
    assert revision.revision_version == 3
    assert session.added == []


@patch("ato_service.synthetic_intake.process_next_synthetic_scan", new_callable=AsyncMock)
@patch(
    "ato_service.synthetic_intake.process_next_synthetic_extraction",
    new_callable=AsyncMock,
)
def test_process_next_finishes_extraction_before_claiming_new_scan(
    mock_extract: AsyncMock,
    mock_scan: AsyncMock,
    tmp_path: Path,
) -> None:
    expected = MagicMock()
    mock_extract.return_value = expected
    session = MagicMock()

    result = _run(
        process_next_synthetic_intake(
            session,
            blob_store=BlobStore(tmp_path),
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is expected
    mock_extract.assert_awaited_once()
    mock_scan.assert_not_awaited()


@patch("ato_service.synthetic_intake.process_next_synthetic_scan", new_callable=AsyncMock)
@patch(
    "ato_service.synthetic_intake.process_next_synthetic_extraction",
    new_callable=AsyncMock,
)
def test_process_next_claims_scan_when_no_extraction_is_available(
    mock_extract: AsyncMock,
    mock_scan: AsyncMock,
    tmp_path: Path,
) -> None:
    expected = MagicMock()
    mock_extract.return_value = None
    mock_scan.return_value = expected
    session = MagicMock()

    result = _run(
        process_next_synthetic_intake(
            session,
            blob_store=BlobStore(tmp_path),
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result is expected
    mock_extract.assert_awaited_once()
    mock_scan.assert_awaited_once()
