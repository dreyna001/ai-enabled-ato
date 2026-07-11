"""Focused tests for transactional audit chain primitives."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.audit import (
    AUDIT_CHAIN_ADVISORY_LOCK_ID,
    GENESIS_PREVIOUS_EVENT_HASH,
    MIN_AUDIT_HMAC_KEY_BYTES,
    AuditUnavailableError,
    AuditValidationError,
    _audit_chain_advisory_lock_statement,
    _load_latest_audit_event_statement,
    append_audit_event,
    canonical_audit_event_payload,
    compute_audit_event_hash,
    require_audit_hmac_key,
    verify_audit_event_hash,
)
from ato_service.db.models import AuditEvent

UTC = timezone.utc
NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
EVENT_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
PREVIOUS_HASH = "d" * 64


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _compile_sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class _RecordingSession:
    def __init__(self, execute_results: list[MagicMock]) -> None:
        self._execute_results = list(execute_results)
        self.added: list[object] = []
        self.execute_calls: list[object] = []

    async def execute(self, statement: object) -> MagicMock:
        self.execute_calls.append(statement)
        if not self._execute_results:
            raise AssertionError("unexpected execute call")
        return self._execute_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_event(
    *,
    event_hash: str = "e" * 64,
    occurred_at: datetime = NOW - timedelta(seconds=1),
    audit_event_id: uuid.UUID | None = None,
) -> AuditEvent:
    return AuditEvent(
        audit_event_id=audit_event_id or uuid.UUID("11111111-1111-4111-8111-111111111111"),
        occurred_at=occurred_at,
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="22222222-2222-4222-8222-222222222222",
        outcome="succeeded",
        reason_code=None,
        metadata_={"request_id": "77777777-7777-4777-8777-777777777777"},
        previous_event_hash=PREVIOUS_HASH,
        event_hash=event_hash,
    )


def test_genesis_previous_event_hash_is_sixty_four_zeros() -> None:
    assert GENESIS_PREVIOUS_EVENT_HASH == "0" * 64
    assert len(GENESIS_PREVIOUS_EVENT_HASH) == 64


def test_advisory_lock_sql_uses_transaction_scoped_lock_constant() -> None:
    sql = _compile_sql(_audit_chain_advisory_lock_statement())
    assert "pg_advisory_xact_lock" in sql
    assert str(AUDIT_CHAIN_ADVISORY_LOCK_ID) in sql


def test_latest_audit_event_sql_orders_and_locks_tail_row() -> None:
    sql = _compile_sql(_load_latest_audit_event_statement())
    assert "FROM audit_events" in sql
    assert "ORDER BY audit_events.occurred_at DESC" in sql
    assert "audit_events.audit_event_id DESC" in sql
    assert "LIMIT 1" in sql
    assert "FOR UPDATE" in sql


def test_require_audit_hmac_key_rejects_missing_or_short_keys() -> None:
    with pytest.raises(AuditUnavailableError, match="required"):
        require_audit_hmac_key(None)

    with pytest.raises(AuditUnavailableError, match="at least"):
        require_audit_hmac_key(b"short")


def test_canonical_audit_event_payload_excludes_event_hash() -> None:
    payload = canonical_audit_event_payload(
        audit_event_id=EVENT_ID,
        occurred_at=NOW,
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="11111111-1111-4111-8111-111111111111",
        outcome="succeeded",
        reason_code=None,
        metadata={"request_id": "77777777-7777-4777-8777-777777777777"},
        previous_event_hash=PREVIOUS_HASH,
    )

    assert "event_hash" not in payload
    assert payload["previous_event_hash"] == PREVIOUS_HASH
    assert payload["occurred_at"] == "2026-07-11T12:00:00Z"


def test_compute_and_verify_audit_event_hash_round_trip() -> None:
    metadata = {"request_id": "77777777-7777-4777-8777-777777777777"}
    event_hash = compute_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=EVENT_ID,
        occurred_at=NOW,
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="11111111-1111-4111-8111-111111111111",
        outcome="succeeded",
        reason_code=None,
        metadata=metadata,
        previous_event_hash=PREVIOUS_HASH,
    )

    assert len(event_hash) == 64
    assert verify_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=EVENT_ID,
        occurred_at=NOW,
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="11111111-1111-4111-8111-111111111111",
        outcome="succeeded",
        reason_code=None,
        metadata=metadata,
        previous_event_hash=PREVIOUS_HASH,
        event_hash=event_hash,
    )
    assert not verify_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=EVENT_ID,
        occurred_at=NOW,
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="11111111-1111-4111-8111-111111111111",
        outcome="succeeded",
        reason_code=None,
        metadata=metadata,
        previous_event_hash=PREVIOUS_HASH,
        event_hash="f" * 64,
    )


def test_append_audit_event_uses_genesis_hash_on_empty_chain() -> None:
    session = _RecordingSession(
        [
            MagicMock(),
            _scalar_result(None),
        ]
    )

    event = _run(
        append_audit_event(
            session,
            hmac_key=HMAC_KEY,
            actor_type="service",
            actor_id="analysis-worker",
            action="package_revision.created",
            object_type="package_revision",
            object_id="11111111-1111-4111-8111-111111111111",
            outcome="succeeded",
            reason_code=None,
            metadata={"request_id": "77777777-7777-4777-8777-777777777777"},
            occurred_at=NOW,
        )
    )

    assert len(session.execute_calls) == 2
    assert session.added == [event]
    assert event.previous_event_hash == GENESIS_PREVIOUS_EVENT_HASH
    assert verify_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=event.audit_event_id,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        action=event.action,
        object_type=event.object_type,
        object_id=event.object_id,
        outcome=event.outcome,
        reason_code=event.reason_code,
        metadata=event.metadata_,
        previous_event_hash=event.previous_event_hash,
        event_hash=event.event_hash,
    )


def test_append_audit_event_chains_from_latest_locked_row() -> None:
    latest = _make_event(event_hash="c" * 64)
    session = _RecordingSession(
        [
            MagicMock(),
            _scalar_result(latest),
        ]
    )

    event = _run(
        append_audit_event(
            session,
            hmac_key=HMAC_KEY,
            actor_type="user",
            actor_id="reviewer@example.test",
            action="package_revision.confirmed",
            object_type="package_revision",
            object_id="11111111-1111-4111-8111-111111111111",
            outcome="succeeded",
            reason_code=None,
            metadata={"revision_version": 3},
            occurred_at=NOW,
        )
    )

    assert event.previous_event_hash == latest.event_hash
    assert event.actor_type == "user"
    assert event.metadata_ == {"revision_version": 3}


def test_append_audit_event_acquires_advisory_lock_before_tail_query() -> None:
    session = _RecordingSession([MagicMock(), _scalar_result(None)])

    _run(
        append_audit_event(
            session,
            hmac_key=HMAC_KEY,
            actor_type="service",
            actor_id="analysis-worker",
            action="package_revision.created",
            object_type="package_revision",
            object_id="11111111-1111-4111-8111-111111111111",
            outcome="succeeded",
            reason_code=None,
            metadata={},
            occurred_at=NOW,
        )
    )

    lock_sql = _compile_sql(session.execute_calls[0])
    tail_sql = _compile_sql(session.execute_calls[1])
    assert "pg_advisory_xact_lock" in lock_sql
    assert "FOR UPDATE" in tail_sql


def test_append_audit_event_rejects_secret_metadata() -> None:
    session = _RecordingSession([])

    with pytest.raises(AuditValidationError, match="secret"):
        _run(
            append_audit_event(
                session,
                hmac_key=HMAC_KEY,
                actor_type="service",
                actor_id="analysis-worker",
                action="package_revision.created",
                object_type="package_revision",
                object_id="11111111-1111-4111-8111-111111111111",
                outcome="denied",
                reason_code="authorization_denied",
                metadata={"bearer_token": "abc"},
                occurred_at=NOW,
            )
        )

    assert session.execute_calls == []
    assert session.added == []


def test_append_audit_event_rejects_invalid_action_or_reason_code() -> None:
    session = _RecordingSession([])

    with pytest.raises(AuditValidationError, match="action"):
        _run(
            append_audit_event(
                session,
                hmac_key=HMAC_KEY,
                actor_type="service",
                actor_id="analysis-worker",
                action="INVALID",
                object_type="package_revision",
                object_id="11111111-1111-4111-8111-111111111111",
                outcome="failed",
                reason_code="audit_chain_invalid",
                metadata={},
                occurred_at=NOW,
            )
        )

    with pytest.raises(AuditValidationError, match="reason_code"):
        _run(
            append_audit_event(
                session,
                hmac_key=HMAC_KEY,
                actor_type="service",
                actor_id="analysis-worker",
                action="package_revision.created",
                object_type="package_revision",
                object_id="11111111-1111-4111-8111-111111111111",
                outcome="failed",
                reason_code="Bad_Code",
                metadata={},
                occurred_at=NOW,
            )
        )
