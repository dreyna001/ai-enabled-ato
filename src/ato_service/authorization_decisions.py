"""External authorization decision attach service (Component A Diff 11)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    require_system_mutation_access,
    require_system_read_access,
)
from ato_service.concurrency import assert_if_match, format_package_revision_etag
from ato_service.domain_mapping import format_uuid
from ato_service.idempotency import (
    IdempotencyReplay,
    load_idempotency_replay,
    record_idempotency_outcome,
    request_digest_from_payload,
)

OPERATION_ATTACH = "authorization_decisions.attach"


class AuthorizationDecisionNotFoundError(Exception):
    error_code = "resource_not_found"


class AuthorizationDecisionValidationError(ValueError):
    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AttachAuthorizationDecisionInput:
    decision_type: str
    decision_date: str
    issuing_authority: str
    artifact_id: uuid.UUID | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class AuthorizationDecisionMutationResult:
    payload: dict[str, Any]
    status: int
    etag: str
    replayed: bool


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_authorization_decision_to_domain(record: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "object_type": "authorization_decision_record",
        "authorization_decision_id": format_uuid(record.authorization_decision_id),
        "system_id": format_uuid(record.system_id),
        "package_revision_id": (
            format_uuid(record.package_revision_id)
            if record.package_revision_id is not None
            else None
        ),
        "decision_type": record.decision_type,
        "decision_date": record.decision_date,
        "issuing_authority": record.issuing_authority,
        "artifact_id": (
            format_uuid(record.artifact_id) if record.artifact_id is not None else None
        ),
        "notes": record.notes,
        "attached_by": record.attached_by,
        "attached_at": _format_utc(record.attached_at),
    }


async def attach_authorization_decision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    system_id: uuid.UUID,
    package_revision_id: uuid.UUID | None,
    request: AttachAuthorizationDecisionInput,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> AuthorizationDecisionMutationResult:
    from ato_service.db.models import AuthorizationDecisionRecord, PackageRevision, System

    if not request.decision_type.strip():
        raise AuthorizationDecisionValidationError("decision_type is required")
    if not request.issuing_authority.strip():
        raise AuthorizationDecisionValidationError("issuing_authority is required")

    system_result = await session.execute(select(System).where(System.system_id == system_id))
    system = system_result.scalar_one_or_none()
    if system is None:
        raise AuthorizationDecisionNotFoundError()
    try:
        require_system_mutation_access(principal, system)
    except AuthorizationDeniedError:
        raise

    if package_revision_id is not None:
        revision_result = await session.execute(
            select(PackageRevision).where(
                PackageRevision.package_revision_id == package_revision_id,
                PackageRevision.system_id == system_id,
            )
        )
        revision = revision_result.scalar_one_or_none()
        if revision is None:
            raise AuthorizationDecisionNotFoundError()

    request_digest = request_digest_from_payload(
        {
            "system_id": str(system_id).lower(),
            "package_revision_id": (
                str(package_revision_id).lower() if package_revision_id is not None else None
            ),
            "decision_type": request.decision_type,
            "decision_date": request.decision_date,
            "issuing_authority": request.issuing_authority,
            "artifact_id": (
                str(request.artifact_id).lower() if request.artifact_id is not None else None
            ),
            "notes": request.notes,
        }
    )
    replay = await load_idempotency_replay(
        session,
        operation=OPERATION_ATTACH,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    if isinstance(replay, IdempotencyReplay):
        return AuthorizationDecisionMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            etag=replay.response_headers.get("ETag", '"v1"'),
            replayed=True,
        )

    decision_id = uuid.uuid4()
    record = AuthorizationDecisionRecord(
        authorization_decision_id=decision_id,
        system_id=system_id,
        package_revision_id=package_revision_id,
        decision_type=request.decision_type.strip(),
        decision_date=request.decision_date.strip(),
        issuing_authority=request.issuing_authority.strip(),
        artifact_id=request.artifact_id,
        notes=request.notes,
        attached_by=principal.actor_id,
        attached_at=now,
    )
    session.add(record)
    payload = map_authorization_decision_to_domain(record)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="authorization_decision.attach",
        object_type="authorization_decision_record",
        object_id=str(decision_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={"system_id": str(system_id).lower()},
        now=now,
    )
    await record_idempotency_outcome(
        session,
        operation=OPERATION_ATTACH,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=201,
        response_body=payload,
        response_headers={"ETag": '"v1"'},
        now=now,
    )
    return AuthorizationDecisionMutationResult(
        payload=payload,
        status=201,
        etag='"v1"',
        replayed=False,
    )


async def list_authorization_decisions(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    system_id: uuid.UUID,
) -> tuple[dict[str, Any], ...]:
    from ato_service.db.models import AuthorizationDecisionRecord, System

    system_result = await session.execute(select(System).where(System.system_id == system_id))
    system = system_result.scalar_one_or_none()
    if system is None:
        raise AuthorizationDecisionNotFoundError()
    try:
        require_system_read_access(principal, system)
    except AuthorizationDeniedError:
        raise

    result = await session.execute(
        select(AuthorizationDecisionRecord)
        .where(AuthorizationDecisionRecord.system_id == system_id)
        .order_by(AuthorizationDecisionRecord.attached_at.desc())
    )
    records = result.scalars().all()
    return tuple(map_authorization_decision_to_domain(record) for record in records)
