"""Fact proposal listing and review for awaiting_confirmation revisions."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.package_rbac import require_any_package_role, require_package_role
from ato_service.route_role_matrix import ROLE_ISSO, ROLE_SYSTEM_OWNER, ROLE_VIEWER
from ato_service.concurrency import (
    IfMatchRequiredError,
    assert_if_match,
    format_package_revision_etag,
)
from ato_service.db.models import FactProposal, PackageRevision, System
from ato_service.domain_mapping import format_uuid, map_fact_proposal_to_domain
from ato_service.lifecycle_transitions import IllegalStateTransitionError
from ato_service.package_revisions import PackageRevisionNotFoundError
from ato_service.pagination import (
    InvalidPaginationCursorError,
    validate_page_limit,
)

PROPOSAL_CURSOR_VERSION = 1
MAX_PROPOSAL_CURSOR_LENGTH = 2048
_PROPOSAL_CURSOR_PATTERN = r"^[A-Za-z0-9_-]+$"

FACT_PROPOSAL_REVIEW_TERMINAL = frozenset({"accepted", "edited", "rejected"})


class FactProposalNotFoundError(Exception):
    """Raised when a fact proposal cannot be loaded for the caller."""

    error_code = "resource_not_found"


class FactProposalReviewConflictError(Exception):
    """Raised when a proposal review is illegal for the current parent state."""

    error_code = "illegal_state_transition"

    def __init__(
        self,
        *,
        current_state: str,
        target_state: str,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        super().__init__("illegal state transition")


@dataclass(frozen=True, slots=True)
class FactProposalsPage:
    items: list[dict[str, Any]]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class FactProposalMutationResult:
    payload: dict[str, Any]
    etag: str


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _load_package_revision_statement(package_revision_id: uuid.UUID) -> Any:
    return select(PackageRevision).where(
        PackageRevision.package_revision_id == package_revision_id
    )


def _load_system_statement(system_id: uuid.UUID) -> Any:
    return select(System).where(System.system_id == system_id)


def _load_proposal_for_update_statement(fact_proposal_id: uuid.UUID) -> Any:
    return (
        select(FactProposal)
        .where(FactProposal.fact_proposal_id == fact_proposal_id)
        .with_for_update()
    )


def _encode_proposal_cursor(*, json_pointer: str, fact_proposal_id: uuid.UUID) -> str:
    payload = {
        "v": PROPOSAL_CURSOR_VERSION,
        "jp": json_pointer,
        "id": format_uuid(fact_proposal_id),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    if len(encoded) > MAX_PROPOSAL_CURSOR_LENGTH:
        raise InvalidPaginationCursorError()
    return encoded


def _decode_proposal_cursor(cursor: str) -> tuple[str, uuid.UUID]:
    if not cursor or len(cursor) > MAX_PROPOSAL_CURSOR_LENGTH:
        raise InvalidPaginationCursorError()
    import re

    if not re.fullmatch(_PROPOSAL_CURSOR_PATTERN, cursor):
        raise InvalidPaginationCursorError()
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii") + b"===")
        payload = json.loads(decoded)
    except (binascii.Error, json.JSONDecodeError, ValueError):
        raise InvalidPaginationCursorError() from None
    if not isinstance(payload, dict):
        raise InvalidPaginationCursorError()
    if payload.get("v") != PROPOSAL_CURSOR_VERSION:
        raise InvalidPaginationCursorError()
    json_pointer = payload.get("jp")
    proposal_id_raw = payload.get("id")
    if not isinstance(json_pointer, str) or not json_pointer:
        raise InvalidPaginationCursorError()
    try:
        proposal_id = uuid.UUID(str(proposal_id_raw))
    except (TypeError, ValueError):
        raise InvalidPaginationCursorError() from None
    return json_pointer, proposal_id


async def _load_revision_and_system(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
) -> tuple[PackageRevision, System]:
    revision_result = await session.execute(
        _load_package_revision_statement(package_revision_id)
    )
    package_revision = revision_result.scalar_one_or_none()
    if package_revision is None:
        raise PackageRevisionNotFoundError(package_revision_id=package_revision_id)
    system_result = await session.execute(
        _load_system_statement(package_revision.system_id)
    )
    system = system_result.scalar_one()
    return package_revision, system


async def list_fact_proposals(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
) -> FactProposalsPage:
    """List fact proposals for an authorized package revision."""
    package_revision, system = await _load_revision_and_system(
        session,
        package_revision_id=package_revision_id,
    )
    require_package_role(
        principal,
        system=system,
        revision=package_revision,
        role=ROLE_VIEWER,
    )
    page_limit = validate_page_limit(limit)

    statement = (
        select(FactProposal)
        .where(FactProposal.package_revision_id == package_revision_id)
        .order_by(FactProposal.json_pointer.asc(), FactProposal.fact_proposal_id.asc())
        .limit(page_limit + 1)
    )
    if cursor is not None:
        cursor_pointer, cursor_id = _decode_proposal_cursor(cursor)
        statement = statement.where(
            or_(
                FactProposal.json_pointer > cursor_pointer,
                and_(
                    FactProposal.json_pointer == cursor_pointer,
                    FactProposal.fact_proposal_id > cursor_id,
                ),
            )
        )

    result = await session.execute(statement)
    rows = list(result.scalars().all())
    next_cursor: str | None = None
    if len(rows) > page_limit:
        last_visible = rows[page_limit - 1]
        next_cursor = _encode_proposal_cursor(
            json_pointer=last_visible.json_pointer,
            fact_proposal_id=last_visible.fact_proposal_id,
        )
        rows = rows[:page_limit]

    return FactProposalsPage(
        items=[map_fact_proposal_to_domain(row) for row in rows],
        next_cursor=next_cursor,
    )


async def _review_fact_proposal(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    fact_proposal_id: uuid.UUID,
    if_match: str | None,
    target_status: str,
    edited_value: Any | None,
    hmac_key: bytes,
    now: datetime,
    audit_action: str,
) -> FactProposalMutationResult:
    validated_now = _require_aware_utc(now, field_name="now")
    if if_match is None:
        raise IfMatchRequiredError()

    proposal_result = await session.execute(
        _load_proposal_for_update_statement(fact_proposal_id)
    )
    proposal = proposal_result.scalar_one_or_none()
    if proposal is None:
        raise FactProposalNotFoundError()

    package_revision, system = await _load_revision_and_system(
        session,
        package_revision_id=proposal.package_revision_id,
    )
    require_any_package_role(
        principal,
        system=system,
        revision=package_revision,
        roles=(ROLE_SYSTEM_OWNER, ROLE_ISSO),
    )
    assert_if_match(if_match, package_revision.revision_version)

    if package_revision.status != "awaiting_confirmation":
        raise FactProposalReviewConflictError(
            current_state=package_revision.status,
            target_state=target_status,
        )
    if proposal.review_status in FACT_PROPOSAL_REVIEW_TERMINAL:
        raise FactProposalReviewConflictError(
            current_state=proposal.review_status,
            target_state=target_status,
        )
    if proposal.review_status != "pending":
        raise FactProposalReviewConflictError(
            current_state=proposal.review_status,
            target_state=target_status,
        )

    if target_status == "edited":
        if edited_value is None:
            raise IllegalStateTransitionError(
                error_code="request_schema_invalid",
                current_state=proposal.review_status,
                target_state=target_status,
                condition="edited_value_required",
            )
        proposal.proposed_value = edited_value
    elif target_status == "accepted":
        if edited_value is not None:
            raise IllegalStateTransitionError(
                error_code="request_schema_invalid",
                current_state=proposal.review_status,
                target_state=target_status,
                condition="edited_value_must_be_null",
            )

    proposal.review_status = target_status
    proposal.reviewed_by = principal.actor_id
    proposal.reviewed_at = validated_now

    payload = map_fact_proposal_to_domain(proposal)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action=audit_action,
        object_type="fact_proposal",
        object_id=format_uuid(fact_proposal_id),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "package_revision_id": format_uuid(proposal.package_revision_id),
            "review_status": target_status,
            "json_pointer": proposal.json_pointer,
        },
        occurred_at=validated_now,
    )
    return FactProposalMutationResult(
        payload=payload,
        etag=format_package_revision_etag(package_revision.revision_version),
    )


async def accept_fact_proposal(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    fact_proposal_id: uuid.UUID,
    if_match: str | None,
    edited_value: Any | None,
    hmac_key: bytes,
    now: datetime,
) -> FactProposalMutationResult:
    """Accept or edit a pending fact proposal."""
    target_status = "accepted" if edited_value is None else "edited"
    return await _review_fact_proposal(
        session,
        principal=principal,
        fact_proposal_id=fact_proposal_id,
        if_match=if_match,
        target_status=target_status,
        edited_value=edited_value,
        hmac_key=hmac_key,
        now=now,
        audit_action="fact_proposal.accepted",
    )


async def reject_fact_proposal(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    fact_proposal_id: uuid.UUID,
    if_match: str | None,
    reason: str,
    hmac_key: bytes,
    now: datetime,
) -> FactProposalMutationResult:
    """Reject a pending fact proposal."""
    if not isinstance(reason, str) or not reason.strip():
        raise IllegalStateTransitionError(
            error_code="request_schema_invalid",
            current_state="pending",
            target_state="rejected",
            condition="reason_required",
        )
    return await _review_fact_proposal(
        session,
        principal=principal,
        fact_proposal_id=fact_proposal_id,
        if_match=if_match,
        target_status="rejected",
        edited_value=None,
        hmac_key=hmac_key,
        now=now,
        audit_action="fact_proposal.rejected",
    )
