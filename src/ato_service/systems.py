"""Authorized Systems persistence for P1.1 package routes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    require_system_mutation_access,
    require_system_read_access,
)
from ato_service.db.models import System
from ato_service.domain_mapping import map_system_to_domain
from ato_service.idempotency import (
    load_idempotency_replay,
    record_idempotency_outcome,
    request_digest_from_payload,
)
from ato_service.pagination import (
    PaginationCursor,
    decode_pagination_cursor,
    encode_pagination_cursor,
    validate_page_limit,
)

SYSTEMS_CREATE_OPERATION = "systems.create"
SYSTEMS_ARCHIVE_OPERATION = "systems.archive"

MAX_DISPLAY_NAME_LENGTH = 255
MIN_DISPLAY_NAME_LENGTH = 1
MAX_GROUP_ID_LENGTH = 255
MIN_GROUP_ID_LENGTH = 1
MAX_EXTERNAL_SYSTEM_ID_LENGTH = 255
MAX_VIEWER_GROUPS = 100


class ResourceNotFoundError(Exception):
    """Raised when a requested system does not exist."""

    error_code = "resource_not_found"


class RequestSchemaInvalidError(Exception):
    """Raised when create-system inputs fail domain/OpenAPI validation."""

    error_code = "request_schema_invalid"

    def __init__(self, field_errors: list[FieldValidationError]) -> None:
        self.field_errors = field_errors
        super().__init__("request schema invalid")


@dataclass(frozen=True, slots=True)
class FieldValidationError:
    path: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CreateSystemResult:
    payload: dict[str, Any]
    status: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ArchiveSystemResult:
    payload: dict[str, Any]
    status: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class SystemsPage:
    items: list[dict[str, Any]]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _ProspectiveSystem:
    owner_group: str
    viewer_groups: list[str]


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _normalize_group_id(value: str, *, path: str, errors: list[FieldValidationError]) -> str | None:
    if not isinstance(value, str):
        errors.append(
            FieldValidationError(path=path, code="type", message="must be a string")
        )
        return None
    normalized = value.strip()
    if len(normalized) < MIN_GROUP_ID_LENGTH:
        errors.append(
            FieldValidationError(
                path=path,
                code="min_length",
                message="must be at least 1 character",
            )
        )
        return None
    if len(normalized) > MAX_GROUP_ID_LENGTH:
        errors.append(
            FieldValidationError(
                path=path,
                code="max_length",
                message="must be at most 255 characters",
            )
        )
        return None
    return normalized


def _validate_display_name(value: str, errors: list[FieldValidationError]) -> str | None:
    if not isinstance(value, str):
        errors.append(
            FieldValidationError(
                path="display_name",
                code="type",
                message="must be a string",
            )
        )
        return None
    if len(value) < MIN_DISPLAY_NAME_LENGTH:
        errors.append(
            FieldValidationError(
                path="display_name",
                code="min_length",
                message="must be at least 1 character",
            )
        )
        return None
    if len(value) > MAX_DISPLAY_NAME_LENGTH:
        errors.append(
            FieldValidationError(
                path="display_name",
                code="max_length",
                message="must be at most 255 characters",
            )
        )
        return None
    return value


def _validate_external_system_id(
    value: str | None,
    errors: list[FieldValidationError],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(
            FieldValidationError(
                path="external_system_id",
                code="type",
                message="must be a string or null",
            )
        )
        return None
    if len(value) > MAX_EXTERNAL_SYSTEM_ID_LENGTH:
        errors.append(
            FieldValidationError(
                path="external_system_id",
                code="max_length",
                message="must be at most 255 characters",
            )
        )
        return None
    return value


def _validate_viewer_groups(
    value: Any,
    errors: list[FieldValidationError],
) -> list[str] | None:
    if not isinstance(value, list):
        errors.append(
            FieldValidationError(
                path="viewer_groups",
                code="type",
                message="must be an array",
            )
        )
        return None
    if len(value) > MAX_VIEWER_GROUPS:
        errors.append(
            FieldValidationError(
                path="viewer_groups",
                code="max_items",
                message="must contain at most 100 items",
            )
        )
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw_group in enumerate(value):
        group = _normalize_group_id(
            raw_group,
            path=f"viewer_groups[{index}]",
            errors=errors,
        )
        if group is None:
            continue
        if group in seen:
            errors.append(
                FieldValidationError(
                    path=f"viewer_groups[{index}]",
                    code="unique_items",
                    message="duplicate group id",
                )
            )
            continue
        seen.add(group)
        normalized.append(group)
    return normalized


def _validate_create_system_inputs(
    *,
    display_name: str,
    external_system_id: str | None,
    owner_group: str,
    viewer_groups: list[str],
) -> tuple[str, str | None, str, list[str]]:
    errors: list[FieldValidationError] = []

    validated_display_name = _validate_display_name(display_name, errors)
    validated_external_system_id = _validate_external_system_id(
        external_system_id,
        errors,
    )
    validated_owner_group = _normalize_group_id(
        owner_group,
        path="owner_group",
        errors=errors,
    )
    validated_viewer_groups = _validate_viewer_groups(viewer_groups, errors)

    if errors:
        raise RequestSchemaInvalidError(errors)

    assert validated_display_name is not None
    assert validated_owner_group is not None
    assert validated_viewer_groups is not None
    return (
        validated_display_name,
        validated_external_system_id,
        validated_owner_group,
        validated_viewer_groups,
    )


def create_system_request_digest_payload(
    *,
    display_name: str,
    external_system_id: str | None,
    owner_group: str,
    viewer_groups: list[str],
) -> dict[str, Any]:
    """Return the replay-safe request digest payload excluding secret/session/csrf."""
    return {
        "display_name": display_name,
        "external_system_id": external_system_id,
        "owner_group": owner_group,
        "viewer_groups": viewer_groups,
    }


def archive_system_request_digest_payload(*, system_id: uuid.UUID) -> dict[str, Any]:
    """Return the replay-safe archive request digest payload."""
    return {"system_id": str(system_id).lower()}


def _system_read_access_predicate(principal_groups: tuple[str, ...]) -> Any:
    if not principal_groups:
        return false()
    conditions: list[Any] = [System.owner_group.in_(principal_groups)]
    for group in principal_groups:
        conditions.append(System.viewer_groups.contains([group]))
    return or_(*conditions)


def _list_systems_select_statement(
    *,
    principal_groups: tuple[str, ...],
    cursor: PaginationCursor | None,
    limit: int,
    include_archived: bool,
) -> Any:
    filters: list[Any] = [_system_read_access_predicate(principal_groups)]
    if not include_archived:
        filters.append(System.archived_at.is_(None))

    statement = (
        select(System)
        .where(*filters)
        .order_by(System.created_at.asc(), System.system_id.asc())
        .limit(limit + 1)
    )
    if cursor is None:
        return statement

    return statement.where(
        or_(
            System.created_at > cursor.created_at,
            and_(
                System.created_at == cursor.created_at,
                System.system_id > cursor.item_id,
            ),
        )
    )


async def create_system(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    audit_hmac_key: bytes,
    idempotency_key: str,
    display_name: str,
    external_system_id: str | None,
    owner_group: str,
    viewer_groups: list[str],
    customer_enterprise_id: str,
    now: datetime,
) -> CreateSystemResult:
    """Create a system with replay-safe idempotency inside the caller's transaction."""
    validated_now = _require_aware_utc(now, field_name="now")
    (
        validated_display_name,
        validated_external_system_id,
        validated_owner_group,
        validated_viewer_groups,
    ) = _validate_create_system_inputs(
        display_name=display_name,
        external_system_id=external_system_id,
        owner_group=owner_group,
        viewer_groups=viewer_groups,
    )

    require_system_mutation_access(
        principal,
        _ProspectiveSystem(
            owner_group=validated_owner_group,
            viewer_groups=validated_viewer_groups,
        ),
    )

    digest_payload = create_system_request_digest_payload(
        display_name=validated_display_name,
        external_system_id=validated_external_system_id,
        owner_group=validated_owner_group,
        viewer_groups=validated_viewer_groups,
    )
    request_digest = request_digest_from_payload(digest_payload)

    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        SYSTEMS_CREATE_OPERATION,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return CreateSystemResult(
            payload=dict(replay.response_body),
            status=replay.response_status,
            replayed=True,
        )

    system_id = uuid.uuid4()
    system = System(
        system_id=system_id,
        display_name=validated_display_name,
        external_system_id=validated_external_system_id,
        customer_enterprise_id=customer_enterprise_id,
        owner_group=validated_owner_group,
        viewer_groups=validated_viewer_groups,
        created_at=validated_now,
        archived_at=None,
    )
    session.add(system)

    payload = map_system_to_domain(system)

    await append_audit_event(
        session,
        hmac_key=audit_hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="system.created",
        object_type="system",
        object_id=str(system_id),
        outcome="succeeded",
        reason_code=None,
        metadata={},
        occurred_at=validated_now,
    )

    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=SYSTEMS_CREATE_OPERATION,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=201,
        response_body=payload,
        response_headers={},
        now=validated_now,
    )

    return CreateSystemResult(payload=payload, status=201, replayed=False)


async def get_system(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    system_id: uuid.UUID,
) -> System:
    """Load one system when the principal has read access."""
    result = await session.execute(
        select(System).where(System.system_id == system_id)
    )
    system = result.scalar_one_or_none()
    if system is None:
        raise ResourceNotFoundError()

    require_system_read_access(principal, system)
    return system


async def archive_system(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    audit_hmac_key: bytes,
    system_id: uuid.UUID,
    idempotency_key: str,
    now: datetime,
) -> ArchiveSystemResult:
    """Soft-archive one system with replay-safe idempotency inside the caller transaction."""
    validated_now = _require_aware_utc(now, field_name="now")
    request_digest = request_digest_from_payload(
        archive_system_request_digest_payload(system_id=system_id)
    )

    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        SYSTEMS_ARCHIVE_OPERATION,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return ArchiveSystemResult(
            payload=dict(replay.response_body),
            status=replay.response_status,
            replayed=True,
        )

    result = await session.execute(
        select(System).where(System.system_id == system_id)
    )
    system = result.scalar_one_or_none()
    if system is None:
        raise ResourceNotFoundError()

    require_system_mutation_access(principal, system)

    payload = map_system_to_domain(system)
    if system.archived_at is None:
        system.archived_at = validated_now
        payload = map_system_to_domain(system)
        await append_audit_event(
            session,
            hmac_key=audit_hmac_key,
            actor_type="user",
            actor_id=principal.actor_id,
            action="system.archived",
            object_type="system",
            object_id=str(system_id),
            outcome="succeeded",
            reason_code=None,
            metadata={},
            occurred_at=validated_now,
        )

    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=SYSTEMS_ARCHIVE_OPERATION,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=200,
        response_body=payload,
        response_headers={},
        now=validated_now,
    )

    return ArchiveSystemResult(payload=payload, status=200, replayed=False)


async def list_systems(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    cursor: str | None,
    limit: int | None,
    include_archived: bool = False,
) -> SystemsPage:
    """List systems visible to the principal using PostgreSQL authorization filters."""
    validated_limit = validate_page_limit(limit)
    decoded_cursor = (
        None if cursor is None else decode_pagination_cursor(cursor)
    )

    result = await session.execute(
        _list_systems_select_statement(
            principal_groups=principal.groups,
            cursor=decoded_cursor,
            limit=validated_limit,
            include_archived=include_archived,
        )
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > validated_limit
    page_rows = rows[:validated_limit]

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_pagination_cursor(last.created_at, last.system_id)

    return SystemsPage(
        items=[map_system_to_domain(row) for row in page_rows],
        next_cursor=next_cursor,
    )
