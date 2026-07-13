"""Review revision persistence and disposition workflow (Component D)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.concurrency import assert_if_match, format_package_revision_etag
from ato_service.domain_mapping import format_uuid
from ato_service.idempotency import (
    IdempotencyReplay,
    load_idempotency_replay,
    record_idempotency_outcome,
    request_digest_from_payload,
)
from ato_service.package_rbac import require_package_role

OPERATION_CREATE = "review_revisions.create"
OPERATION_SUBMIT = "review_revisions.submit"
OPERATION_DISPOSITION = "review_revisions.disposition"


class ReviewRevisionNotFoundError(Exception):
    error_code = "resource_not_found"


class ReviewRevisionValidationError(ValueError):
    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ReviewRevisionMutationResult:
    payload: dict[str, Any]
    status: int
    etag: str
    replayed: bool


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_review_revision_to_domain(
    review_revision: Any,
    *,
    dispositions: list[Any],
) -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "object_type": "review_revision",
        "review_revision_id": format_uuid(review_revision.review_revision_id),
        "run_id": format_uuid(review_revision.run_id),
        "version": review_revision.version,
        "status": review_revision.status,
        "dispositions": [map_disposition_to_domain(item) for item in dispositions],
        "created_by": review_revision.created_by,
        "created_at": _format_utc(review_revision.created_at),
    }


def map_disposition_to_domain(disposition: Any) -> dict[str, Any]:
    return {
        "matrix_row_id": format_uuid(disposition.matrix_row_id),
        "decision": disposition.decision,
        "edited_summary": disposition.edited_summary,
        "notes": disposition.notes,
        "version": disposition.version,
        "decided_by": disposition.decided_by,
        "decided_at": _format_utc(disposition.decided_at),
    }


async def _load_run_context(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
) -> tuple[Any, Any, Any]:
    from ato_service.db.models import AnalysisRun, MatrixRow, PackageRevision, System

    run_result = await session.execute(select(AnalysisRun).where(AnalysisRun.run_id == run_id))
    run = run_result.scalar_one_or_none()
    if run is None:
        raise ReviewRevisionNotFoundError()
    revision_result = await session.execute(
        select(PackageRevision).where(
            PackageRevision.package_revision_id == run.package_revision_id
        )
    )
    revision = revision_result.scalar_one_or_none()
    if revision is None:
        raise ReviewRevisionNotFoundError()
    system_result = await session.execute(
        select(System).where(System.system_id == revision.system_id)
    )
    system = system_result.scalar_one_or_none()
    if system is None:
        raise ReviewRevisionNotFoundError()
    matrix_result = await session.execute(select(MatrixRow).where(MatrixRow.run_id == run_id))
    matrix_rows = matrix_result.scalars().all()
    return run, revision, system, matrix_rows


async def create_review_revision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    run_id: uuid.UUID,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> ReviewRevisionMutationResult:
    from ato_service.db.models import Disposition, ReviewRevision

    run, revision, system, matrix_rows = await _load_run_context(session, run_id=run_id)
    try:
        require_package_role(principal, system=system, revision=revision, role="reviewer")
    except AuthorizationDeniedError:
        raise

    request_digest = request_digest_from_payload({"run_id": str(run_id).lower()})
    replay = await load_idempotency_replay(
        session,
        operation=OPERATION_CREATE,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    if isinstance(replay, IdempotencyReplay):
        return ReviewRevisionMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            etag=replay.response_headers.get("ETag", '"v1"'),
            replayed=True,
        )

    review_revision_id = uuid.uuid4()
    review_revision = ReviewRevision(
        review_revision_id=review_revision_id,
        run_id=run_id,
        version=1,
        status="draft",
        created_by=principal.actor_id,
        created_at=now,
    )
    session.add(review_revision)
    dispositions: list[Disposition] = []
    for matrix_row in matrix_rows:
        disposition = Disposition(
            disposition_id=uuid.uuid4(),
            review_revision_id=review_revision_id,
            matrix_row_id=matrix_row.matrix_row_id,
            decision="pending",
            edited_summary=None,
            notes=None,
            version=1,
            decided_by=principal.actor_id,
            decided_at=now,
        )
        session.add(disposition)
        dispositions.append(disposition)

    payload = map_review_revision_to_domain(review_revision, dispositions=dispositions)
    etag = format_package_revision_etag(review_revision.version)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="review_revision.create",
        object_type="review_revision",
        object_id=str(review_revision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={"run_id": str(run_id).lower()},
        now=now,
    )
    await record_idempotency_outcome(
        session,
        operation=OPERATION_CREATE,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=201,
        response_body=payload,
        response_headers={"ETag": etag},
        now=now,
    )
    return ReviewRevisionMutationResult(payload=payload, status=201, etag=etag, replayed=False)


async def submit_review_revision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    review_revision_id: uuid.UUID,
    if_match: str | None,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> ReviewRevisionMutationResult:
    from ato_service.db.models import Disposition, ReviewRevision

    result = await session.execute(
        select(ReviewRevision).where(ReviewRevision.review_revision_id == review_revision_id)
    )
    review_revision = result.scalar_one_or_none()
    if review_revision is None:
        raise ReviewRevisionNotFoundError()
    run, revision, system, _ = await _load_run_context(session, run_id=review_revision.run_id)
    try:
        require_package_role(principal, system=system, revision=revision, role="reviewer")
    except AuthorizationDeniedError:
        raise
    assert_if_match(if_match=if_match, current_version=review_revision.version)

    disposition_result = await session.execute(
        select(Disposition).where(Disposition.review_revision_id == review_revision_id)
    )
    dispositions = disposition_result.scalars().all()
    if any(item.decision == "pending" for item in dispositions):
        raise ReviewRevisionValidationError(
            "all dispositions must be resolved before submit",
            error_code="review_incomplete",
        )
    if review_revision.status != "draft":
        raise ReviewRevisionValidationError(
            "review revision is not in draft status",
            error_code="illegal_state_transition",
        )

    review_revision.status = "submitted"
    review_revision.version += 1
    payload = map_review_revision_to_domain(review_revision, dispositions=dispositions)
    etag = format_package_revision_etag(review_revision.version)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="review_revision.submit",
        object_type="review_revision",
        object_id=str(review_revision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={"run_id": str(run.run_id).lower()},
        now=now,
    )
    return ReviewRevisionMutationResult(payload=payload, status=200, etag=etag, replayed=False)


async def update_disposition(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    review_revision_id: uuid.UUID,
    matrix_row_id: uuid.UUID,
    decision: str,
    edited_summary: str | None,
    notes: str | None,
    if_match: str | None,
    hmac_key: bytes,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    from ato_service.db.models import Disposition, ReviewRevision

    review_result = await session.execute(
        select(ReviewRevision).where(ReviewRevision.review_revision_id == review_revision_id)
    )
    review_revision = review_result.scalar_one_or_none()
    if review_revision is None:
        raise ReviewRevisionNotFoundError()
    run, revision, system, _ = await _load_run_context(session, run_id=review_revision.run_id)
    try:
        require_package_role(principal, system=system, revision=revision, role="reviewer")
    except AuthorizationDeniedError:
        raise
    assert_if_match(if_match=if_match, current_version=review_revision.version)

    disposition_result = await session.execute(
        select(Disposition).where(
            Disposition.review_revision_id == review_revision_id,
            Disposition.matrix_row_id == matrix_row_id,
        )
    )
    disposition = disposition_result.scalar_one_or_none()
    if disposition is None:
        raise ReviewRevisionNotFoundError()

    disposition.decision = decision
    disposition.edited_summary = edited_summary
    disposition.notes = notes
    disposition.version += 1
    disposition.decided_by = principal.actor_id
    disposition.decided_at = now
    review_revision.version += 1

    payload = map_disposition_to_domain(disposition)
    etag = format_package_revision_etag(review_revision.version)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="review_revision.disposition",
        object_type="disposition",
        object_id=str(disposition.disposition_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={"decision": decision},
        now=now,
    )
    return payload, etag
