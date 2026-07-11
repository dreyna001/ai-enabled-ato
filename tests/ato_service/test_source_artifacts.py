"""Focused tests for bounded P1.1 source artifact uploads."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.blobs import BlobStore, BlobStoreError
from ato_service.db import enums as ev
from ato_service.db.models import IdempotencyRecord, PackageRevision, SourceArtifact, System
from ato_service.idempotency import IdempotencyConflictError, request_digest_from_payload
from ato_service.lifecycle_transitions import IllegalStateTransitionError
from ato_service.runtime_config import RuntimeLimits
from ato_service.source_artifacts import (
    DuplicateSourceArtifactError,
    OPERATION,
    PackageLimitExceededError,
    RequestSchemaInvalidError,
    ResourceNotFoundError,
    SourceArtifactStorageError,
    SourceSizeLimitExceededError,
    SourceTypeMismatchError,
    UnsupportedMediaTypeError,
    UploadSourceArtifactResult,
    _upload_request_digest_payload,
    upload_source_artifact,
)
from ato_service.storage_reconciliation import require_storage_regular_file

UTC = timezone.utc
NOW = datetime(2026, 7, 11, 16, 0, 0, tzinfo=UTC)
HMAC_KEY = b"k" * MIN_AUDIT_HMAC_KEY_BYTES
SYSTEM_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
IDEM_KEY = "upload-idem-key-0001"
OWNER_GROUP = "owners"
VIEWER_GROUP = "viewers"


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _limits(
    *,
    max_single_file_bytes: int = 1024,
    max_files_per_revision: int = 5,
    max_package_bytes: int = 4096,
) -> RuntimeLimits:
    return RuntimeLimits(
        max_model_calls_per_run=10,
        max_package_bytes=max_package_bytes,
        max_single_file_bytes=max_single_file_bytes,
        max_files_per_revision=max_files_per_revision,
    )


def _principal(*, groups: tuple[str, ...] = (OWNER_GROUP,)) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="operator@example.test",
        groups=groups,
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example.test",),
    )


def _system() -> System:
    return System(
        system_id=SYSTEM_ID,
        display_name="Demo System",
        external_system_id=None,
        owner_group=OWNER_GROUP,
        viewer_groups=[VIEWER_GROUP],
        created_at=NOW,
        archived_at=None,
    )


def _revision(
    *,
    revision_version: int = 1,
    status: str = "uploading",
) -> PackageRevision:
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=SYSTEM_ID,
        parent_revision_id=None,
        profile_id="fedramp_20x_program",
        certification_class=None,
        impact_level=None,
        data_origin="customer",
        sensitivity="public",
        effective_data_labels=["public"],
        authority_manifest_id="authority.demo",
        content_manifest_sha256=None,
        revision_version=revision_version,
        status=status,
        created_by="operator@example.test",
        created_at=NOW,
    )


def _row_result(value: object) -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = value
    result.one.return_value = value
    result.scalar_one_or_none.return_value = value
    return result


@dataclass
class _UploadSession:
    revision: PackageRevision
    system: System
    artifact_count: int = 0
    artifact_bytes: int = 0
    existing_sha256: str | None = None
    stored_idempotency: IdempotencyRecord | None = None
    added: list[object] = field(default_factory=list)
    execute_calls: list[object] = field(default_factory=list)

    async def execute(self, statement: object) -> MagicMock:
        self.execute_calls.append(statement)
        sql = str(statement)

        if "FROM package_revisions" in sql and "FOR UPDATE" in sql:
            return _row_result((self.revision, self.system))

        if "count(" in sql and "source_artifacts" in sql:
            return _row_result((self.artifact_count, self.artifact_bytes))

        if "FROM idempotency_records" in sql:
            return _row_result(self.stored_idempotency)

        if "FROM source_artifacts" in sql and "sha256" in sql:
            return _row_result(ARTIFACT_ID if self.existing_sha256 else None)

        if "pg_advisory_xact_lock" in sql:
            return _row_result(None)

        if "FROM audit_events" in sql:
            return _row_result(None)

        raise AssertionError(f"unexpected execute statement: {sql}")

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _upload(
    session: _UploadSession,
    blob_store: BlobStore,
    *,
    source: io.BytesIO,
    display_filename: str = "evidence.json",
    declared_media_type: str = "application/json",
    artifact_kind: str = "manifest",
    source_date: date | None = None,
    limits: RuntimeLimits | None = None,
    idempotency_key: str = IDEM_KEY,
) -> UploadSourceArtifactResult:
    return _run(
        upload_source_artifact(
            session,
            principal=_principal(),
            audit_hmac_key=HMAC_KEY,
            blob_store=blob_store,
            limits=limits or _limits(),
            package_revision_id=REVISION_ID,
            idempotency_key=idempotency_key,
            source=source,
            display_filename=display_filename,
            declared_media_type=declared_media_type,
            artifact_kind=artifact_kind,
            source_date=source_date,
            now=NOW,
        )
    )


def test_upload_json_artifact_persists_durable_bytes_and_increments_version(
    tmp_path: Path,
) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(revision_version=1), system=_system())
    payload = {"controls": ["ac-1"]}
    source = io.BytesIO(json.dumps(payload).encode("utf-8"))

    result = _upload(session, store, source=source)

    assert result.status == 201
    assert result.replayed is False
    assert result.etag == '"v2"'
    assert result.payload["object_type"] == "source_artifact"
    assert result.payload["declared_media_type"] == "application/json"
    assert result.payload["detected_media_type"] == "application/json"
    assert result.payload["artifact_kind"] == "manifest"
    assert result.payload["malware_scan_status"] == "pending"
    assert result.payload["extraction_status"] == "pending"
    assert session.revision.revision_version == 2

    artifacts = [obj for obj in session.added if isinstance(obj, SourceArtifact)]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    prefix, digest = artifact.storage_key.split("/", 1)
    blob_path = require_storage_regular_file(store.storage_root, "blobs", prefix, digest)
    assert json.loads(blob_path.read_text(encoding="utf-8")) == payload
    assert artifact.uploaded_at == NOW

    idempotency_rows = [obj for obj in session.added if isinstance(obj, IdempotencyRecord)]
    assert len(idempotency_rows) == 1
    assert idempotency_rows[0].response_body["sha256"] == artifact.sha256
    assert idempotency_rows[0].response_headers == {"ETag": '"v2"'}


def test_upload_text_plain_artifact(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())
    source = io.BytesIO(b"plain evidence note\n")

    result = _upload(
        session,
        store,
        source=source,
        display_filename="note.txt",
        declared_media_type="text/plain",
        artifact_kind="evidence_document",
    )

    assert result.payload["detected_media_type"] == "text/plain"
    assert result.payload["declared_media_type"] == "text/plain"


def test_rejects_unsupported_declared_media_type(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(UnsupportedMediaTypeError) as exc_info:
        _upload(
            session,
            store,
            source=io.BytesIO(b"{}"),
            declared_media_type="application/pdf",
        )

    assert exc_info.value.error_code == "unsupported_media_type"
    assert session.added == []


def test_rejects_mime_mismatch_for_text_declared_as_json(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(SourceTypeMismatchError) as exc_info:
        _upload(
            session,
            store,
            source=io.BytesIO(b"not-json"),
            declared_media_type="application/json",
        )

    assert exc_info.value.error_code == "source_type_mismatch"


def test_rejects_traversal_display_filename(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(RequestSchemaInvalidError) as exc_info:
        _upload(
            session,
            store,
            source=io.BytesIO(b"{}"),
            display_filename="../../etc/passwd",
        )

    assert exc_info.value.error_code == "request_schema_invalid"


@pytest.mark.parametrize(
    "filename",
    [
        "bad\x00name.json",
        "bad/name.json",
        "bad\\name.json",
        "",
        "x" * 256,
    ],
)
def test_rejects_unsafe_display_filenames(
    tmp_path: Path,
    filename: str,
) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(RequestSchemaInvalidError) as exc_info:
        _upload(session, store, source=io.BytesIO(b"{}"), display_filename=filename)

    assert exc_info.value.error_code == "request_schema_invalid"


def test_rejects_single_file_size_limit(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())
    limits = _limits(max_single_file_bytes=8)

    with pytest.raises(SourceSizeLimitExceededError) as exc_info:
        _upload(
            session,
            store,
            source=io.BytesIO(b'{"data":"too-large"}'),
            limits=limits,
        )

    assert exc_info.value.error_code == "source_size_limit_exceeded"


def test_rejects_revision_file_count_limit(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(
        revision=_revision(),
        system=_system(),
        artifact_count=1,
    )
    limits = _limits(max_files_per_revision=1)

    with pytest.raises(PackageLimitExceededError) as exc_info:
        _upload(session, store, source=io.BytesIO(b"{}"), limits=limits)

    assert exc_info.value.error_code == "package_limit_exceeded"


def test_rejects_revision_aggregate_byte_limit(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(
        revision=_revision(),
        system=_system(),
        artifact_bytes=9,
    )
    limits = _limits(max_package_bytes=10, max_single_file_bytes=10)

    with pytest.raises(PackageLimitExceededError) as exc_info:
        _upload(session, store, source=io.BytesIO(b"1234567890"), limits=limits)

    assert exc_info.value.error_code == "package_limit_exceeded"


def test_rejects_duplicate_sha256_with_different_idempotency_key(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(
        revision=_revision(),
        system=_system(),
        existing_sha256="deadbeef",
    )
    source = io.BytesIO(b"{}")

    with pytest.raises(DuplicateSourceArtifactError) as exc_info:
        _upload(session, store, source=source, idempotency_key="another-idem-key-01")

    assert exc_info.value.error_code == "duplicate_canonical_id"
    assert session.revision.revision_version == 1


def test_replay_returns_stored_etag_without_blob_store_or_mutation(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    source = io.BytesIO(b"{}")
    stored = store.store_stream(io.BytesIO(b"{}"), max_bytes=1024)
    replay_digest = request_digest_from_payload(
        _upload_request_digest_payload(
            package_revision_id=REVISION_ID,
            display_filename="evidence.json",
            declared_media_type="application/json",
            artifact_kind="manifest",
            source_date=None,
            sha256=stored.sha256,
        )
    )
    revision = _revision(revision_version=2)
    prior_payload = {
        "schema_version": "2.0.0",
        "object_type": "source_artifact",
        "artifact_id": str(ARTIFACT_ID),
        "package_revision_id": str(REVISION_ID),
        "display_filename": "evidence.json",
        "storage_key": stored.storage_key,
        "sha256": stored.sha256,
        "size_bytes": stored.size_bytes,
        "declared_media_type": "application/json",
        "detected_media_type": "application/json",
        "artifact_kind": "manifest",
        "malware_scan_status": "pending",
        "extraction_status": "pending",
        "source_date": None,
        "uploaded_at": "2026-07-11T16:00:00Z",
    }
    session = _UploadSession(
        revision=revision,
        system=_system(),
        stored_idempotency=IdempotencyRecord(
            idempotency_record_id=uuid.uuid4(),
            principal=_principal().actor_id,
            operation=OPERATION,
            idempotency_key=IDEM_KEY,
            request_digest=replay_digest,
            response_status=201,
            response_body=prior_payload,
            response_headers={"ETag": '"v5"'},
            created_at=NOW,
            expires_at=NOW + timedelta(hours=24),
        ),
    )
    store_calls: list[object] = []
    original_store_stream = store.store_stream

    def _counting_store_stream(*args: object, **kwargs: object) -> object:
        store_calls.append((args, kwargs))
        return original_store_stream(*args, **kwargs)  # type: ignore[arg-type]

    store.store_stream = _counting_store_stream  # type: ignore[method-assign]

    result = _upload(session, store, source=source)
    added_artifacts = [obj for obj in session.added if isinstance(obj, SourceArtifact)]
    added_idempotency = [obj for obj in session.added if isinstance(obj, IdempotencyRecord)]

    assert result.replayed is True
    assert result.status == 201
    assert result.payload == prior_payload
    assert result.etag == '"v5"'
    assert revision.revision_version == 2
    assert added_artifacts == []
    assert added_idempotency == []
    assert store_calls == []


def test_conflict_raises_without_blob_store_write(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    source = io.BytesIO(b"{}")
    request_digest = request_digest_from_payload(
        _upload_request_digest_payload(
            package_revision_id=REVISION_ID,
            display_filename="evidence.json",
            declared_media_type="application/json",
            artifact_kind="manifest",
            source_date=None,
            sha256=hashlib.sha256(b"{}").hexdigest(),
        )
    )
    session = _UploadSession(
        revision=_revision(),
        system=_system(),
        stored_idempotency=IdempotencyRecord(
            idempotency_record_id=uuid.uuid4(),
            principal=_principal().actor_id,
            operation=OPERATION,
            idempotency_key=IDEM_KEY,
            request_digest="f" * 64,
            response_status=201,
            response_body={"status": "ready"},
            response_headers={"ETag": '"v1"'},
            created_at=NOW,
            expires_at=NOW + timedelta(hours=24),
        ),
    )
    store_calls: list[object] = []
    original_store_stream = store.store_stream

    def _counting_store_stream(*args: object, **kwargs: object) -> object:
        store_calls.append((args, kwargs))
        return original_store_stream(*args, **kwargs)  # type: ignore[arg-type]

    store.store_stream = _counting_store_stream  # type: ignore[method-assign]

    with pytest.raises(IdempotencyConflictError):
        _upload(session, store, source=source)

    assert store_calls == []
    assert request_digest != "f" * 64


def test_request_digest_includes_revision_metadata_and_sha256(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())
    source = io.BytesIO(b"{}")

    result = _upload(session, store, source=source, source_date=date(2026, 7, 9))
    stored = next(obj for obj in session.added if isinstance(obj, SourceArtifact))

    expected = request_digest_from_payload(
        _upload_request_digest_payload(
            package_revision_id=REVISION_ID,
            display_filename="evidence.json",
            declared_media_type="application/json",
            artifact_kind="manifest",
            source_date=date(2026, 7, 9),
            sha256=stored.sha256,
        )
    )
    idempotency_row = next(obj for obj in session.added if isinstance(obj, IdempotencyRecord))
    assert idempotency_row.request_digest == expected
    assert result.payload["source_date"] == "2026-07-09"


def test_rejects_non_uploading_revision_status(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(
        revision=_revision(status="scanning"),
        system=_system(),
    )

    with pytest.raises(IllegalStateTransitionError) as exc_info:
        _upload(session, store, source=io.BytesIO(b"{}"))

    assert exc_info.value.error_code == "illegal_state_transition"


def test_rejects_missing_revision(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)

    class _MissingRevisionSession:
        async def execute(self, statement: object) -> MagicMock:
            return _row_result(None)

        def add(self, obj: object) -> None:
            raise AssertionError("add should not be called")

    with pytest.raises(ResourceNotFoundError) as exc_info:
        _run(
            upload_source_artifact(
                _MissingRevisionSession(),
                principal=_principal(),
                audit_hmac_key=HMAC_KEY,
                blob_store=store,
                limits=_limits(),
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                source=io.BytesIO(b"{}"),
                display_filename="evidence.json",
                declared_media_type="application/json",
                artifact_kind="manifest",
                source_date=None,
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "resource_not_found"
    assert str(exc_info.value) == "resource not found"
    assert str(REVISION_ID) not in str(exc_info.value)


def test_rejects_non_owner_mutation(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(AuthorizationDeniedError):
        _run(
            upload_source_artifact(
                session,
                principal=_principal(groups=(VIEWER_GROUP,)),
                audit_hmac_key=HMAC_KEY,
                blob_store=store,
                limits=_limits(),
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                source=io.BytesIO(b"{}"),
                display_filename="evidence.json",
                declared_media_type="application/json",
                artifact_kind="manifest",
                source_date=None,
                now=NOW,
            )
        )


def test_normalizes_declared_media_type_parameters(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    result = _upload(
        session,
        store,
        source=io.BytesIO(b"{}"),
        declared_media_type="application/json; charset=utf-8",
    )

    assert result.payload["declared_media_type"] == "application/json"


def test_rejects_naive_now_before_db_or_storage(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())
    naive_now = datetime(2026, 7, 11, 16, 0, 0)

    with pytest.raises(RequestSchemaInvalidError) as exc_info:
        _run(
            upload_source_artifact(
                session,
                principal=_principal(),
                audit_hmac_key=HMAC_KEY,
                blob_store=store,
                limits=_limits(),
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                source=io.BytesIO(b"{}"),
                display_filename="evidence.json",
                declared_media_type="application/json",
                artifact_kind="manifest",
                source_date=None,
                now=naive_now,
            )
        )

    assert exc_info.value.error_code == "request_schema_invalid"
    assert session.execute_calls == []
    assert session.added == []
    assert not any(tmp_path.rglob("blobs/**"))


def test_rejects_unsupported_artifact_kind(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(RequestSchemaInvalidError) as exc_info:
        _upload(
            session,
            store,
            source=io.BytesIO(b"{}"),
            artifact_kind="not_a_real_kind",
        )

    assert exc_info.value.error_code == "request_schema_invalid"
    assert session.execute_calls == []


@pytest.mark.parametrize("artifact_kind", ev.ARTIFACT_KIND_VALUES)
def test_accepts_all_contract_artifact_kinds(tmp_path: Path, artifact_kind: str) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    result = _upload(
        session,
        store,
        source=io.BytesIO(b"{}"),
        artifact_kind=artifact_kind,
    )

    assert result.status == 201
    assert result.payload["artifact_kind"] == artifact_kind


class _NonSeekableStream:
    def read(self, size: int = -1) -> bytes:
        return b"{}"


def test_rejects_non_seekable_source_before_storage_mutation(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(RequestSchemaInvalidError) as exc_info:
        _run(
            upload_source_artifact(
                session,
                principal=_principal(),
                audit_hmac_key=HMAC_KEY,
                blob_store=store,
                limits=_limits(),
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                source=_NonSeekableStream(),  # type: ignore[arg-type]
                display_filename="evidence.json",
                declared_media_type="application/json",
                artifact_kind="manifest",
                source_date=None,
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "request_schema_invalid"
    assert session.execute_calls == []
    assert session.added == []
    assert not any(tmp_path.rglob("blobs/**"))


class _FailingBlobStore(BlobStore):
    def store_stream(self, source: object, *, max_bytes: int) -> object:
        raise BlobStoreError("blob storage backend failed")


def test_maps_generic_blob_store_error_to_storage_unavailable(tmp_path: Path) -> None:
    store = _FailingBlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(SourceArtifactStorageError) as exc_info:
        _upload(session, store, source=io.BytesIO(b"{}"))

    assert exc_info.value.error_code == "storage_unavailable"
    assert "blob storage backend failed" in str(exc_info.value)


def test_rejects_invalid_source_date_type(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(RequestSchemaInvalidError) as exc_info:
        _run(
            upload_source_artifact(
                session,
                principal=_principal(),
                audit_hmac_key=HMAC_KEY,
                blob_store=store,
                limits=_limits(),
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                source=io.BytesIO(b"{}"),
                display_filename="evidence.json",
                declared_media_type="application/json",
                artifact_kind="manifest",
                source_date="2026-07-09",  # type: ignore[arg-type]
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "request_schema_invalid"
    assert session.execute_calls == []


@pytest.mark.parametrize(
    "declared_media_type",
    [
        "application/json; boundary=abc",
        "application/json; charset=iso-8859-1",
        "text/plain; foo=bar",
        "not-a/media-type",
    ],
)
def test_rejects_malformed_or_unsupported_content_type_parameters(
    tmp_path: Path,
    declared_media_type: str,
) -> None:
    store = BlobStore(tmp_path)
    session = _UploadSession(revision=_revision(), system=_system())

    with pytest.raises(UnsupportedMediaTypeError) as exc_info:
        _upload(
            session,
            store,
            source=io.BytesIO(b"{}"),
            declared_media_type=declared_media_type,
        )

    assert exc_info.value.error_code == "unsupported_media_type"
    assert session.execute_calls == []
