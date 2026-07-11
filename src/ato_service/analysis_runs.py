"""Transactional AnalysisRun persistence and HTTP-facing mutations."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.analysis_profile import (
    analysis_profile_sha256,
    expected_assessment_item_ids,
    load_pinned_fisma_synthetic_profile,
)
from ato_service.audit import append_audit_event
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    require_system_mutation_access,
    require_system_read_access,
)
from ato_service.db.models import AnalysisRun, Job, MatrixRow, PackageRevision, System
from ato_service.domain_mapping import (
    format_uuid,
    map_analysis_run_to_domain,
    map_matrix_row_to_domain,
)
from ato_service.idempotency import (
    IdempotencyReplay,
    load_idempotency_replay,
    record_idempotency_outcome,
    request_digest_from_payload,
)
from ato_service.lifecycle_transitions import (
    AnalysisRunStatus,
    AnalysisRunTransitionCondition,
    IllegalStateTransitionError,
    analysis_run_status_is_terminal,
    require_analysis_run_transition,
)
from ato_service.matrix_coverage import require_exact_matrix_coverage
from ato_service.package_revisions import PackageRevisionNotFoundError
from ato_service.pagination import (
    InvalidPaginationCursorError,
    PaginationCursor,
    decode_pagination_cursor,
    encode_pagination_cursor,
    validate_page_limit,
)
from ato_service.run_fingerprints import (
    compute_config_fingerprint,
    model_profile_for_run_type,
    prompt_bundle_sha256_for_run_type,
)
from ato_service.runtime_config import RuntimeConfig

from ato_service.deterministic_analyzer import DETERMINISTIC_STEP_KEY

OPERATION_START = "analysis_runs.start"
OPERATION_CANCEL = "analysis_runs.cancel"

HTTP_ACCEPTED = 202

_RUN_CURSOR_VERSION = 1
_MATRIX_CURSOR_VERSION = 1
_MAX_CURSOR_LENGTH = 2048
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_ASSESSMENT_ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9()._-]{1,127}$")


class AnalysisRunNotFoundError(Exception):
    """Raised when an analysis run cannot be loaded for the caller."""

    error_code = "resource_not_found"

    def __init__(self, *, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        super().__init__("requested resource was not found")


class AnalysisRunPolicyError(Exception):
    """Raised when analysis run policy denies the requested operation."""

    def __init__(self, *, error_code: str) -> None:
        self.error_code = error_code
        super().__init__("analysis run policy denied")


class AnalysisRunValidationError(ValueError):
    """Raised when analysis run inputs fail validation."""

    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class StartRunInput:
    run_type: str
    parent_run_id: uuid.UUID | None
    assessment_item_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisRunMutationResult:
    payload: dict[str, Any]
    status: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class AnalysisRunsPage:
    items: list[dict[str, Any]]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MatrixRowsPage:
    items: list[dict[str, Any]]
    next_cursor: str | None
    total: int


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _load_package_revision_with_system_statement(
    package_revision_id: uuid.UUID,
) -> Any:
    return (
        select(PackageRevision, System)
        .join(System, System.system_id == PackageRevision.system_id)
        .where(PackageRevision.package_revision_id == package_revision_id)
    )


def _load_run_with_system_statement(run_id: uuid.UUID) -> Any:
    return (
        select(AnalysisRun, PackageRevision, System)
        .join(
            PackageRevision,
            PackageRevision.package_revision_id == AnalysisRun.package_revision_id,
        )
        .join(System, System.system_id == PackageRevision.system_id)
        .where(AnalysisRun.run_id == run_id)
    )


def _load_run_for_update_statement(run_id: uuid.UUID) -> Any:
    return (
        select(AnalysisRun)
        .where(AnalysisRun.run_id == run_id)
        .with_for_update()
    )


def _encode_run_cursor(*, requested_at: datetime, run_id: uuid.UUID) -> str:
    payload = {
        "v": _RUN_CURSOR_VERSION,
        "ra": requested_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "id": format_uuid(run_id),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    if len(encoded) > _MAX_CURSOR_LENGTH:
        raise InvalidPaginationCursorError()
    return encoded


def _decode_run_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH or not _CURSOR_PATTERN.fullmatch(cursor):
        raise InvalidPaginationCursorError()
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidPaginationCursorError() from exc
    if payload.get("v") != _RUN_CURSOR_VERSION:
        raise InvalidPaginationCursorError()
    requested_at_raw = payload.get("ra")
    run_id_raw = payload.get("id")
    if not isinstance(requested_at_raw, str) or not isinstance(run_id_raw, str):
        raise InvalidPaginationCursorError()
    requested_at = datetime.fromisoformat(requested_at_raw.replace("Z", "+00:00"))
    return requested_at, uuid.UUID(run_id_raw)


def _encode_matrix_cursor(*, assessment_item_id: str) -> str:
    payload = {"v": _MATRIX_CURSOR_VERSION, "ai": assessment_item_id}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    if len(encoded) > _MAX_CURSOR_LENGTH:
        raise InvalidPaginationCursorError()
    return encoded


def _decode_matrix_cursor(cursor: str) -> str:
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH or not _CURSOR_PATTERN.fullmatch(cursor):
        raise InvalidPaginationCursorError()
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidPaginationCursorError() from exc
    if payload.get("v") != _MATRIX_CURSOR_VERSION:
        raise InvalidPaginationCursorError()
    assessment_item_id = payload.get("ai")
    if not isinstance(assessment_item_id, str):
        raise InvalidPaginationCursorError()
    return assessment_item_id


def _validate_assessment_item_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or _ASSESSMENT_ITEM_ID_PATTERN.fullmatch(value) is None:
            raise AnalysisRunValidationError("assessment_item_ids contain invalid identifiers")
        if value in seen:
            raise AnalysisRunValidationError("assessment_item_ids must be unique")
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _resolve_requested_assessment_item_ids(
    *,
    requested_ids: tuple[str, ...],
    profile: dict[str, Any],
) -> tuple[str, ...]:
    profile_ids = expected_assessment_item_ids(profile)
    if not requested_ids:
        return profile_ids
    validated = _validate_assessment_item_ids(requested_ids)
    require_exact_matrix_coverage(profile_ids, validated)
    return validated


def _assert_deterministic_run_gate(
    *,
    config: RuntimeConfig,
    package_revision: PackageRevision,
    run_type: str,
) -> None:
    if config.runtime_profile != "dev_local":
        raise AnalysisRunPolicyError(error_code="prohibited_model_action")
    if package_revision.data_origin != "synthetic":
        raise AnalysisRunPolicyError(error_code="model_routing_denied")
    if package_revision.status != "ready":
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state=package_revision.status,
            target_state="analysis_run",
        )
    if run_type != "deterministic_only":
        raise AnalysisRunPolicyError(error_code="prohibited_model_action")


async def start_run(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    request: StartRunInput,
    config: RuntimeConfig,
    authority_manifest_id: str,
    project_root: Path,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> AnalysisRunMutationResult:
    """Create a queued deterministic analysis run and durable analyzer job."""
    validated_now = _require_aware_utc(now, field_name="now")
    request_digest = request_digest_from_payload(
        {
            "package_revision_id": format_uuid(package_revision_id),
            "run_type": request.run_type,
            "parent_run_id": (
                None
                if request.parent_run_id is None
                else format_uuid(request.parent_run_id)
            ),
            "assessment_item_ids": list(request.assessment_item_ids),
        }
    )
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_START,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return AnalysisRunMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            replayed=True,
        )

    result = await session.execute(
        _load_package_revision_with_system_statement(package_revision_id)
    )
    row = result.one_or_none()
    if row is None:
        raise PackageRevisionNotFoundError(package_revision_id=package_revision_id)
    package_revision, system = row
    require_system_mutation_access(principal, system)

    _assert_deterministic_run_gate(
        config=config,
        package_revision=package_revision,
        run_type=request.run_type,
    )
    if request.parent_run_id is not None:
        raise AnalysisRunPolicyError(error_code="prohibited_model_action")

    profile = load_pinned_fisma_synthetic_profile(project_root=project_root)
    if package_revision.profile_id != profile["profile_id"]:
        raise AnalysisRunValidationError(
            "pinned analysis profile does not match package revision profile",
            error_code="request_schema_invalid",
        )
    resolved_item_ids = _resolve_requested_assessment_item_ids(
        requested_ids=request.assessment_item_ids,
        profile=profile,
    )
    profile_digest = analysis_profile_sha256(profile)

    run_id = uuid.uuid4()
    analysis_run = AnalysisRun(
        run_id=run_id,
        package_revision_id=package_revision_id,
        parent_run_id=None,
        run_type=request.run_type,
        status=AnalysisRunStatus.QUEUED.value,
        requested_by=principal.actor_id,
        requested_at=validated_now,
        started_at=None,
        completed_at=None,
        authority_manifest_id=authority_manifest_id,
        analysis_profile_sha256=profile_digest,
        config_fingerprint=compute_config_fingerprint(config),
        prompt_bundle_sha256=prompt_bundle_sha256_for_run_type(request.run_type),
        model_profile=model_profile_for_run_type(request.run_type),
        artifact_manifest_sha256=None,
        llm_call_count=0,
        assessment_item_ids=list(resolved_item_ids),
        error_code=None,
        error_retryable=None,
    )
    job = Job(
        job_id=uuid.uuid4(),
        run_id=run_id,
        step_key=DETERMINISTIC_STEP_KEY,
        step_idempotent=True,
        status="available",
        attempt_count=0,
        available_at=validated_now,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        last_error_code=None,
    )
    session.add_all([analysis_run, job])
    payload = map_analysis_run_to_domain(analysis_run)

    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="analysis_run.started",
        object_type="analysis_run",
        object_id=format_uuid(run_id),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "package_revision_id": format_uuid(package_revision_id),
            "run_type": request.run_type,
            "llm_call_count": 0,
        },
        now=validated_now,
    )

    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_START,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=HTTP_ACCEPTED,
        response_body=payload,
        now=validated_now,
    )
    return AnalysisRunMutationResult(
        payload=payload,
        status=HTTP_ACCEPTED,
        replayed=False,
    )


async def list_runs(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
) -> AnalysisRunsPage:
    """List analysis runs for one package revision."""
    page_limit = validate_page_limit(limit)
    result = await session.execute(
        _load_package_revision_with_system_statement(package_revision_id)
    )
    row = result.one_or_none()
    if row is None:
        raise PackageRevisionNotFoundError(package_revision_id=package_revision_id)
    _, system = row
    require_system_read_access(principal, system)

    decoded_cursor: tuple[datetime, uuid.UUID] | None = None
    if cursor is not None:
        requested_at, run_id = _decode_run_cursor(cursor)
        decoded_cursor = (requested_at, run_id)

    statement = (
        select(AnalysisRun)
        .where(AnalysisRun.package_revision_id == package_revision_id)
        .order_by(AnalysisRun.requested_at.asc(), AnalysisRun.run_id.asc())
        .limit(page_limit + 1)
    )
    if decoded_cursor is not None:
        requested_at, run_id = decoded_cursor
        statement = statement.where(
            tuple_(AnalysisRun.requested_at, AnalysisRun.run_id)
            > tuple_(requested_at, run_id)
        )

    runs = list((await session.execute(statement)).scalars())
    next_cursor = None
    if len(runs) > page_limit:
        last = runs[page_limit - 1]
        next_cursor = _encode_run_cursor(
            requested_at=last.requested_at,
            run_id=last.run_id,
        )
        runs = runs[:page_limit]

    return AnalysisRunsPage(
        items=[map_analysis_run_to_domain(run) for run in runs],
        next_cursor=next_cursor,
    )


async def get_run(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Return one analysis run when the caller can read the owning system."""
    result = await session.execute(_load_run_with_system_statement(run_id))
    row = result.one_or_none()
    if row is None:
        raise AnalysisRunNotFoundError(run_id=run_id)
    analysis_run, _, system = row
    require_system_read_access(principal, system)
    return map_analysis_run_to_domain(analysis_run)


async def cancel_run(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    run_id: uuid.UUID,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> AnalysisRunMutationResult:
    """Cancel a queued or running analysis run."""
    validated_now = _require_aware_utc(now, field_name="now")
    request_digest = request_digest_from_payload({"run_id": format_uuid(run_id)})
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_CANCEL,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return AnalysisRunMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            replayed=True,
        )

    result = await session.execute(_load_run_with_system_statement(run_id))
    row = result.one_or_none()
    if row is None:
        raise AnalysisRunNotFoundError(run_id=run_id)
    analysis_run, _, system = row
    require_system_mutation_access(principal, system)

    locked = await session.execute(_load_run_for_update_statement(run_id))
    analysis_run = locked.scalar_one()

    current_status = AnalysisRunStatus(analysis_run.status)
    if analysis_run_status_is_terminal(current_status):
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state=analysis_run.status,
            target_state=AnalysisRunStatus.CANCELLED.value,
        )

    require_analysis_run_transition(
        current_status,
        AnalysisRunStatus.CANCELLED,
        condition=AnalysisRunTransitionCondition.AUTHORIZED_CANCELLATION,
    )
    analysis_run.status = AnalysisRunStatus.CANCELLED.value
    analysis_run.completed_at = validated_now
    analysis_run.error_code = None
    analysis_run.error_retryable = None

    payload = map_analysis_run_to_domain(analysis_run)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="analysis_run.cancelled",
        object_type="analysis_run",
        object_id=format_uuid(run_id),
        outcome="succeeded",
        reason_code=None,
        metadata={"status": AnalysisRunStatus.CANCELLED.value},
        now=validated_now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_CANCEL,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=HTTP_ACCEPTED,
        response_body=payload,
        now=validated_now,
    )
    return AnalysisRunMutationResult(
        payload=payload,
        status=HTTP_ACCEPTED,
        replayed=False,
    )


async def get_run_matrix(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    run_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
    status: str | None,
) -> MatrixRowsPage:
    """Return paginated matrix rows for one analysis run."""
    page_limit = validate_page_limit(limit)
    result = await session.execute(_load_run_with_system_statement(run_id))
    row = result.one_or_none()
    if row is None:
        raise AnalysisRunNotFoundError(run_id=run_id)
    analysis_run, _, system = row
    require_system_read_access(principal, system)

    filters = [MatrixRow.run_id == run_id]
    if status is not None:
        filters.append(
            or_(
                MatrixRow.model_proposed_status == status,
                MatrixRow.system_status == status,
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(MatrixRow).where(*filters)
    )

    decoded_item_id: str | None = None
    if cursor is not None:
        decoded_item_id = _decode_matrix_cursor(cursor)

    statement = (
        select(MatrixRow)
        .where(*filters)
        .order_by(MatrixRow.assessment_item_id.asc())
        .limit(page_limit + 1)
    )
    if decoded_item_id is not None:
        statement = statement.where(MatrixRow.assessment_item_id > decoded_item_id)

    rows = list((await session.execute(statement)).scalars())
    next_cursor = None
    if len(rows) > page_limit:
        last = rows[page_limit - 1]
        next_cursor = _encode_matrix_cursor(assessment_item_id=last.assessment_item_id)
        rows = rows[:page_limit]

    return MatrixRowsPage(
        items=[map_matrix_row_to_domain(item) for item in rows],
        next_cursor=next_cursor,
        total=int(total or 0),
    )
