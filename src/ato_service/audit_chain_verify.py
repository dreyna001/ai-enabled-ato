"""Ordered HMAC audit-chain verification with bounded memory and redacted reporting."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import (
    GENESIS_PREVIOUS_EVENT_HASH,
    verify_audit_event_hash,
)
from ato_service.db.models import AuditEvent

DEFAULT_CHECKPOINT_INTERVAL = 1_000
DEFAULT_BATCH_SIZE = 100
MAX_CHECKPOINTS = 100


class AuditChainFailureReason(StrEnum):
    """Explicit verification failure categories for operator reconciliation."""

    CHAIN_BREAK = "chain_break"
    HMAC_MISMATCH = "hmac_mismatch"
    ORDERING_VIOLATION = "ordering_violation"
    WRONG_KEY = "wrong_key"


@dataclass(frozen=True, slots=True)
class AuditChainCheckpoint:
    """Rolling chain summary marker without event payload material."""

    event_index: int
    audit_event_id: str
    event_hash: str


@dataclass(frozen=True, slots=True)
class AuditChainVerifyOptions:
    """Bounded verification options; filters never alter global integrity checks."""

    checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL
    batch_size: int = DEFAULT_BATCH_SIZE
    object_type: str | None = None
    object_id_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class AuditChainVerificationReport:
    """Redacted audit-chain verification outcome for operators and drills."""

    passed: bool
    verified_events: int
    total_events: int
    genesis_hash: str
    head_hash: str | None
    checkpoints: tuple[AuditChainCheckpoint, ...]
    failure_reason: AuditChainFailureReason | None
    failure_event_index: int | None
    failure_audit_event_id: str | None
    verification_scope: str
    matching_events: int
    detail: str

    def to_redacted_dict(self) -> dict[str, Any]:
        """Return a JSON-safe summary that omits secrets and event metadata."""
        payload: dict[str, Any] = {
            "passed": self.passed,
            "verified_events": self.verified_events,
            "total_events": self.total_events,
            "genesis_hash": self.genesis_hash,
            "head_hash": self.head_hash,
            "checkpoints": [
                {
                    "event_index": checkpoint.event_index,
                    "audit_event_id": checkpoint.audit_event_id,
                    "event_hash": checkpoint.event_hash,
                }
                for checkpoint in self.checkpoints
            ],
            "failure_reason": (
                self.failure_reason.value if self.failure_reason is not None else None
            ),
            "failure_event_index": self.failure_event_index,
            "failure_audit_event_id": self.failure_audit_event_id,
            "verification_scope": self.verification_scope,
            "matching_events": self.matching_events,
            "detail": self.detail,
        }
        return payload


def _matches_filter(
    event: AuditEvent,
    *,
    object_type: str | None,
    object_id_prefix: str | None,
) -> bool:
    if object_type is not None and event.object_type != object_type:
        return False
    if object_id_prefix is not None and not event.object_id.startswith(object_id_prefix):
        return False
    return True


def _ordering_violation(
    previous: AuditEvent | None,
    current: AuditEvent,
) -> bool:
    if previous is None:
        return False
    if current.occurred_at < previous.occurred_at:
        return True
    if current.occurred_at == previous.occurred_at:
        return current.audit_event_id < previous.audit_event_id
    return False


def _append_checkpoint(
    checkpoints: list[AuditChainCheckpoint],
    *,
    event_index: int,
    event: AuditEvent,
) -> None:
    if len(checkpoints) >= MAX_CHECKPOINTS:
        return
    checkpoints.append(
        AuditChainCheckpoint(
            event_index=event_index,
            audit_event_id=str(event.audit_event_id).lower(),
            event_hash=event.event_hash,
        )
    )


def verify_audit_chain_events(
    events: Sequence[AuditEvent],
    *,
    hmac_key: bytes,
    options: AuditChainVerifyOptions | None = None,
) -> AuditChainVerificationReport:
    """Verify one in-memory audit chain in canonical order."""
    resolved = options or AuditChainVerifyOptions()
    checkpoints: list[AuditChainCheckpoint] = []
    expected_previous = GENESIS_PREVIOUS_EVENT_HASH
    matching_events = 0
    previous_event: AuditEvent | None = None
    head_hash: str | None = None

    if not events:
        return AuditChainVerificationReport(
            passed=True,
            verified_events=0,
            total_events=0,
            genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
            head_hash=None,
            checkpoints=(),
            failure_reason=None,
            failure_event_index=None,
            failure_audit_event_id=None,
            verification_scope="global",
            matching_events=0,
            detail="no audit events present",
        )

    for index, event in enumerate(events):
        if _ordering_violation(previous_event, event):
            return AuditChainVerificationReport(
                passed=False,
                verified_events=index,
                total_events=len(events),
                genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
                head_hash=head_hash,
                checkpoints=tuple(checkpoints),
                failure_reason=AuditChainFailureReason.ORDERING_VIOLATION,
                failure_event_index=index,
                failure_audit_event_id=str(event.audit_event_id).lower(),
                verification_scope="global",
                matching_events=matching_events,
                detail=f"ordering violation at event index {index}",
            )

        if event.previous_event_hash != expected_previous:
            return AuditChainVerificationReport(
                passed=False,
                verified_events=index,
                total_events=len(events),
                genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
                head_hash=head_hash,
                checkpoints=tuple(checkpoints),
                failure_reason=AuditChainFailureReason.CHAIN_BREAK,
                failure_event_index=index,
                failure_audit_event_id=str(event.audit_event_id).lower(),
                verification_scope="global",
                matching_events=matching_events,
                detail=f"chain break at event index {index}",
            )

        if not verify_audit_event_hash(
            hmac_key=hmac_key,
            audit_event_id=event.audit_event_id,
            occurred_at=event.occurred_at,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            action=event.action,
            object_type=event.object_type,
            object_id=event.object_id,
            outcome=event.outcome,
            reason_code=event.reason_code,
            metadata=event.metadata_ or {},
            previous_event_hash=event.previous_event_hash,
            event_hash=event.event_hash,
        ):
            failure_reason = (
                AuditChainFailureReason.WRONG_KEY
                if index == 0
                else AuditChainFailureReason.HMAC_MISMATCH
            )
            detail = (
                "HMAC mismatch at genesis event; audit key may be incorrect"
                if failure_reason is AuditChainFailureReason.WRONG_KEY
                else f"HMAC mismatch at event index {index}"
            )
            return AuditChainVerificationReport(
                passed=False,
                verified_events=index,
                total_events=len(events),
                genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
                head_hash=head_hash,
                checkpoints=tuple(checkpoints),
                failure_reason=failure_reason,
                failure_event_index=index,
                failure_audit_event_id=str(event.audit_event_id).lower(),
                verification_scope="global",
                matching_events=matching_events,
                detail=detail,
            )

        if _matches_filter(
            event,
            object_type=resolved.object_type,
            object_id_prefix=resolved.object_id_prefix,
        ):
            matching_events += 1

        if index == 0 or (index + 1) % resolved.checkpoint_interval == 0:
            _append_checkpoint(checkpoints, event_index=index, event=event)

        expected_previous = event.event_hash
        head_hash = event.event_hash
        previous_event = event

    if len(events) > 1 and (len(events) - 1) % resolved.checkpoint_interval != 0:
        _append_checkpoint(
            checkpoints,
            event_index=len(events) - 1,
            event=events[-1],
        )

    return AuditChainVerificationReport(
        passed=True,
        verified_events=len(events),
        total_events=len(events),
        genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
        head_hash=head_hash,
        checkpoints=tuple(checkpoints),
        failure_reason=None,
        failure_event_index=None,
        failure_audit_event_id=None,
        verification_scope="global",
        matching_events=matching_events,
        detail="chain intact",
    )


def _ordered_events_statement(
    *,
    batch_size: int,
    cursor_occurred_at: datetime | None,
    cursor_event_id: uuid.UUID | None,
) -> Any:
    statement = select(AuditEvent).order_by(
        AuditEvent.occurred_at.asc(),
        AuditEvent.audit_event_id.asc(),
    )
    if cursor_occurred_at is not None and cursor_event_id is not None:
        statement = statement.where(
            or_(
                AuditEvent.occurred_at > cursor_occurred_at,
                and_(
                    AuditEvent.occurred_at == cursor_occurred_at,
                    AuditEvent.audit_event_id > cursor_event_id,
                ),
            )
        )
    return statement.limit(batch_size)


async def _iter_audit_events(
    session: AsyncSession,
    *,
    batch_size: int,
) -> AsyncIterator[AuditEvent]:
    cursor_occurred_at: datetime | None = None
    cursor_event_id: uuid.UUID | None = None
    while True:
        result = await session.execute(
            _ordered_events_statement(
                batch_size=batch_size,
                cursor_occurred_at=cursor_occurred_at,
                cursor_event_id=cursor_event_id,
            )
        )
        batch = list(result.scalars())
        if not batch:
            return
        for event in batch:
            yield event
        last = batch[-1]
        cursor_occurred_at = last.occurred_at
        cursor_event_id = last.audit_event_id


async def verify_audit_chain_session(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    options: AuditChainVerifyOptions | None = None,
) -> AuditChainVerificationReport:
    """Verify the persisted audit chain using bounded batch reads."""
    resolved = options or AuditChainVerifyOptions()
    checkpoints: list[AuditChainCheckpoint] = []
    expected_previous = GENESIS_PREVIOUS_EVENT_HASH
    matching_events = 0
    previous_event: AuditEvent | None = None
    head_hash: str | None = None
    index = 0

    async for event in _iter_audit_events(session, batch_size=resolved.batch_size):
        if _ordering_violation(previous_event, event):
            return AuditChainVerificationReport(
                passed=False,
                verified_events=index,
                total_events=index + 1,
                genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
                head_hash=head_hash,
                checkpoints=tuple(checkpoints),
                failure_reason=AuditChainFailureReason.ORDERING_VIOLATION,
                failure_event_index=index,
                failure_audit_event_id=str(event.audit_event_id).lower(),
                verification_scope="global",
                matching_events=matching_events,
                detail=f"ordering violation at event index {index}",
            )

        if event.previous_event_hash != expected_previous:
            return AuditChainVerificationReport(
                passed=False,
                verified_events=index,
                total_events=index + 1,
                genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
                head_hash=head_hash,
                checkpoints=tuple(checkpoints),
                failure_reason=AuditChainFailureReason.CHAIN_BREAK,
                failure_event_index=index,
                failure_audit_event_id=str(event.audit_event_id).lower(),
                verification_scope="global",
                matching_events=matching_events,
                detail=f"chain break at event index {index}",
            )

        if not verify_audit_event_hash(
            hmac_key=hmac_key,
            audit_event_id=event.audit_event_id,
            occurred_at=event.occurred_at,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            action=event.action,
            object_type=event.object_type,
            object_id=event.object_id,
            outcome=event.outcome,
            reason_code=event.reason_code,
            metadata=event.metadata_ or {},
            previous_event_hash=event.previous_event_hash,
            event_hash=event.event_hash,
        ):
            failure_reason = (
                AuditChainFailureReason.WRONG_KEY
                if index == 0
                else AuditChainFailureReason.HMAC_MISMATCH
            )
            detail = (
                "HMAC mismatch at genesis event; audit key may be incorrect"
                if failure_reason is AuditChainFailureReason.WRONG_KEY
                else f"HMAC mismatch at event index {index}"
            )
            return AuditChainVerificationReport(
                passed=False,
                verified_events=index,
                total_events=index + 1,
                genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
                head_hash=head_hash,
                checkpoints=tuple(checkpoints),
                failure_reason=failure_reason,
                failure_event_index=index,
                failure_audit_event_id=str(event.audit_event_id).lower(),
                verification_scope="global",
                matching_events=matching_events,
                detail=detail,
            )

        if _matches_filter(
            event,
            object_type=resolved.object_type,
            object_id_prefix=resolved.object_id_prefix,
        ):
            matching_events += 1

        if index == 0 or (index + 1) % resolved.checkpoint_interval == 0:
            _append_checkpoint(checkpoints, event_index=index, event=event)

        expected_previous = event.event_hash
        head_hash = event.event_hash
        previous_event = event
        index += 1

    if index == 0:
        return AuditChainVerificationReport(
            passed=True,
            verified_events=0,
            total_events=0,
            genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
            head_hash=None,
            checkpoints=(),
            failure_reason=None,
            failure_event_index=None,
            failure_audit_event_id=None,
            verification_scope="global",
            matching_events=0,
            detail="no audit events present",
        )

    assert previous_event is not None
    if index > 1 and (index - 1) % resolved.checkpoint_interval != 0:
        _append_checkpoint(
            checkpoints,
            event_index=index - 1,
            event=previous_event,
        )

    return AuditChainVerificationReport(
        passed=True,
        verified_events=index,
        total_events=index,
        genesis_hash=GENESIS_PREVIOUS_EVENT_HASH,
        head_hash=head_hash,
        checkpoints=tuple(checkpoints),
        failure_reason=None,
        failure_event_index=None,
        failure_audit_event_id=None,
        verification_scope="global",
        matching_events=matching_events,
        detail="chain intact",
    )


def redact_verification_detail(report: Mapping[str, Any]) -> dict[str, Any]:
    """Ensure operator output never echoes secret-like substrings."""
    forbidden_fragments = (
        "sk-",
        "Bearer ",
        "AKIA",
        "eyJ",
        "password",
        "secret",
        "api_key",
    )
    serialized = str(report)
    for fragment in forbidden_fragments:
        if fragment.lower() in serialized.lower():
            raise ValueError("verification report contains secret-like material")
    return dict(report)


__all__ = [
    "AuditChainCheckpoint",
    "AuditChainFailureReason",
    "AuditChainVerificationReport",
    "AuditChainVerifyOptions",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CHECKPOINT_INTERVAL",
    "MAX_CHECKPOINTS",
    "redact_verification_detail",
    "verify_audit_chain_events",
    "verify_audit_chain_session",
]
