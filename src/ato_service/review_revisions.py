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
from ato_service.disposition_transitions import (
    DispositionTransitionError,
    require_decision_compatible_with_matrix_status,
)
from ato_service.domain_mapping import format_uuid
from ato_service.idempotency import (
    IdempotencyReplay,
    load_idempotency_replay,
    record_idempotency_outcome,
    request_digest_from_payload,
)
from ato_service.package_rbac import require_package_role
from ato_service.route_role_matrix import ROLE_REVIEWER, ROLE_VIEWER
from ato_service.pagination import (
    InvalidPaginationCursorError,
    PaginationCursor,
    decode_pagination_cursor,
    encode_pagination_cursor,
    validate_page_limit,
)

OPERATION_CREATE = "review_revisions.create"
OPERATION_SUBMIT = "review_revisions.submit"
OPERATION_DISPOSITION = "review_revisions.disposition"
OPERATION_CREATE_COMMENT = "review_revisions.comment.create"

_TERMINAL_REVIEW_STATUSES = frozenset({"submitted", "superseded"})
_RESOLVED_DISPOSITION_DECISIONS = frozenset(
    {
        "accepted",
        "edited",
        "rejected",
        "evidence_requested",
        "weakness_confirmed",
    }
)


class ReviewRevisionNotFoundError(Exception):
    error_code = "resource_not_found"


class ReviewRevisionValidationError(ValueError):
    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class DispositionValidationError(ReviewRevisionValidationError):
    """Raised when a disposition mutation violates routing or transition rules."""


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


def map_review_comment_to_domain(comment: Any) -> dict[str, Any]:
    return {
        "comment_id": format_uuid(comment.review_comment_id),
        "review_revision_id": format_uuid(comment.review_revision_id),
        "matrix_row_id": format_uuid(comment.matrix_row_id) if comment.matrix_row_id is not None else None,
        "body": comment.body,
        "created_by": comment.created_by,
        "created_at": _format_utc(comment.created_at),
    }


def _validate_disposition_decision(*, decision: str, edited_summary: str | None) -> None:
    if decision not in _RESOLVED_DISPOSITION_DECISIONS:
        raise ReviewRevisionValidationError(
            "invalid disposition decision",
            error_code="request_schema_invalid",
        )
    if decision == "edited" and (edited_summary is None or not edited_summary.strip()):
        raise ReviewRevisionValidationError(
            "edited disposition requires edited_summary",
            error_code="request_schema_invalid",
        )


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


async def _load_dispositions(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
) -> list[Any]:
    from ato_service.db.models import Disposition

    disposition_result = await session.execute(
        select(Disposition).where(Disposition.review_revision_id == review_revision_id)
    )
    return list(disposition_result.scalars().all())


def _review_mutation_result(
    review_revision: Any,
    *,
    dispositions: list[Any],
    status: int,
) -> ReviewRevisionMutationResult:
    payload = map_review_revision_to_domain(review_revision, dispositions=dispositions)
    etag = format_package_revision_etag(review_revision.version)
    return ReviewRevisionMutationResult(payload=payload, status=status, etag=etag, replayed=False)


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
        require_package_role(principal, system=system, revision=revision, role=ROLE_REVIEWER)
    except AuthorizationDeniedError:
        raise

    existing_draft_result = await session.execute(
        select(ReviewRevision).where(
            ReviewRevision.run_id == run_id,
            ReviewRevision.status == "draft",
        )
    )
    existing_draft = existing_draft_result.scalar_one_or_none()
    if existing_draft is not None:
        dispositions = await _load_dispositions(
            session,
            review_revision_id=existing_draft.review_revision_id,
        )
        return _review_mutation_result(existing_draft, dispositions=dispositions, status=200)

    submitted_result = await session.execute(
        select(ReviewRevision)
        .where(
            ReviewRevision.run_id == run_id,
            ReviewRevision.status == "submitted",
        )
        .limit(1)
    )
    existing_submitted = submitted_result.scalar_one_or_none()
    if existing_submitted is not None:
        dispositions = await _load_dispositions(
            session,
            review_revision_id=existing_submitted.review_revision_id,
        )
        return _review_mutation_result(existing_submitted, dispositions=dispositions, status=200)

    request_digest = request_digest_from_payload({"run_id": str(run_id).lower()})
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_CREATE,
        idempotency_key,
        request_digest,
        now,
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
        occurred_at=now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
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
        require_package_role(principal, system=system, revision=revision, role=ROLE_REVIEWER)
    except AuthorizationDeniedError:
        raise
    assert_if_match(if_match, review_revision.version)

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
        occurred_at=now,
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
    from ato_service.db.models import Disposition, MatrixRow, ReviewRevision
    from ato_service.poam_routing import route_disposition_side_effects

    review_result = await session.execute(
        select(ReviewRevision).where(ReviewRevision.review_revision_id == review_revision_id)
    )
    review_revision = review_result.scalar_one_or_none()
    if review_revision is None:
        raise ReviewRevisionNotFoundError()
    if review_revision.status in _TERMINAL_REVIEW_STATUSES:
        raise ReviewRevisionValidationError(
            "review revision is immutable",
            error_code="illegal_state_transition",
        )
    run, revision, system, _ = await _load_run_context(session, run_id=review_revision.run_id)
    try:
        require_package_role(principal, system=system, revision=revision, role=ROLE_REVIEWER)
    except AuthorizationDeniedError:
        raise
    assert_if_match(if_match, review_revision.version)
    _validate_disposition_decision(decision=decision, edited_summary=edited_summary)

    disposition_result = await session.execute(
        select(Disposition).where(
            Disposition.review_revision_id == review_revision_id,
            Disposition.matrix_row_id == matrix_row_id,
        )
    )
    disposition = disposition_result.scalar_one_or_none()
    if disposition is None:
        raise ReviewRevisionNotFoundError()

    matrix_result = await session.execute(
        select(MatrixRow).where(MatrixRow.matrix_row_id == matrix_row_id)
    )
    matrix_row = matrix_result.scalar_one_or_none()
    if matrix_row is None:
        raise ReviewRevisionNotFoundError()
    try:
        require_decision_compatible_with_matrix_status(
            decision=decision,
            system_status=matrix_row.system_status,
        )
    except DispositionTransitionError as exc:
        raise DispositionValidationError(exc.message, error_code=exc.error_code) from exc

    disposition.decision = decision
    disposition.edited_summary = edited_summary
    disposition.notes = notes
    disposition.version += 1
    disposition.decided_by = principal.actor_id
    disposition.decided_at = now
    review_revision.version += 1

    routing_result = await route_disposition_side_effects(
        session,
        review_revision_id=review_revision_id,
        disposition_id=disposition.disposition_id,
        matrix_row_id=matrix_row_id,
        run_id=review_revision.run_id,
        assessment_item_id=matrix_row.assessment_item_id,
        assessment_item_type=matrix_row.assessment_item_type,
        system_status=matrix_row.system_status,
        finding_summary=matrix_row.finding_summary,
        decision=decision,
        actor_id=principal.actor_id,
        hmac_key=hmac_key,
        now=now,
    )

    payload = map_disposition_to_domain(disposition)
    if routing_result.evidence_request_id is not None:
        payload["evidence_request_id"] = format_uuid(routing_result.evidence_request_id)
    if routing_result.poam_candidate_id is not None:
        payload["poam_candidate_id"] = format_uuid(routing_result.poam_candidate_id)
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
        metadata={
            "decision": decision,
            "evidence_request_created": routing_result.evidence_request_id is not None
            and routing_result.created,
            "poam_candidate_created": routing_result.poam_candidate_id is not None
            and routing_result.created,
        },
        occurred_at=now,
    )
    return payload, etag


async def create_review_comment(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    review_revision_id: uuid.UUID,
    matrix_row_id: uuid.UUID | None,
    body: str,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> tuple[dict[str, Any], int, bool]:
    from ato_service.db.models import MatrixRow, ReviewComment, ReviewRevision

    if not isinstance(body, str) or not body.strip():
        raise ReviewRevisionValidationError(
            "comment body is required",
            error_code="request_schema_invalid",
        )

    review_result = await session.execute(
        select(ReviewRevision).where(ReviewRevision.review_revision_id == review_revision_id)
    )
    review_revision = review_result.scalar_one_or_none()
    if review_revision is None:
        raise ReviewRevisionNotFoundError()
    if review_revision.status in _TERMINAL_REVIEW_STATUSES:
        raise ReviewRevisionValidationError(
            "review revision is immutable",
            error_code="illegal_state_transition",
        )
    run, revision, system, _ = await _load_run_context(session, run_id=review_revision.run_id)
    try:
        require_package_role(principal, system=system, revision=revision, role=ROLE_REVIEWER)
    except AuthorizationDeniedError:
        raise

    if matrix_row_id is not None:
        matrix_result = await session.execute(
            select(MatrixRow).where(
                MatrixRow.run_id == run.run_id,
                MatrixRow.matrix_row_id == matrix_row_id,
            )
        )
        if matrix_result.scalar_one_or_none() is None:
            raise ReviewRevisionNotFoundError()

    request_digest = request_digest_from_payload(
        {
            "review_revision_id": str(review_revision_id).lower(),
            "matrix_row_id": str(matrix_row_id).lower() if matrix_row_id is not None else None,
            "body": body.strip(),
        }
    )
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_CREATE_COMMENT,
        idempotency_key,
        request_digest,
        now,
    )
    if isinstance(replay, IdempotencyReplay):
        return replay.response_body, replay.response_status, True

    comment_id = uuid.uuid4()
    comment = ReviewComment(
        review_comment_id=comment_id,
        review_revision_id=review_revision_id,
        matrix_row_id=matrix_row_id,
        body=body.strip(),
        created_by=principal.actor_id,
        created_at=now,
    )
    session.add(comment)
    payload = map_review_comment_to_domain(comment)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="review_revision.comment",
        object_type="review_comment",
        object_id=str(comment_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={"review_revision_id": str(review_revision_id).lower()},
        occurred_at=now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_CREATE_COMMENT,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=201,
        response_body=payload,
        response_headers={},
        now=now,
    )
    return payload, 201, False


async def list_review_comments(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    review_revision_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    from ato_service.db.models import ReviewComment, ReviewRevision

    review_result = await session.execute(
        select(ReviewRevision).where(ReviewRevision.review_revision_id == review_revision_id)
    )
    review_revision = review_result.scalar_one_or_none()
    if review_revision is None:
        raise ReviewRevisionNotFoundError()
    run, revision, system, _ = await _load_run_context(session, run_id=review_revision.run_id)
    try:
        require_package_role(principal, system=system, revision=revision, role=ROLE_VIEWER)
    except AuthorizationDeniedError:
        raise

    page_limit = validate_page_limit(limit)
    decoded_cursor: PaginationCursor | None = None
    if cursor is not None:
        try:
            decoded_cursor = decode_pagination_cursor(cursor)
        except InvalidPaginationCursorError as exc:
            raise ReviewRevisionValidationError(
                "invalid pagination cursor",
                error_code=exc.error_code,
            ) from exc

    query = (
        select(ReviewComment)
        .where(ReviewComment.review_revision_id == review_revision_id)
        .order_by(ReviewComment.created_at.desc(), ReviewComment.review_comment_id.desc())
        .limit(page_limit + 1)
    )
    if decoded_cursor is not None:
        query = query.where(
            (ReviewComment.created_at < decoded_cursor.created_at)
            | (
                (ReviewComment.created_at == decoded_cursor.created_at)
                & (ReviewComment.review_comment_id < decoded_cursor.item_id)
            )
        )

    result = await session.execute(query)
    comments = list(result.scalars().all())
    next_cursor: str | None = None
    if len(comments) > page_limit:
        last = comments[page_limit - 1]
        next_cursor = encode_pagination_cursor(
            created_at=last.created_at,
            item_id=last.review_comment_id,
        )
        comments = comments[:page_limit]

    return {
        "items": [map_review_comment_to_domain(item) for item in comments],
        "next_cursor": next_cursor,
    }
