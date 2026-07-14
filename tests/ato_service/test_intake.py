"""Focused tests for unified intake orchestration (Component A Diff 3)."""

from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.blobs import BlobStore
from ato_service.db.models import (
    PackageRevision,
    PackageRevisionDraft,
    PackageRevisionIntakeAttempt,
    PackageRevisionIntakeWork,
    SourceArtifact,
    System,
)
from ato_service.intake import (
    ArtifactSnapshot,
    ClaimedIntakeOperation,
    IntakeOutcomeKind,
    IntakeRevisionSnapshot,
    _ScanComputeOutcome,
    _commit_clean_scan,
    _compute_scan_outcome,
    _persist_scan_outcome,
    build_intake_lease_owner,
    process_next_intake_operation,
    require_intake_runtime,
    resolve_intake_allowed_data_origins,
)
from ato_service.intake_work import (
    IntakeAttemptStatus,
    IntakeWorkPhase,
    IntakeWorkStatus,
    assert_intake_claim_live,
)
from ato_service.malware_scan import (
    DevLocalIntegrityScanSubstitute,
    MalwareScanOutcome,
    MalwareScanResult,
    MalwareScannerUnavailableError,
)
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.synthetic_intake_worker import run_synthetic_intake_worker

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
JSON_ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
DOCX_ARTIFACT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
FENCE_TOKEN = uuid.UUID("55555555-5555-4555-8555-555555555555")
ROOT = Path(__file__).resolve().parents[2]
FISMA_FIXTURE_PATH = (
    ROOT / "data/synthetic-packages/fisma-demo-portal/agency-security-plan-excerpt.json"
)


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _config(tmp_path: Path):
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/data",
        },
        base_dir=tmp_path,
    )


def _build_docx(*paragraphs: str) -> bytes:
    body = "".join(
        f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
        for text in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode("utf-8")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", document_xml)
        archive.writestr(
            "word/_rels/document.xml.rels",
            b'<?xml version="1.0"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Type="internal" Target="document.xml"/>'
            b"</Relationships>",
        )
    return buffer.getvalue()


def _artifact_snapshot(
    *,
    artifact_id: uuid.UUID,
    content: bytes,
    declared_media_type: str,
    detected_media_type: str,
    filename: str,
    malware_scan_status: str = "pending",
    extraction_status: str = "pending",
) -> ArtifactSnapshot:
    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    return ArtifactSnapshot(
        artifact_id=artifact_id,
        package_revision_id=REVISION_ID,
        display_filename=filename,
        storage_key=f"sha256/{digest[:2]}/{digest[2:4]}/{digest}",
        sha256=digest,
        size_bytes=len(content),
        declared_media_type=declared_media_type,
        detected_media_type=detected_media_type,
        artifact_kind="evidence_document",
        malware_scan_status=malware_scan_status,
        extraction_status=extraction_status,
    )


def _revision_snapshot(
    *,
    status: str = "scanning",
    revision_version: int = 2,
    artifacts: tuple[ArtifactSnapshot, ...],
    data_origin: str = "synthetic",
) -> IntakeRevisionSnapshot:
    return IntakeRevisionSnapshot(
        package_revision_id=REVISION_ID,
        revision_version=revision_version,
        status=status,
        profile_id="fisma_agency_security",
        impact_level="moderate",
        content_manifest_sha256="a" * 64,
        data_origin=data_origin,
        sensitivity="internal_unclassified",
        system_id=SYSTEM_ID,
        system_display_name="Intake Test System",
        artifacts=artifacts,
    )


def _claimed(*, work_phase: str, revision_version: int = 2) -> ClaimedIntakeOperation:
    return ClaimedIntakeOperation(
        package_revision_id=REVISION_ID,
        work_phase=work_phase,
        lease_owner="intake-worker",
        fence_token=FENCE_TOKEN,
        expected_revision_version=revision_version,
    )


def test_runtime_gate_accepts_dev_local(tmp_path: Path) -> None:
    require_intake_runtime(_config(tmp_path))


def test_runtime_gate_fails_closed_for_production() -> None:
    config = MagicMock(runtime_profile="onprem_production")
    with pytest.raises(MalwareScannerUnavailableError):
        require_intake_runtime(config)


@patch("ato_service.synthetic_intake_worker.create_async_engine_from_url")
def test_worker_rejects_production_before_creating_engine(
    mock_engine: MagicMock,
) -> None:
    config = MagicMock(runtime_profile="onprem_production")
    with pytest.raises(MalwareScannerUnavailableError):
        _run(
            run_synthetic_intake_worker(
                config,
                dsn="postgresql://example",
                audit_hmac_key=b"x" * 32,
            )
        )
    mock_engine.assert_not_called()


def test_resolve_allowed_data_origins_for_dev_local(tmp_path: Path) -> None:
    origins = resolve_intake_allowed_data_origins(_config(tmp_path))
    assert origins == frozenset(
        {"synthetic", "redacted_nonproduction", "customer_production"}
    )


def test_claim_query_origin_filter_is_present_in_intake_work_sql() -> None:
    from ato_service.intake_work import _claim_select_statement

    sql = str(
        _claim_select_statement(
            work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            now=NOW,
            max_attempts=3,
            allowed_data_origins=frozenset({"synthetic"}),
        ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "data_origin IN ('synthetic')" in sql


def test_compute_scan_mixed_json_and_docx_is_clean(tmp_path: Path) -> None:
    json_bytes = FISMA_FIXTURE_PATH.read_bytes()
    docx_bytes = _build_docx("Supplemental narrative evidence")
    blob_store = BlobStore(tmp_path)
    json_stored = blob_store.store_stream(io.BytesIO(json_bytes), max_bytes=1024 * 1024)
    docx_stored = blob_store.store_stream(io.BytesIO(docx_bytes), max_bytes=1024 * 1024)
    artifacts = (
        ArtifactSnapshot(
            artifact_id=JSON_ARTIFACT_ID,
            package_revision_id=REVISION_ID,
            display_filename="manifest.json",
            storage_key=json_stored.storage_key,
            sha256=json_stored.sha256,
            size_bytes=json_stored.size_bytes,
            declared_media_type="application/json",
            detected_media_type="application/json",
            artifact_kind="manifest",
            malware_scan_status="pending",
            extraction_status="pending",
        ),
        ArtifactSnapshot(
            artifact_id=DOCX_ARTIFACT_ID,
            package_revision_id=REVISION_ID,
            display_filename="narrative.docx",
            storage_key=docx_stored.storage_key,
            sha256=docx_stored.sha256,
            size_bytes=docx_stored.size_bytes,
            declared_media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            detected_media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            artifact_kind="evidence_document",
            malware_scan_status="pending",
            extraction_status="pending",
        ),
    )
    snapshot = _revision_snapshot(artifacts=artifacts)
    session_factory = MagicMock()
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)

    outcome = _run(
        _compute_scan_outcome(
            session_factory,
            snapshot=snapshot,
            blob_store=blob_store,
            scanner=DevLocalIntegrityScanSubstitute(),
            claimed=claimed,
            lease_owner="intake-worker",
            lease_seconds=300,
            now_factory=lambda: NOW,
        )
    )

    assert outcome.kind == "all_clean"
    assert len(outcome.artifact_results) == 2


def test_compute_scan_marks_storage_unavailable_retryable(tmp_path: Path) -> None:
    content = b'{"package":{"title":"x"}}'
    artifact = _artifact_snapshot(
        artifact_id=JSON_ARTIFACT_ID,
        content=content,
        declared_media_type="application/json",
        detected_media_type="application/json",
        filename="manifest.json",
    )
    snapshot = _revision_snapshot(artifacts=(artifact,))
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)

    class _MissingBlobScanner(DevLocalIntegrityScanSubstitute):
        def scan_stored_artifact(self, *, blob_store, artifact) -> MalwareScanResult:
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="storage_unavailable",
            )

    outcome = _run(
        _compute_scan_outcome(
            MagicMock(),
            snapshot=snapshot,
            blob_store=BlobStore(tmp_path),
            scanner=_MissingBlobScanner(),
            claimed=claimed,
            lease_owner="intake-worker",
            lease_seconds=300,
            now_factory=lambda: NOW,
        )
    )

    assert outcome.kind == "retryable"
    assert outcome.reason_code == "storage_unavailable"


def test_no_open_transaction_during_scan_io(tmp_path: Path) -> None:
    content = FISMA_FIXTURE_PATH.read_bytes()
    blob_store = BlobStore(tmp_path)
    stored = blob_store.store_stream(io.BytesIO(content), max_bytes=1024 * 1024)
    artifact = _artifact_snapshot(
        artifact_id=JSON_ARTIFACT_ID,
        content=content,
        declared_media_type="application/json",
        detected_media_type="application/json",
        filename="manifest.json",
    )
    artifact = ArtifactSnapshot(
        artifact_id=artifact.artifact_id,
        package_revision_id=artifact.package_revision_id,
        display_filename=artifact.display_filename,
        storage_key=stored.storage_key,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        declared_media_type=artifact.declared_media_type,
        detected_media_type=artifact.detected_media_type,
        artifact_kind=artifact.artifact_kind,
        malware_scan_status=artifact.malware_scan_status,
        extraction_status=artifact.extraction_status,
    )
    snapshot = _revision_snapshot(artifacts=(artifact,))
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)
    open_sessions = {"count": 0}

    @asynccontextmanager
    async def tracking_scope(_factory: Any) -> AsyncIterator[MagicMock]:
        open_sessions["count"] += 1
        try:
            yield MagicMock()
        finally:
            open_sessions["count"] -= 1

    original_to_thread = asyncio.to_thread

    async def inspect_to_thread(func, /, *args, **kwargs):
        assert open_sessions["count"] == 0
        return await original_to_thread(func, *args, **kwargs)

    with patch("ato_service.intake.session_scope", side_effect=tracking_scope):
        with patch("ato_service.intake.asyncio.to_thread", side_effect=inspect_to_thread):
            outcome = _run(
                _compute_scan_outcome(
                    MagicMock(),
                    snapshot=snapshot,
                    blob_store=blob_store,
                    scanner=DevLocalIntegrityScanSubstitute(),
            claimed=claimed,
            lease_owner="intake-worker",
            lease_seconds=300,
            now_factory=lambda: NOW,
        )
    )

    assert outcome.kind == "all_clean"


def test_process_next_prefers_extract_before_scan(tmp_path: Path) -> None:
    config = _config(tmp_path)
    claim_calls: list[str] = []

    async def fake_claim(session_factory, *, work_phase, **kwargs):
        claim_calls.append(work_phase)
        if work_phase == IntakeWorkPhase.DETERMINISTIC_EXTRACT.value:
            return _claimed(work_phase=work_phase, revision_version=3)
        return None

    with (
        patch("ato_service.intake._claim_operation", side_effect=fake_claim),
        patch(
            "ato_service.intake._process_claimed_operation",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                package_revision_id=REVISION_ID,
                work_phase=IntakeWorkPhase.DETERMINISTIC_EXTRACT.value,
                outcome=IntakeOutcomeKind.COMPLETED,
            ),
        ),
    ):
        result = _run(
            process_next_intake_operation(
                MagicMock(),
                config=config,
                blob_store=BlobStore(tmp_path),
                hmac_key=HMAC_KEY,
                scanner=DevLocalIntegrityScanSubstitute(),
                now_factory=lambda: NOW,
            )
        )

    assert result is not None
    assert claim_calls == [IntakeWorkPhase.DETERMINISTIC_EXTRACT.value]


def test_stale_fence_discards_scan_results(tmp_path: Path) -> None:
    from ato_service.intake_work import IntakeLeaseLostError

    snapshot = _revision_snapshot(
        artifacts=(
            _artifact_snapshot(
                artifact_id=JSON_ARTIFACT_ID,
                content=b"{}",
                declared_media_type="application/json",
                detected_media_type="application/json",
                filename="manifest.json",
            ),
        )
    )
    compute = _ScanComputeOutcome(kind="all_clean", artifact_results=())
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)

    @asynccontextmanager
    async def failing_scope(_factory: Any) -> AsyncIterator[MagicMock]:
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=MagicMock()), scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
        with patch(
            "ato_service.intake._load_locked_intake_state",
            side_effect=IntakeLeaseLostError(
                package_revision_id=REVISION_ID,
                work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
            ),
        ):
            yield session

    with patch("ato_service.intake.session_scope", side_effect=failing_scope):
        result = _run(
            _persist_scan_outcome(
                MagicMock(),
                claimed=claimed,
                snapshot=snapshot,
                compute=compute,
                hmac_key=HMAC_KEY,
                lease_owner="intake-worker",
                now_factory=lambda: NOW,
                max_attempts=3,
            )
        )

    assert result.outcome == IntakeOutcomeKind.DISCARDED_STALE


def test_compute_extract_invalid_json_returns_invalid_content(tmp_path: Path) -> None:
    from ato_service.intake import _compute_extract_outcome

    corrupt = b"not-json"
    blob_store = BlobStore(tmp_path)
    stored = blob_store.store_stream(io.BytesIO(corrupt), max_bytes=1024)
    artifact = ArtifactSnapshot(
        artifact_id=JSON_ARTIFACT_ID,
        package_revision_id=REVISION_ID,
        display_filename="manifest.json",
        storage_key=stored.storage_key,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        declared_media_type="application/json",
        detected_media_type="application/json",
        artifact_kind="manifest",
        malware_scan_status="clean",
        extraction_status="pending",
    )
    snapshot = _revision_snapshot(
        status="extracting",
        revision_version=3,
        artifacts=(artifact,),
    )
    claimed = _claimed(
        work_phase=IntakeWorkPhase.DETERMINISTIC_EXTRACT.value,
        revision_version=3,
    )
    outcome = _run(
        _compute_extract_outcome(
            MagicMock(),
            snapshot=snapshot,
            config=_config(tmp_path),
            blob_store=blob_store,
            claimed=claimed,
            lease_owner="intake-worker",
            lease_seconds=300,
            now_factory=lambda: NOW,
        )
    )
    assert outcome.kind == "invalid_content"
    assert outcome.reason_code == "source_parse_failed"


class _ErrorScanner:
    def __init__(self, *, reason_code: str | None) -> None:
        self._reason_code = reason_code

    def scan_stored_artifact(self, *, blob_store: BlobStore, artifact: object) -> MalwareScanResult:
        return MalwareScanResult(
            MalwareScanOutcome.ERROR,
            reason_code=self._reason_code,
        )


def test_compute_scan_malware_scan_unavailable_is_retryable(tmp_path: Path) -> None:
    json_bytes = b"{}"
    blob_store = BlobStore(tmp_path)
    json_stored = blob_store.store_stream(io.BytesIO(json_bytes), max_bytes=1024 * 1024)
    snapshot = _revision_snapshot(
        artifacts=(
            ArtifactSnapshot(
                artifact_id=JSON_ARTIFACT_ID,
                package_revision_id=REVISION_ID,
                display_filename="manifest.json",
                storage_key=json_stored.storage_key,
                sha256=json_stored.sha256,
                size_bytes=json_stored.size_bytes,
                declared_media_type="application/json",
                detected_media_type="application/json",
                artifact_kind="manifest",
                malware_scan_status="pending",
                extraction_status="pending",
            ),
        )
    )
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)
    outcome = _run(
        _compute_scan_outcome(
            MagicMock(),
            snapshot=snapshot,
            blob_store=blob_store,
            scanner=_ErrorScanner(reason_code="malware_scan_unavailable"),
            claimed=claimed,
            lease_owner="intake-worker",
            lease_seconds=300,
            now_factory=lambda: NOW,
        )
    )
    assert outcome.kind == "retryable"
    assert outcome.reason_code == "malware_scan_unavailable"


def test_compute_scan_error_without_reason_defaults_to_malware_scan_failed(
    tmp_path: Path,
) -> None:
    json_bytes = b"{}"
    blob_store = BlobStore(tmp_path)
    json_stored = blob_store.store_stream(io.BytesIO(json_bytes), max_bytes=1024 * 1024)
    snapshot = _revision_snapshot(
        artifacts=(
            ArtifactSnapshot(
                artifact_id=JSON_ARTIFACT_ID,
                package_revision_id=REVISION_ID,
                display_filename="manifest.json",
                storage_key=json_stored.storage_key,
                sha256=json_stored.sha256,
                size_bytes=json_stored.size_bytes,
                declared_media_type="application/json",
                detected_media_type="application/json",
                artifact_kind="manifest",
                malware_scan_status="pending",
                extraction_status="pending",
            ),
        )
    )
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)
    outcome = _run(
        _compute_scan_outcome(
            MagicMock(),
            snapshot=snapshot,
            blob_store=blob_store,
            scanner=_ErrorScanner(reason_code=None),
            claimed=claimed,
            lease_owner="intake-worker",
            lease_seconds=300,
            now_factory=lambda: NOW,
        )
    )
    assert outcome.kind == "retryable"
    assert outcome.reason_code == "malware_scan_failed"


def test_compute_scan_source_type_mismatch_is_invalid_not_retryable(
    tmp_path: Path,
) -> None:
    class _MismatchScanner:
        def scan_stored_artifact(self, *, blob_store: BlobStore, artifact: object) -> MalwareScanResult:
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="source_type_mismatch",
            )

    json_bytes = b"{}"
    blob_store = BlobStore(tmp_path)
    json_stored = blob_store.store_stream(io.BytesIO(json_bytes), max_bytes=1024 * 1024)
    snapshot = _revision_snapshot(
        artifacts=(
            ArtifactSnapshot(
                artifact_id=JSON_ARTIFACT_ID,
                package_revision_id=REVISION_ID,
                display_filename="manifest.json",
                storage_key=json_stored.storage_key,
                sha256=json_stored.sha256,
                size_bytes=json_stored.size_bytes,
                declared_media_type="application/json",
                detected_media_type="application/json",
                artifact_kind="manifest",
                malware_scan_status="pending",
                extraction_status="pending",
            ),
        )
    )
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)
    outcome = _run(
        _compute_scan_outcome(
            MagicMock(),
            snapshot=snapshot,
            blob_store=blob_store,
            scanner=_MismatchScanner(),
            claimed=claimed,
            lease_owner="intake-worker",
            lease_seconds=300,
            now_factory=lambda: NOW,
        )
    )
    assert outcome.kind == "invalid_content"
    assert outcome.reason_code == "source_type_mismatch"


def test_build_intake_lease_owner_is_bounded_and_injectable() -> None:
    owner = build_intake_lease_owner(token="test-token")
    assert owner.startswith("intake-")
    assert "test-token" in owner
    assert len(owner) <= 255


def test_heartbeat_uses_fresh_clock_values(tmp_path: Path) -> None:
    times = [NOW + timedelta(seconds=90)]
    clock_calls = {"index": 0}

    def scripted_clock() -> datetime:
        value = times[clock_calls["index"]]
        clock_calls["index"] += 1
        return value

    heartbeat_now_values: list[datetime] = []

    async def capture_heartbeat(
        *_args: object,
        now_factory: Callable[[], datetime],
        **_kwargs: object,
    ) -> None:
        heartbeat_now_values.append(now_factory())

    json_bytes = FISMA_FIXTURE_PATH.read_bytes()
    docx_bytes = _build_docx("one")
    docx2 = _build_docx("two")
    docx3 = _build_docx("three")
    blob_store = BlobStore(tmp_path)
    artifact_specs = (
        (json_bytes, JSON_ARTIFACT_ID, "manifest.json", "application/json", "manifest"),
        (
            docx_bytes,
            DOCX_ARTIFACT_ID,
            "a.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "evidence_document",
        ),
        (
            docx2,
            uuid.uuid5(DOCX_ARTIFACT_ID, "two"),
            "b.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "evidence_document",
        ),
        (
            docx3,
            uuid.uuid5(DOCX_ARTIFACT_ID, "three"),
            "c.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "evidence_document",
        ),
    )
    artifacts: list[ArtifactSnapshot] = []
    for content, artifact_id, filename, media, kind in artifact_specs:
        stored = blob_store.store_stream(io.BytesIO(content), max_bytes=1024 * 1024)
        artifacts.append(
            ArtifactSnapshot(
                artifact_id=artifact_id,
                package_revision_id=REVISION_ID,
                display_filename=filename,
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                declared_media_type=media,
                detected_media_type=media,
                artifact_kind=kind,
                malware_scan_status="pending",
                extraction_status="pending",
            )
        )
    snapshot = _revision_snapshot(artifacts=tuple(artifacts))
    claimed = _claimed(work_phase=IntakeWorkPhase.MALWARE_SCAN.value)

    with patch("ato_service.intake._heartbeat_claim", side_effect=capture_heartbeat):
        outcome = _run(
            _compute_scan_outcome(
                MagicMock(),
                snapshot=snapshot,
                blob_store=blob_store,
                scanner=DevLocalIntegrityScanSubstitute(),
                claimed=claimed,
                lease_owner="intake-worker",
                lease_seconds=300,
                now_factory=scripted_clock,
            )
        )

    assert outcome.kind == "all_clean"
    assert heartbeat_now_values == [NOW + timedelta(seconds=90)]


class _CommitRecordingSession:
    def __init__(self, execute_results: list[MagicMock]) -> None:
        self._execute_results = list(execute_results)
        self.added: list[object] = []

    async def execute(self, statement: object) -> MagicMock:
        if not self._execute_results:
            raise AssertionError("unexpected execute call")
        return self._execute_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _commit_scalar(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_db_revision(*, revision_version: int = 2) -> PackageRevision:
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=SYSTEM_ID,
        parent_revision_id=None,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="synthetic",
        sensitivity="cui",
        effective_data_labels=["cui"],
        authority_manifest_id="authority-manifest-1",
        content_manifest_sha256="a" * 64,
        package_content_sha256=None,
        system_context_snapshot_id=None,
        revision_version=revision_version,
        status="scanning",
        created_by="operator",
        created_at=NOW,
    )


def _make_db_work() -> PackageRevisionIntakeWork:
    return PackageRevisionIntakeWork(
        package_revision_id=REVISION_ID,
        work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
        status=IntakeWorkStatus.LEASED.value,
        attempt_count=1,
        available_at=NOW,
        lease_owner="intake-worker-a",
        lease_expires_at=NOW + timedelta(minutes=5),
        heartbeat_at=NOW,
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
        last_error_code=None,
    )


def _make_db_attempt() -> PackageRevisionIntakeAttempt:
    return PackageRevisionIntakeAttempt(
        attempt_id=uuid.UUID("66666666-6666-4666-8666-666666666666"),
        package_revision_id=REVISION_ID,
        work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
        attempt_number=1,
        status=IntakeAttemptStatus.ACTIVE.value,
        lease_owner="intake-worker-a",
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
        started_at=NOW,
        completed_at=None,
        error_code=None,
        error_retryable=None,
    )


def _make_pending_artifact() -> SourceArtifact:
    return SourceArtifact(
        artifact_id=JSON_ARTIFACT_ID,
        package_revision_id=REVISION_ID,
        display_filename="manifest.json",
        storage_key=f"sha256/ab/cd/{'a' * 64}",
        sha256="a" * 64,
        size_bytes=2,
        declared_media_type="application/json",
        detected_media_type="application/json",
        artifact_kind="manifest",
        malware_scan_status="pending",
        extraction_status="pending",
        source_date=None,
        uploaded_at=NOW,
    )


def test_commit_clean_scan_completes_at_expected_version_then_increments_once() -> None:
    work = _make_db_work()
    revision = _make_db_revision(revision_version=2)
    attempt = _make_db_attempt()
    artifact = _make_pending_artifact()
    claimed = ClaimedIntakeOperation(
        package_revision_id=REVISION_ID,
        work_phase=IntakeWorkPhase.MALWARE_SCAN.value,
        lease_owner="intake-worker-a",
        fence_token=FENCE_TOKEN,
        expected_revision_version=2,
    )
    snapshot = _revision_snapshot(
        revision_version=2,
        artifacts=(
            ArtifactSnapshot(
                artifact_id=artifact.artifact_id,
                package_revision_id=artifact.package_revision_id,
                display_filename=artifact.display_filename,
                storage_key=artifact.storage_key,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                declared_media_type=artifact.declared_media_type,
                detected_media_type=artifact.detected_media_type,
                artifact_kind=artifact.artifact_kind,
                malware_scan_status=artifact.malware_scan_status,
                extraction_status=artifact.extraction_status,
            ),
        ),
    )
    versions_at_assert: list[int] = []

    def recording_assert(
        observed_work: PackageRevisionIntakeWork,
        observed_revision: PackageRevision,
        **kwargs: object,
    ) -> None:
        versions_at_assert.append(observed_revision.revision_version)
        assert_intake_claim_live(
            observed_work,
            observed_revision,
            lease_owner="intake-worker-a",
            fence_token=FENCE_TOKEN,
            now=NOW,
        )

    session = _CommitRecordingSession(
        [
            _commit_scalar(work),
            _commit_scalar(revision),
            _commit_scalar(attempt),
        ]
    )

    with (
        patch(
            "ato_service.intake_work.assert_intake_claim_live",
            side_effect=recording_assert,
        ),
        patch("ato_service.intake.append_audit_event", new_callable=AsyncMock),
    ):
        result = _run(
            _commit_clean_scan(
                session,  # type: ignore[arg-type]
                work=work,
                revision=revision,
                artifacts=[artifact],
                claimed=claimed,
                snapshot=snapshot,
                hmac_key=HMAC_KEY,
                lease_owner="intake-worker-a",
                now=NOW,
            )
        )

    assert versions_at_assert == [2]
    assert revision.revision_version == 3
    assert work.status == IntakeWorkStatus.COMPLETED.value
    assert artifact.malware_scan_status == "clean"
    assert result.revision_version == 3
    assert any(
        isinstance(added, PackageRevisionIntakeWork)
        and added.work_phase == IntakeWorkPhase.DETERMINISTIC_EXTRACT.value
        for added in session.added
    )


@pytest.mark.integration
def test_intake_persists_draft_and_audit_atomically(tmp_path: Path) -> None:
    import os

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession

    from ato_service.db.models import AuditEvent, PackageRevisionIntakeWork
    from ato_service.db.session import create_async_engine_from_url
    from ato_service.intake import drain_intake
    from ato_service.intake_work import bootstrap_malware_scan_work
    from ato_service.malware_scan import resolve_malware_scanner

    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        json_bytes = FISMA_FIXTURE_PATH.read_bytes()
        docx_bytes = _build_docx("Supplemental evidence section")
        blob_store = BlobStore(tmp_path)
        json_stored = blob_store.store_stream(io.BytesIO(json_bytes), max_bytes=1024 * 1024)
        docx_stored = blob_store.store_stream(io.BytesIO(docx_bytes), max_bytes=1024 * 1024)
        config = _config(tmp_path)
        engine = create_async_engine_from_url(url)
        from ato_service.db.session import create_session_factory

        session_factory = create_session_factory(engine)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    session.add(
                        System(
                            system_id=SYSTEM_ID,
                            display_name="Unified intake integration",
                            external_system_id=None,
                            customer_enterprise_id="dev-local-enterprise",
                            owner_group="owners",
                            viewer_groups=[],
                            created_at=NOW,
                            archived_at=None,
                        )
                    )
                    revision = PackageRevision(
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
                        revision_version=2,
                        status="scanning",
                        created_by="integration@example.test",
                        created_at=NOW,
                    )
                    session.add(revision)
                    session.add_all(
                        [
                            SourceArtifact(
                                artifact_id=JSON_ARTIFACT_ID,
                                package_revision_id=REVISION_ID,
                                display_filename="manifest.json",
                                storage_key=json_stored.storage_key,
                                sha256=json_stored.sha256,
                                size_bytes=json_stored.size_bytes,
                                declared_media_type="application/json",
                                detected_media_type="application/json",
                                artifact_kind="manifest",
                                malware_scan_status="pending",
                                extraction_status="pending",
                                source_date=None,
                                uploaded_at=NOW,
                            ),
                            SourceArtifact(
                                artifact_id=DOCX_ARTIFACT_ID,
                                package_revision_id=REVISION_ID,
                                display_filename="narrative.docx",
                                storage_key=docx_stored.storage_key,
                                sha256=docx_stored.sha256,
                                size_bytes=docx_stored.size_bytes,
                                declared_media_type=(
                                    "application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document"
                                ),
                                detected_media_type=(
                                    "application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document"
                                ),
                                artifact_kind="evidence_document",
                                malware_scan_status="pending",
                                extraction_status="pending",
                                source_date=None,
                                uploaded_at=NOW,
                            ),
                        ]
                    )
                    bootstrap_malware_scan_work(
                        session,
                        package_revision_id=REVISION_ID,
                        expected_revision_version=2,
                        now=NOW,
                    )
                    await session.flush()
                finally:
                    await session.close()
                    await transaction.commit()

            results = await drain_intake(
                session_factory,
                config=config,
                blob_store=blob_store,
                hmac_key=HMAC_KEY,
                scanner=resolve_malware_scanner(config),
                now_factory=lambda: NOW,
            )
            assert len(results) == 2
            assert results[0].work_phase == IntakeWorkPhase.MALWARE_SCAN.value
            assert results[1].work_phase == IntakeWorkPhase.DETERMINISTIC_EXTRACT.value

            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    await session.refresh(
                        await session.get(PackageRevision, REVISION_ID)
                    )
                    revision = await session.get(PackageRevision, REVISION_ID)
                    assert revision is not None
                    assert revision.status == "awaiting_confirmation"
                    assert revision.revision_version == 4
                    draft = await session.get(PackageRevisionDraft, REVISION_ID)
                    assert draft is not None
                    assert draft.document_schema_version == "1.0.0"
                    assert draft.document["package"]["title"].startswith("Synthetic FISMA")
                    artifacts = (
                        await session.execute(
                            select(SourceArtifact).where(
                                SourceArtifact.package_revision_id == REVISION_ID
                            )
                        )
                    ).scalars().all()
                    assert all(artifact.malware_scan_status == "clean" for artifact in artifacts)
                    assert all(
                        artifact.extraction_status == "succeeded" for artifact in artifacts
                    )
                    audit_count = await session.scalar(
                        select(func.count()).select_from(AuditEvent).where(
                            AuditEvent.object_id == str(REVISION_ID).lower(),
                            AuditEvent.actor_id == "intake-worker",
                        )
                    )
                    extract_work = await session.get(
                        PackageRevisionIntakeWork,
                        {
                            "package_revision_id": REVISION_ID,
                            "work_phase": IntakeWorkPhase.DETERMINISTIC_EXTRACT.value,
                        },
                    )
                    assert extract_work is not None
                    assert extract_work.status == "completed"
                    assert audit_count == 2
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await engine.dispose()

    _run(exercise())
