"""Bounded source artifact upload for P1.1 package revision intake.

Blobs are written durably before database references are created. If the caller's
transaction rolls back after ``store_stream``, the content-addressed blob may
remain on disk without a referencing row; that is safe because storage keys are
derived from SHA-256 digests and unreferenced blobs are reclaimed by the storage
reconciler rather than being treated as committed package content.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import partial
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    require_system_mutation_access,
)
from ato_service.blobs import (
    BlobStore,
    BlobStoreError,
    BlobTooLargeError,
    EmptyBlobError,
    StoredBlob,
)
from ato_service.concurrency import format_package_revision_etag
from ato_service.db import constraints as ck
from ato_service.db import enums as ev
from ato_service.db.models import PackageRevision, SourceArtifact, System
from ato_service.domain_mapping import map_source_artifact_to_domain
from ato_service.idempotency import (
    load_idempotency_replay,
    record_idempotency_outcome,
    replay_etag_from_outcome,
    request_digest_from_payload,
)
from ato_service.lifecycle_transitions import (
    IllegalStateTransitionError,
    PackageRevisionStatus,
)
from ato_service.runtime_config import RuntimeLimits
from ato_service.storage_reconciliation import (
    StoragePathError,
    require_storage_regular_file,
)

OPERATION = "package_revisions.upload_file"

_HASH_BLOCK_SIZE = 1024 * 1024

_ALLOWED_DECLARED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "application/json",
        "text/plain",
    }
)

_MEDIA_JSON = "application/json"
_MEDIA_TEXT = "text/plain"

_STORAGE_KEY_PATTERN = re.compile(ck.STORAGE_KEY_REGEX)
_FILENAME_SEPARATOR_PATTERN = re.compile(r"[/\\]")
_FILENAME_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_TEXT_DISALLOWED_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResourceNotFoundError(Exception):
    """Raised when the target package revision does not exist."""

    error_code = "resource_not_found"

    def __init__(self) -> None:
        super().__init__("resource not found")


class RequestSchemaInvalidError(Exception):
    """Raised when upload request inputs fail schema validation."""

    error_code = "request_schema_invalid"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class UnsupportedMediaTypeError(Exception):
    """Raised when the declared media type is not accepted for P1.1 uploads."""

    error_code = "unsupported_media_type"


class SourceSizeLimitExceededError(Exception):
    """Raised when a single source artifact exceeds configured byte limits."""

    error_code = "source_size_limit_exceeded"


class PackageLimitExceededError(Exception):
    """Raised when revision file count or aggregate byte limits are exceeded."""

    error_code = "package_limit_exceeded"


class SourceTypeMismatchError(Exception):
    """Raised when durable bytes do not match the declared media type."""

    error_code = "source_type_mismatch"


class DuplicateSourceArtifactError(Exception):
    """Raised when the same SHA-256 is already stored on the revision."""

    error_code = "duplicate_canonical_id"


class SourceArtifactStorageError(Exception):
    """Raised when durable blob bytes cannot be read safely."""

    error_code = "storage_unavailable"


@dataclass(frozen=True, slots=True)
class UploadSourceArtifactResult:
    """Outcome of a replay-safe source artifact upload mutation."""

    status: int
    payload: dict[str, Any]
    etag: str
    replayed: bool


async def upload_source_artifact(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    audit_hmac_key: bytes,
    blob_store: BlobStore,
    limits: RuntimeLimits,
    package_revision_id: uuid.UUID,
    idempotency_key: str,
    source: BinaryIO,
    display_filename: str,
    declared_media_type: str,
    artifact_kind: str,
    source_date: date | None,
    now: datetime,
) -> UploadSourceArtifactResult:
    """Upload one bounded source artifact under a locked package revision.

    The caller owns the database transaction and must commit atomically with any
    other work in the unit of work. This helper does not commit.
    """
    validated_now = _require_aware_utc(now, field_name="now")
    validated_filename = _validate_display_filename(display_filename)
    validated_kind = _validate_artifact_kind(artifact_kind)
    normalized_declared_media_type = _validate_declared_media_type(declared_media_type)
    validated_source_date = _validate_source_date(source_date)
    initial_position = _require_seekable_stream(source)

    revision, system = await _lock_package_revision_and_system(
        session, package_revision_id
    )
    require_system_mutation_access(principal, system)
    _require_uploading_status(revision)

    prehashed_sha256, prehashed_size_bytes = _prehash_seekable_stream(
        source,
        initial_position=initial_position,
        max_bytes=limits.max_single_file_bytes,
    )

    request_digest = request_digest_from_payload(
        _upload_request_digest_payload(
            package_revision_id=package_revision_id,
            display_filename=validated_filename,
            declared_media_type=normalized_declared_media_type,
            artifact_kind=validated_kind,
            source_date=validated_source_date,
            sha256=prehashed_sha256,
        )
    )

    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        etag = replay_etag_from_outcome(
            response_body=replay.response_body,
            response_headers=replay.response_headers,
        )
        if etag is None:
            etag = format_package_revision_etag(revision.revision_version)
        return UploadSourceArtifactResult(
            status=replay.response_status,
            payload=dict(replay.response_body),
            etag=etag,
            replayed=True,
        )

    artifact_count, artifact_bytes = await _load_revision_artifact_totals(
        session, package_revision_id
    )

    try:
        stored_blob = await asyncio.to_thread(
            partial(
                blob_store.store_stream,
                source,
                max_bytes=limits.max_single_file_bytes,
            )
        )
    except BlobTooLargeError as exc:
        raise SourceSizeLimitExceededError(str(exc)) from exc
    except EmptyBlobError as exc:
        raise SourceSizeLimitExceededError(str(exc)) from exc
    except BlobStoreError as exc:
        raise SourceArtifactStorageError(str(exc)) from exc

    if (
        stored_blob.sha256 != prehashed_sha256
        or stored_blob.size_bytes != prehashed_size_bytes
    ):
        raise SourceTypeMismatchError(
            "stored blob digest or size does not match prehashed upload bytes"
        )

    detected_media_type = _detect_media_type(blob_store, stored_blob)
    if detected_media_type != normalized_declared_media_type:
        raise SourceTypeMismatchError(
            "declared media type does not match detected content"
        )

    _enforce_package_limits_before_insert(
        artifact_count=artifact_count,
        artifact_bytes=artifact_bytes,
        incoming_bytes=stored_blob.size_bytes,
        limits=limits,
    )

    if await _revision_has_sha256(
        session, package_revision_id, stored_blob.sha256
    ):
        raise DuplicateSourceArtifactError(
            "source artifact with the same SHA-256 already exists on this revision"
        )

    artifact = _insert_source_artifact(
        package_revision_id=package_revision_id,
        display_filename=validated_filename,
        stored_blob=stored_blob,
        declared_media_type=normalized_declared_media_type,
        detected_media_type=detected_media_type,
        artifact_kind=validated_kind,
        source_date=validated_source_date,
        uploaded_at=validated_now,
    )
    session.add(artifact)

    revision.revision_version += 1
    payload = map_source_artifact_to_domain(artifact)
    etag = format_package_revision_etag(revision.revision_version)

    await append_audit_event(
        session,
        hmac_key=audit_hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action=OPERATION,
        object_type="source_artifact",
        object_id=str(artifact.artifact_id),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "package_revision_id": str(package_revision_id).lower(),
            "artifact_kind": validated_kind,
            "sha256": stored_blob.sha256,
            "size_bytes": stored_blob.size_bytes,
        },
        occurred_at=validated_now,
    )

    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=201,
        response_body=payload,
        response_headers={"ETag": etag},
        now=validated_now,
    )

    return UploadSourceArtifactResult(
        status=201,
        payload=payload,
        etag=etag,
        replayed=False,
    )


def _require_seekable_stream(source: BinaryIO) -> int:
    if not hasattr(source, "seek") or not hasattr(source, "tell"):
        raise RequestSchemaInvalidError(
            "upload source must be a seekable byte stream"
        )
    try:
        initial_position = source.tell()
        source.seek(initial_position)
    except (OSError, ValueError) as exc:
        raise RequestSchemaInvalidError(
            "upload source must be a seekable byte stream"
        ) from exc
    return initial_position


def _prehash_seekable_stream(
    source: BinaryIO,
    *,
    initial_position: int,
    max_bytes: int,
) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total_bytes = 0
    source.seek(initial_position)

    while True:
        chunk = source.read(_HASH_BLOCK_SIZE)
        if not isinstance(chunk, bytes):
            raise RequestSchemaInvalidError(
                "upload source must return bytes from read()"
            )
        if chunk == b"":
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise SourceSizeLimitExceededError(
                f"blob exceeds configured maximum of {max_bytes} bytes"
            )
        hasher.update(chunk)

    if total_bytes == 0:
        raise SourceSizeLimitExceededError("blob input must not be empty")

    source.seek(initial_position)
    return hasher.hexdigest(), total_bytes


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RequestSchemaInvalidError(
            f"{field_name} must be a timezone-aware datetime"
        )
    return value.astimezone(timezone.utc)


def _validate_display_filename(display_filename: str) -> str:
    if not isinstance(display_filename, str):
        raise RequestSchemaInvalidError("display_filename must be a string")
    if len(display_filename) < 1 or len(display_filename) > 255:
        raise RequestSchemaInvalidError(
            "display_filename must be between 1 and 255 characters"
        )
    if (
        _FILENAME_SEPARATOR_PATTERN.search(display_filename) is not None
        or _FILENAME_CONTROL_PATTERN.search(display_filename) is not None
    ):
        raise RequestSchemaInvalidError(
            "display_filename contains unsafe path or control characters"
        )
    return display_filename


def _validate_artifact_kind(artifact_kind: str) -> str:
    if artifact_kind not in ev.ARTIFACT_KIND_VALUES:
        raise RequestSchemaInvalidError("artifact_kind is not accepted for uploads")
    return artifact_kind


def _validate_declared_media_type(declared_media_type: str) -> str:
    normalized = _normalize_media_type(declared_media_type)
    if normalized not in _ALLOWED_DECLARED_MEDIA_TYPES:
        raise UnsupportedMediaTypeError(
            "declared media type is not supported for P1.1 uploads"
        )
    return normalized


def _validate_source_date(source_date: date | None) -> date | None:
    if source_date is None:
        return None
    if not isinstance(source_date, date):
        raise RequestSchemaInvalidError("source_date must be a date or None")
    return source_date


def _normalize_media_type(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnsupportedMediaTypeError("declared media type must be nonempty")

    parts = [part.strip() for part in value.strip().split(";")]
    if not parts or not parts[0]:
        raise UnsupportedMediaTypeError("declared media type must be nonempty")

    media_type = parts[0].lower()
    if media_type not in _ALLOWED_DECLARED_MEDIA_TYPES:
        raise UnsupportedMediaTypeError(
            "declared media type is not supported for P1.1 uploads"
        )

    if len(parts) == 1:
        return media_type

    if len(parts) != 2:
        raise UnsupportedMediaTypeError(
            "declared media type has unexpected parameters"
        )

    parameter_name, _, parameter_value = parts[1].partition("=")
    if parameter_name.lower() != "charset":
        raise UnsupportedMediaTypeError(
            "declared media type has unexpected parameters"
        )

    normalized_charset = parameter_value.strip().strip('"').strip("'").lower()
    if normalized_charset != "utf-8":
        raise UnsupportedMediaTypeError("declared media type charset is not supported")

    return media_type


def _require_uploading_status(revision: PackageRevision) -> None:
    if revision.status != PackageRevisionStatus.UPLOADING.value:
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state=revision.status,
            target_state=PackageRevisionStatus.UPLOADING.value,
        )


async def _lock_package_revision_and_system(
    session: AsyncSession,
    package_revision_id: uuid.UUID,
) -> tuple[PackageRevision, System]:
    statement = (
        select(PackageRevision, System)
        .join(System, PackageRevision.system_id == System.system_id)
        .where(PackageRevision.package_revision_id == package_revision_id)
        .with_for_update(of=PackageRevision)
    )
    result = await session.execute(statement)
    row = result.one_or_none()
    if row is None:
        raise ResourceNotFoundError()
    revision, system = row
    return revision, system


async def _load_revision_artifact_totals(
    session: AsyncSession,
    package_revision_id: uuid.UUID,
) -> tuple[int, int]:
    statement = select(
        func.count(SourceArtifact.artifact_id),
        func.coalesce(func.sum(SourceArtifact.size_bytes), 0),
    ).where(SourceArtifact.package_revision_id == package_revision_id)
    result = await session.execute(statement)
    count, total_bytes = result.one()
    return int(count), int(total_bytes)


def _enforce_package_limits_before_insert(
    *,
    artifact_count: int,
    artifact_bytes: int,
    incoming_bytes: int,
    limits: RuntimeLimits,
) -> None:
    if artifact_count >= limits.max_files_per_revision:
        raise PackageLimitExceededError(
            "revision file count limit exceeded before accepting another artifact"
        )
    if incoming_bytes > limits.max_single_file_bytes:
        raise SourceSizeLimitExceededError(
            "source artifact exceeds configured single-file byte limit"
        )
    if artifact_bytes >= limits.max_package_bytes:
        raise PackageLimitExceededError(
            "revision aggregate byte limit already reached before accepting another artifact"
        )
    if artifact_bytes + incoming_bytes > limits.max_package_bytes:
        raise PackageLimitExceededError(
            "revision aggregate byte limit would be exceeded by this artifact"
        )


async def _revision_has_sha256(
    session: AsyncSession,
    package_revision_id: uuid.UUID,
    sha256: str,
) -> bool:
    statement = (
        select(SourceArtifact.artifact_id)
        .where(
            SourceArtifact.package_revision_id == package_revision_id,
            SourceArtifact.sha256 == sha256,
        )
        .limit(1)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


def _insert_source_artifact(
    *,
    package_revision_id: uuid.UUID,
    display_filename: str,
    stored_blob: StoredBlob,
    declared_media_type: str,
    detected_media_type: str,
    artifact_kind: str,
    source_date: date | None,
    uploaded_at: datetime,
) -> SourceArtifact:
    return SourceArtifact(
        artifact_id=uuid.uuid4(),
        package_revision_id=package_revision_id,
        display_filename=display_filename,
        storage_key=stored_blob.storage_key,
        sha256=stored_blob.sha256,
        size_bytes=stored_blob.size_bytes,
        declared_media_type=declared_media_type,
        detected_media_type=detected_media_type,
        artifact_kind=artifact_kind,
        malware_scan_status="pending",
        extraction_status="pending",
        source_date=source_date,
        uploaded_at=uploaded_at,
    )


def _upload_request_digest_payload(
    *,
    package_revision_id: uuid.UUID,
    display_filename: str,
    declared_media_type: str,
    artifact_kind: str,
    source_date: date | None,
    sha256: str,
) -> dict[str, Any]:
    return {
        "artifact_kind": artifact_kind,
        "declared_media_type": declared_media_type,
        "display_filename": display_filename,
        "package_revision_id": str(package_revision_id).lower(),
        "sha256": sha256,
        "source_date": None if source_date is None else source_date.isoformat(),
    }


def _detect_media_type(blob_store: BlobStore, stored_blob: StoredBlob) -> str:
    blob_bytes = _read_stored_blob_bytes(blob_store, stored_blob)
    try:
        text = blob_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceTypeMismatchError("source bytes are not valid UTF-8") from exc

    try:
        json.loads(text)
    except json.JSONDecodeError:
        if _TEXT_DISALLOWED_CONTROL_PATTERN.search(text) is not None:
            raise SourceTypeMismatchError(
                "text/plain source contains disallowed control characters"
            )
        return _MEDIA_TEXT

    return _MEDIA_JSON


def _read_stored_blob_bytes(
    blob_store: BlobStore,
    stored_blob: StoredBlob,
) -> bytes:
    if stored_blob.size_bytes < 1:
        raise SourceTypeMismatchError("stored blob size is invalid")

    if _STORAGE_KEY_PATTERN.fullmatch(stored_blob.storage_key) is None:
        raise SourceTypeMismatchError("stored blob key is invalid")

    prefix, digest = stored_blob.storage_key.split("/", 1)
    if digest != stored_blob.sha256 or prefix != stored_blob.sha256[:2]:
        raise SourceTypeMismatchError("stored blob key does not match digest")

    try:
        blob_path = require_storage_regular_file(
            blob_store.storage_root,
            "blobs",
            prefix,
            digest,
        )
    except FileNotFoundError as exc:
        raise SourceArtifactStorageError("stored blob is not available") from exc
    except StoragePathError as exc:
        raise SourceArtifactStorageError("stored blob path is unsafe") from exc

    try:
        actual_size = blob_path.stat().st_size
    except OSError as exc:
        raise SourceArtifactStorageError(
            "stored blob metadata could not be read"
        ) from exc

    if actual_size != stored_blob.size_bytes:
        raise SourceTypeMismatchError("stored blob size does not match declared size")

    read_upper_bound = stored_blob.size_bytes + 1
    try:
        with blob_path.open("rb") as blob_file:
            blob_bytes = blob_file.read(read_upper_bound)
    except OSError as exc:
        raise SourceArtifactStorageError("stored blob could not be read") from exc

    if len(blob_bytes) != stored_blob.size_bytes:
        raise SourceTypeMismatchError(
            "stored blob content does not match declared size"
        )

    actual_digest = hashlib.sha256(blob_bytes).hexdigest()
    if actual_digest != stored_blob.sha256:
        raise SourceTypeMismatchError("stored blob digest does not match declared sha256")

    return blob_bytes


__all__ = [
    "DuplicateSourceArtifactError",
    "OPERATION",
    "PackageLimitExceededError",
    "RequestSchemaInvalidError",
    "ResourceNotFoundError",
    "SourceArtifactStorageError",
    "SourceSizeLimitExceededError",
    "SourceTypeMismatchError",
    "UnsupportedMediaTypeError",
    "UploadSourceArtifactResult",
    "upload_source_artifact",
]
