"""Transactional PackageRevision persistence and lifecycle mutations for P1.1."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.authorization_boundary import (
    ClassifiedAuthorizationInputError,
    require_unclassified_sensitivity,
)
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.package_rbac import require_any_package_role, require_package_role
from ato_service.route_role_matrix import (
    ROLE_ISSO,
    ROLE_SYSTEM_OWNER,
    ROLE_VIEWER,
)
from ato_service.concurrency import (
    IfMatchRequiredError,
    assert_if_match,
    format_package_revision_etag,
)
from ato_service.content_manifests import (
    ContentManifestBlobError,
    ContentManifestCommitError,
    ContentManifestConflictError,
    ContentManifestError,
    ContentManifestValidationError,
    ManifestSourceEntry,
    write_content_manifest,
)
from ato_service.db import enums as ev
from ato_service.domain_mapping import format_uuid, map_package_revision_to_domain
from ato_service.idempotency import (
    IdempotencyReplay,
    load_idempotency_replay,
    record_idempotency_outcome,
    replay_etag_from_outcome,
    request_digest_from_payload,
)
from ato_service.intake_work import bootstrap_malware_scan_work
from ato_service.package_revision_drafts import (
    load_draft_for_confirm,
    seal_package_revision_draft,
)
from ato_service.lifecycle_transitions import (
    IllegalStateTransitionError,
    PackageRevisionStatus,
    PackageRevisionTransitionCondition,
    require_package_revision_transition,
)
from ato_service.pagination import (
    PaginationCursor,
    decode_pagination_cursor,
    encode_pagination_cursor,
    validate_page_limit,
)
from ato_service.runtime_config import RuntimeLimits

OPERATION_CREATE = "package_revisions.create"
OPERATION_FINALIZE = "package_revisions.finalize"
OPERATION_CONFIRM = "package_revisions.confirm"

HTTP_CREATED = 201
HTTP_OK = 200
HTTP_ACCEPTED = 202

_RESOURCE_NOT_FOUND_MESSAGE = "requested resource was not found"


class PackageRevisionNotFoundError(Exception):
    """Raised when a package revision cannot be loaded for the caller."""

    error_code = "resource_not_found"

    def __init__(self, *, package_revision_id: uuid.UUID) -> None:
        self.package_revision_id = package_revision_id
        super().__init__(_RESOURCE_NOT_FOUND_MESSAGE)


class SystemNotFoundError(Exception):
    """Raised when a system cannot be loaded for package revision work."""

    error_code = "resource_not_found"

    def __init__(self, *, system_id: uuid.UUID) -> None:
        self.system_id = system_id
        super().__init__(_RESOURCE_NOT_FOUND_MESSAGE)


class ParentRevisionNotFoundError(Exception):
    """Raised when a parent revision is missing or belongs to another system."""

    error_code = "resource_not_found"

    def __init__(self, *, parent_revision_id: uuid.UUID) -> None:
        self.parent_revision_id = parent_revision_id
        super().__init__(_RESOURCE_NOT_FOUND_MESSAGE)


class PackageRevisionValidationError(ValueError):
    """Raised when package revision inputs fail validation."""

    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class ProfileBoundaryError(PackageRevisionValidationError):
    """Raised when profile, class, and impact boundaries are inconsistent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_code="request_schema_invalid")


class EmptyPackageRevisionError(PackageRevisionValidationError):
    """Raised when finalize is requested without any source artifacts."""

    def __init__(self) -> None:
        super().__init__(
            "package revision has no source artifacts",
            error_code="request_schema_invalid",
        )


class UnconfirmedFactProposalsError(Exception):
    """Raised when confirm is blocked by pending fact proposals."""

    error_code = "unconfirmed_fact_proposals"


class PackageRevisionStorageError(Exception):
    """Raised when durable manifest or blob storage is unavailable."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "storage_unavailable",
        retryable: bool = True,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CreatePackageRevisionInput:
    """Validated create payload for a new package revision."""

    parent_revision_id: uuid.UUID | None
    profile_id: str
    certification_class: str | None
    impact_level: str | None
    data_origin: str
    sensitivity: str


@dataclass(frozen=True, slots=True)
class PackageRevisionMutationResult:
    """Mutation outcome for replay-safe package revision operations."""

    payload: dict[str, Any]
    status: int
    etag: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class PackageRevisionListResult:
    """Paginated package revision list for one system."""

    items: tuple[dict[str, Any], ...]
    next_cursor: str | None


def _load_system_statement(system_id: uuid.UUID) -> Any:
    from ato_service.db.models import System

    return select(System).where(System.system_id == system_id)


def _load_system_for_update_statement(system_id: uuid.UUID) -> Any:
    return _load_system_statement(system_id).with_for_update()


def _load_package_revision_for_update_statement(
    package_revision_id: uuid.UUID,
) -> Any:
    from ato_service.db.models import PackageRevision

    return (
        select(PackageRevision)
        .where(PackageRevision.package_revision_id == package_revision_id)
        .with_for_update()
    )


def _load_parent_revision_statement(parent_revision_id: uuid.UUID) -> Any:
    from ato_service.db.models import PackageRevision

    return (
        select(PackageRevision)
        .where(PackageRevision.package_revision_id == parent_revision_id)
    )


def _load_package_revision_with_system_statement(
    package_revision_id: uuid.UUID,
) -> Any:
    from ato_service.db.models import PackageRevision, System

    return (
        select(PackageRevision, System)
        .join(System, System.system_id == PackageRevision.system_id)
        .where(PackageRevision.package_revision_id == package_revision_id)
    )


def _list_package_revisions_statement(
    *,
    system_id: uuid.UUID,
    cursor: PaginationCursor | None,
    limit: int,
) -> Any:
    from ato_service.db.models import PackageRevision

    statement = (
        select(PackageRevision)
        .where(PackageRevision.system_id == system_id)
        .order_by(
            PackageRevision.created_at.asc(),
            PackageRevision.package_revision_id.asc(),
        )
        .limit(limit + 1)
    )
    if cursor is not None:
        statement = statement.where(
            tuple_(
                PackageRevision.created_at,
                PackageRevision.package_revision_id,
            )
            > tuple_(cursor.created_at, cursor.item_id)
        )
    return statement


def _load_source_artifacts_statement(package_revision_id: uuid.UUID) -> Any:
    from ato_service.db.models import SourceArtifact

    return (
        select(SourceArtifact)
        .where(SourceArtifact.package_revision_id == package_revision_id)
        .order_by(SourceArtifact.artifact_id.asc())
    )


def _pending_fact_proposal_exists_statement(package_revision_id: uuid.UUID) -> Any:
    from ato_service.db.models import FactProposal

    return select(
        exists().where(
            FactProposal.package_revision_id == package_revision_id,
            FactProposal.review_status == "pending",
        )
    )


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PackageRevisionValidationError(
            f"{field_name} must be a timezone-aware datetime"
        )
    return value.astimezone(timezone.utc)


def _require_enum_value(value: str, *, field_name: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise PackageRevisionValidationError(f"{field_name} is not supported")
    return value


def _effective_data_labels(data_origin: str, sensitivity: str) -> list[str]:
    labels = sorted({data_origin, sensitivity})
    if len(labels) != 2:
        raise PackageRevisionValidationError(
            "effective_data_labels must contain unique data_origin and sensitivity values"
        )
    return labels


def validate_profile_boundaries(
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
) -> None:
    """Validate profile/class/impact boundaries exactly per domain schema."""
    profile_id = _require_enum_value(
        profile_id,
        field_name="profile_id",
        allowed=ev.PROFILE_ID_VALUES,
    )

    if profile_id == "fedramp_20x_program":
        if certification_class not in ("B", "C"):
            raise ProfileBoundaryError(
                "fedramp_20x_program requires certification_class B or C"
            )
        if impact_level is not None:
            raise ProfileBoundaryError(
                "fedramp_20x_program requires impact_level to be null"
            )
        return

    if profile_id in ("fedramp_rev5_transition", "fisma_agency_security"):
        if certification_class is not None:
            raise ProfileBoundaryError(
                f"{profile_id} requires certification_class to be null"
            )
        if impact_level not in ev.IMPACT_LEVEL_VALUES:
            raise ProfileBoundaryError(
                f"{profile_id} requires impact_level low, moderate, or high"
            )
        return

    raise ProfileBoundaryError("profile_id is not supported")


def validate_create_input(
    *,
    parent_revision_id: uuid.UUID | None,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
    data_origin: str,
    sensitivity: str,
) -> CreatePackageRevisionInput:
    """Validate create inputs before any database mutation."""
    data_origin = _require_enum_value(
        data_origin,
        field_name="data_origin",
        allowed=ev.DATA_ORIGIN_VALUES,
    )
    sensitivity = _require_enum_value(
        sensitivity,
        field_name="sensitivity",
        allowed=ev.SENSITIVITY_VALUES,
    )
    try:
        require_unclassified_sensitivity(sensitivity)
    except ClassifiedAuthorizationInputError as exc:
        raise PackageRevisionValidationError(
            "classified sensitivity is outside product scope",
            error_code=exc.error_code,
        )
    validate_profile_boundaries(
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
    )
    _effective_data_labels(data_origin, sensitivity)
    return CreatePackageRevisionInput(
        parent_revision_id=parent_revision_id,
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
        data_origin=data_origin,
        sensitivity=sensitivity,
    )


def create_request_digest(
    *,
    system_id: uuid.UUID,
    request: CreatePackageRevisionInput,
) -> str:
    """Return the normalized request digest for revision creation."""
    return request_digest_from_payload(
        {
            "system_id": format_uuid(system_id),
            "parent_revision_id": (
                None
                if request.parent_revision_id is None
                else format_uuid(request.parent_revision_id)
            ),
            "profile_id": request.profile_id,
            "certification_class": request.certification_class,
            "impact_level": request.impact_level,
            "data_origin": request.data_origin,
            "sensitivity": request.sensitivity,
        }
    )


def finalize_request_digest(*, package_revision_id: uuid.UUID) -> str:
    """Return the normalized request digest for upload finalization."""
    return request_digest_from_payload(
        {"package_revision_id": format_uuid(package_revision_id)}
    )


def confirm_request_digest(
    *,
    package_revision_id: uuid.UUID,
    if_match: str,
) -> str:
    """Return the normalized request digest for revision confirmation."""
    return request_digest_from_payload(
        {
            "package_revision_id": format_uuid(package_revision_id),
            "if_match": if_match,
        }
    )


def _mutation_result_from_domain(
    payload: dict[str, Any],
    *,
    status: int,
    replayed: bool,
) -> PackageRevisionMutationResult:
    revision_version = payload["revision_version"]
    return PackageRevisionMutationResult(
        payload=payload,
        status=status,
        etag=format_package_revision_etag(revision_version),
        replayed=replayed,
    )


def _mutation_result_from_replay(
    replay: IdempotencyReplay,
) -> PackageRevisionMutationResult:
    payload = dict(replay.response_body)
    etag = replay_etag_from_outcome(
        response_body=replay.response_body,
        response_headers=replay.response_headers,
    )
    if etag is None:
        etag = format_package_revision_etag(payload["revision_version"])
    return PackageRevisionMutationResult(
        payload=payload,
        status=replay.response_status,
        etag=etag,
        replayed=True,
    )


def _manifest_source_entries(artifacts: list[Any]) -> tuple[ManifestSourceEntry, ...]:
    return tuple(
        ManifestSourceEntry(
            artifact_id=format_uuid(artifact.artifact_id),
            storage_key=artifact.storage_key,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
        )
        for artifact in artifacts
    )


def _map_storage_error(exc: ContentManifestError) -> PackageRevisionStorageError:
    if isinstance(exc, ContentManifestValidationError):
        return PackageRevisionStorageError(
            str(exc),
            error_code="request_schema_invalid",
            retryable=False,
        )
    if isinstance(exc, ContentManifestBlobError):
        return PackageRevisionStorageError(
            str(exc),
            error_code="artifact_digest_mismatch",
            retryable=False,
        )
    if isinstance(exc, ContentManifestConflictError):
        return PackageRevisionStorageError(
            str(exc),
            error_code="state_artifact_inconsistent",
            retryable=False,
        )
    if isinstance(exc, ContentManifestCommitError):
        return PackageRevisionStorageError(str(exc))
    return PackageRevisionStorageError(str(exc))


async def get_package_revision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
) -> dict[str, Any]:
    """Load one package revision after read authorization."""
    result = await session.execute(
        _load_package_revision_with_system_statement(package_revision_id)
    )
    row = result.one_or_none()
    if row is None:
        raise PackageRevisionNotFoundError(package_revision_id=package_revision_id)

    package_revision, system = row
    require_package_role(principal, system=system, revision=package_revision, role=ROLE_VIEWER)
    return map_package_revision_to_domain(package_revision)


async def list_package_revisions(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    system_id: uuid.UUID,
    cursor: str | None = None,
    limit: int | None = None,
) -> PackageRevisionListResult:
    """List package revisions for a system using stable cursor pagination."""
    system_result = await session.execute(_load_system_statement(system_id))
    system = system_result.scalar_one_or_none()
    if system is None:
        raise SystemNotFoundError(system_id=system_id)

    require_package_role(principal, system=system, role=ROLE_VIEWER)

    decoded_cursor = (
        None if cursor is None else decode_pagination_cursor(cursor)
    )
    page_limit = validate_page_limit(limit)
    result = await session.execute(
        _list_package_revisions_statement(
            system_id=system_id,
            cursor=decoded_cursor,
            limit=page_limit,
        )
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > page_limit
    page_rows = rows[:page_limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_pagination_cursor(last.created_at, last.package_revision_id)

    return PackageRevisionListResult(
        items=tuple(map_package_revision_to_domain(row) for row in page_rows),
        next_cursor=next_cursor,
    )


async def create_package_revision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    system_id: uuid.UUID,
    request: CreatePackageRevisionInput,
    authority_manifest_id: str,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> PackageRevisionMutationResult:
    """Create a package revision without committing the caller transaction."""
    from ato_service.db.models import PackageRevision

    validated_now = _require_aware_utc(now, field_name="now")
    validated_request = validate_create_input(
        parent_revision_id=request.parent_revision_id,
        profile_id=request.profile_id,
        certification_class=request.certification_class,
        impact_level=request.impact_level,
        data_origin=request.data_origin,
        sensitivity=request.sensitivity,
    )
    if not authority_manifest_id:
        raise PackageRevisionValidationError("authority_manifest_id is required")

    request_digest = create_request_digest(
        system_id=system_id,
        request=validated_request,
    )

    system_result = await session.execute(_load_system_for_update_statement(system_id))
    system = system_result.scalar_one_or_none()
    if system is None:
        raise SystemNotFoundError(system_id=system_id)

    require_any_package_role(
        principal,
        system=system,
        roles=(ROLE_SYSTEM_OWNER, ROLE_ISSO),
    )

    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_CREATE,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return _mutation_result_from_replay(replay)

    parent_revision_id = validated_request.parent_revision_id
    if parent_revision_id is not None:
        parent_result = await session.execute(
            _load_parent_revision_statement(parent_revision_id)
        )
        parent_revision = parent_result.scalar_one_or_none()
        if parent_revision is None or parent_revision.system_id != system_id:
            raise ParentRevisionNotFoundError(parent_revision_id=parent_revision_id)

    package_revision_id = uuid.uuid4()
    package_revision = PackageRevision(
        package_revision_id=package_revision_id,
        system_id=system_id,
        parent_revision_id=parent_revision_id,
        profile_id=validated_request.profile_id,
        certification_class=validated_request.certification_class,
        impact_level=validated_request.impact_level,
        data_origin=validated_request.data_origin,
        sensitivity=validated_request.sensitivity,
        effective_data_labels=_effective_data_labels(
            validated_request.data_origin,
            validated_request.sensitivity,
        ),
        authority_manifest_id=authority_manifest_id,
        content_manifest_sha256=None,
        revision_version=1,
        status=PackageRevisionStatus.UPLOADING.value,
        created_by=principal.actor_id,
        created_at=validated_now,
    )
    session.add(package_revision)

    payload = map_package_revision_to_domain(package_revision)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="package_revision.created",
        object_type="package_revision",
        object_id=format_uuid(package_revision_id),
        outcome="succeeded",
        reason_code=None,
        metadata={"revision_version": package_revision.revision_version},
        occurred_at=validated_now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_CREATE,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=HTTP_CREATED,
        response_body=payload,
        response_headers={"ETag": format_package_revision_etag(package_revision.revision_version)},
        now=validated_now,
    )
    return _mutation_result_from_domain(payload, status=HTTP_CREATED, replayed=False)


async def finalize_package_revision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    idempotency_key: str,
    hmac_key: bytes,
    storage_root: Path,
    project_root: Path,
    limits: RuntimeLimits,
    now: datetime,
    schema_path: Path | None = None,
) -> PackageRevisionMutationResult:
    """Finalize upload by writing a durable manifest then transitioning to scanning."""
    validated_now = _require_aware_utc(now, field_name="now")
    request_digest = finalize_request_digest(package_revision_id=package_revision_id)

    revision_result = await session.execute(
        _load_package_revision_for_update_statement(package_revision_id)
    )
    package_revision = revision_result.scalar_one_or_none()
    if package_revision is None:
        raise PackageRevisionNotFoundError(package_revision_id=package_revision_id)

    system_result = await session.execute(
        _load_system_for_update_statement(package_revision.system_id)
    )
    system = system_result.scalar_one()
    require_any_package_role(
        principal,
        system=system,
        revision=package_revision,
        roles=(ROLE_SYSTEM_OWNER, ROLE_ISSO),
    )

    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_FINALIZE,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return _mutation_result_from_replay(replay)

    current_status = PackageRevisionStatus(package_revision.status)
    if current_status is not PackageRevisionStatus.UPLOADING:
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state=package_revision.status,
            target_state=PackageRevisionStatus.SCANNING.value,
            condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION.value,
        )

    artifacts_result = await session.execute(
        _load_source_artifacts_statement(package_revision_id)
    )
    artifacts = list(artifacts_result.scalars().all())
    if not artifacts:
        raise EmptyPackageRevisionError()

    source_entries = _manifest_source_entries(artifacts)
    replace_unreferenced_existing = (
        package_revision.content_manifest_sha256 is None
    )
    try:
        stored_manifest = await asyncio.to_thread(
            write_content_manifest,
            format_uuid(package_revision_id),
            source_entries,
            storage_root=storage_root,
            schema_path=schema_path,
            project_root=project_root,
            max_artifacts=limits.max_files_per_revision,
            max_artifact_bytes=limits.max_single_file_bytes,
            max_package_bytes=limits.max_package_bytes,
            replace_unreferenced_existing=replace_unreferenced_existing,
        )
    except ContentManifestError as exc:
        raise _map_storage_error(exc) from exc

    require_package_revision_transition(
        current_status,
        PackageRevisionStatus.SCANNING,
        condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
    )
    package_revision.status = PackageRevisionStatus.SCANNING.value
    package_revision.content_manifest_sha256 = stored_manifest.sha256
    package_revision.revision_version += 1
    bootstrap_malware_scan_work(
        session,
        package_revision_id=package_revision_id,
        expected_revision_version=package_revision.revision_version,
        now=validated_now,
    )

    payload = map_package_revision_to_domain(package_revision)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="package_revision.finalized",
        object_type="package_revision",
        object_id=format_uuid(package_revision_id),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "revision_version": package_revision.revision_version,
            "content_manifest_sha256": stored_manifest.sha256,
        },
        occurred_at=validated_now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_FINALIZE,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=HTTP_ACCEPTED,
        response_body=payload,
        response_headers={"ETag": format_package_revision_etag(package_revision.revision_version)},
        now=validated_now,
    )
    return _mutation_result_from_domain(
        payload,
        status=HTTP_ACCEPTED,
        replayed=False,
    )


async def confirm_package_revision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    if_match: str | None,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
    config: Any | None = None,
    blob_store: Any | None = None,
) -> PackageRevisionMutationResult:
    """Confirm a package revision after proposal review without committing."""
    validated_now = _require_aware_utc(now, field_name="now")
    if if_match is None:
        raise IfMatchRequiredError()

    request_digest = confirm_request_digest(
        package_revision_id=package_revision_id,
        if_match=if_match,
    )

    revision_result = await session.execute(
        _load_package_revision_for_update_statement(package_revision_id)
    )
    package_revision = revision_result.scalar_one_or_none()
    if package_revision is None:
        raise PackageRevisionNotFoundError(package_revision_id=package_revision_id)

    system_result = await session.execute(
        _load_system_for_update_statement(package_revision.system_id)
    )
    system = system_result.scalar_one()
    require_any_package_role(
        principal,
        system=system,
        revision=package_revision,
        roles=(ROLE_SYSTEM_OWNER, ROLE_ISSO),
    )

    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_CONFIRM,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return _mutation_result_from_replay(replay)

    assert_if_match(if_match, package_revision.revision_version)

    current_status = PackageRevisionStatus(package_revision.status)
    if current_status is not PackageRevisionStatus.AWAITING_CONFIRMATION:
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state=package_revision.status,
            target_state=PackageRevisionStatus.READY.value,
            condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION.value,
        )

    draft = await load_draft_for_confirm(
        session,
        package_revision_id=package_revision_id,
    )
    if draft is None:
        pending_result = await session.execute(
            _pending_fact_proposal_exists_statement(package_revision_id)
        )
        if pending_result.scalar_one():
            raise UnconfirmedFactProposalsError()
        confirm_metadata: dict[str, Any] = {
            "revision_version": package_revision.revision_version + 1,
            "confirm_path": "legacy_fact_proposals",
        }
    else:
        content_sha256, snapshot_id = await seal_package_revision_draft(
            session,
            package_revision=package_revision,
            system=system,
            draft=draft,
            principal=principal,
            now=validated_now,
        )
        confirm_metadata = {
            "revision_version": package_revision.revision_version + 1,
            "confirm_path": "package_editor_draft",
            "package_content_sha256": content_sha256,
            "system_context_snapshot_id": format_uuid(snapshot_id),
        }

    require_package_revision_transition(
        current_status,
        PackageRevisionStatus.READY,
        condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
    )
    package_revision.status = PackageRevisionStatus.READY.value
    package_revision.revision_version += 1
    confirm_metadata["revision_version"] = package_revision.revision_version

    if config is not None and blob_store is not None:
        from ato_service.process_capabilities import resolve_process_capabilities
        from ato_service.package_search_index import rebuild_revision_search_index

        capabilities = resolve_process_capabilities(config.document)
        if capabilities is None or capabilities.package_search:
            await rebuild_revision_search_index(
                session,
                package_revision_id=package_revision_id,
                config=config,
                blob_store=blob_store,
                now=validated_now,
            )

    payload = map_package_revision_to_domain(package_revision)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="package_revision.confirmed",
        object_type="package_revision",
        object_id=format_uuid(package_revision_id),
        outcome="succeeded",
        reason_code=None,
        metadata=confirm_metadata,
        occurred_at=validated_now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_CONFIRM,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=HTTP_OK,
        response_body=payload,
        response_headers={"ETag": format_package_revision_etag(package_revision.revision_version)},
        now=validated_now,
    )
    return _mutation_result_from_domain(payload, status=HTTP_OK, replayed=False)
