"""Focused tests for transactional idempotency primitives."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.db.models import IdempotencyRecord
from ato_service.idempotency import (
    IDEMPOTENCY_RETENTION,
    IdempotencyConflictError,
    IdempotencyReplay,
    IdempotencyValidationError,
    _idempotency_advisory_lock_statement,
    _load_idempotency_select_statement,
    canonical_json_bytes,
    idempotency_advisory_lock_keys,
    load_idempotency_replay,
    record_idempotency_outcome,
    replay_etag_from_outcome,
    request_digest_from_payload,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
PRINCIPAL = "operator@example.test"
OPERATION = "package_revision.confirm"
KEY = "idem-key-0123456789"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


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
        self.deleted: list[object] = []
        self.execute_calls: list[object] = []

    async def execute(self, statement: object) -> MagicMock:
        self.execute_calls.append(statement)
        if not self._execute_results:
            raise AssertionError("unexpected execute call")
        return self._execute_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)


def _scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _replay_session(*select_results: object) -> _RecordingSession:
    return _RecordingSession(
        [MagicMock(), *[_scalar_result(value) for value in select_results]]
    )


def _make_record(
    *,
    request_digest: str = DIGEST_A,
    expires_at: datetime = NOW + IDEMPOTENCY_RETENTION,
    response_status: int = 201,
    response_body: dict[str, Any] | None = None,
    response_headers: dict[str, str] | None = None,
) -> IdempotencyRecord:
    return IdempotencyRecord(
        idempotency_record_id=uuid.uuid4(),
        principal=PRINCIPAL,
        operation=OPERATION,
        idempotency_key=KEY,
        request_digest=request_digest,
        response_status=response_status,
        response_body=response_body or {"status": "ready"},
        response_headers=response_headers or {},
        created_at=NOW,
        expires_at=expires_at,
    )


def test_retention_constant_is_twenty_four_hours() -> None:
    assert IDEMPOTENCY_RETENTION == timedelta(hours=24)


def test_canonical_json_bytes_is_deterministic_and_compact() -> None:
    payload = {"b": 2, "a": {"d": 4, "c": 3}}
    assert canonical_json_bytes(payload) == b'{"a":{"c":3,"d":4},"b":2}'


def test_request_digest_from_payload_matches_sha256_of_canonical_bytes() -> None:
    payload = {"system_id": "11111111-1111-4111-8111-111111111111"}
    digest = request_digest_from_payload(payload)
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


def test_canonical_json_bytes_rejects_secret_bearing_field_names() -> None:
    with pytest.raises(IdempotencyValidationError, match="secret-bearing"):
        canonical_json_bytes({"api_key": "value"})


def test_canonical_json_bytes_rejects_secret_like_values() -> None:
    with pytest.raises(IdempotencyValidationError, match="secret-like"):
        canonical_json_bytes({"note": "Bearer abc.def.ghi"})


def test_advisory_lock_keys_are_deterministic_signed_int32_pairs() -> None:
    keys_a = idempotency_advisory_lock_keys(PRINCIPAL, OPERATION, KEY)
    keys_b = idempotency_advisory_lock_keys(PRINCIPAL, OPERATION, KEY)
    keys_other = idempotency_advisory_lock_keys(PRINCIPAL, OPERATION, "other-idem-key-0001")

    assert keys_a == keys_b
    assert keys_a != keys_other
    for key in keys_a:
        assert -(2**31) <= key < 2**31


def test_advisory_lock_sql_uses_transaction_scoped_two_key_lock() -> None:
    key1, key2 = idempotency_advisory_lock_keys(PRINCIPAL, OPERATION, KEY)
    sql = _compile_sql(_idempotency_advisory_lock_statement(key1, key2))
    assert "pg_advisory_xact_lock" in sql
    assert str(key1) in sql
    assert str(key2) in sql


def test_load_sql_uses_for_update_on_principal_operation_key() -> None:
    sql = _compile_sql(
        _load_idempotency_select_statement(
            principal=PRINCIPAL,
            operation=OPERATION,
            idempotency_key=KEY,
        )
    )
    assert "FROM idempotency_records" in sql
    assert f"principal = '{PRINCIPAL}'" in sql
    assert f"operation = '{OPERATION}'" in sql
    assert f"idempotency_key = '{KEY}'" in sql
    assert "FOR UPDATE" in sql


def test_load_idempotency_replay_acquires_advisory_lock_before_select() -> None:
    session = _replay_session(None)

    replay = _run(
        load_idempotency_replay(
            session,
            PRINCIPAL,
            OPERATION,
            KEY,
            DIGEST_A,
            NOW,
        )
    )

    assert replay is None
    assert len(session.execute_calls) == 2
    lock_sql = _compile_sql(session.execute_calls[0])
    select_sql = _compile_sql(session.execute_calls[1])
    assert "pg_advisory_xact_lock" in lock_sql
    assert "FROM idempotency_records" in select_sql


def test_load_idempotency_replay_returns_none_when_missing() -> None:
    session = _replay_session(None)

    replay = _run(
        load_idempotency_replay(
            session,
            PRINCIPAL,
            OPERATION,
            KEY,
            DIGEST_A,
            NOW,
        )
    )

    assert replay is None
    assert session.deleted == []


def test_load_idempotency_replay_deletes_expired_row_and_returns_none() -> None:
    expired = _make_record(expires_at=NOW - timedelta(seconds=1))
    session = _replay_session(expired)

    replay = _run(
        load_idempotency_replay(
            session,
            PRINCIPAL,
            OPERATION,
            KEY,
            DIGEST_A,
            NOW,
        )
    )

    assert replay is None
    assert session.deleted == [expired]


def test_load_idempotency_replay_returns_immutable_outcome_for_matching_digest() -> None:
    stored = _make_record(
        response_status=202,
        response_body={"etag": '"v2"'},
        response_headers={"ETag": '"v2"'},
    )
    session = _replay_session(stored)

    replay = _run(
        load_idempotency_replay(
            session,
            PRINCIPAL,
            OPERATION,
            KEY,
            DIGEST_A,
            NOW,
        )
    )

    assert isinstance(replay, IdempotencyReplay)
    assert replay.response_status == 202
    assert replay.response_body == {"etag": '"v2"'}
    assert replay.response_headers == {"ETag": '"v2"'}
    replay.response_body["etag"] = '"v3"'
    assert stored.response_body == {"etag": '"v2"'}


def test_load_idempotency_replay_raises_conflict_for_different_digest() -> None:
    stored = _make_record(request_digest=DIGEST_A)
    session = _replay_session(stored)

    with pytest.raises(IdempotencyConflictError) as exc_info:
        _run(
            load_idempotency_replay(
                session,
                PRINCIPAL,
                OPERATION,
                KEY,
                DIGEST_B,
                NOW,
            )
        )

    assert exc_info.value.principal == PRINCIPAL
    assert exc_info.value.operation == OPERATION
    assert exc_info.value.idempotency_key == KEY


def test_replay_etag_from_outcome_prefers_stored_headers_over_body() -> None:
    etag = replay_etag_from_outcome(
        response_body={"revision_version": 1},
        response_headers={"ETag": '"v9"'},
    )
    assert etag == '"v9"'


def test_replay_etag_from_outcome_falls_back_to_body_revision_version() -> None:
    etag = replay_etag_from_outcome(
        response_body={"revision_version": 4},
        response_headers={},
    )
    assert etag == '"v4"'


def test_record_idempotency_outcome_inserts_row_without_commit() -> None:
    session = _RecordingSession([])

    record = _run(
        record_idempotency_outcome(
            session,
            principal=PRINCIPAL,
            operation=OPERATION,
            idempotency_key=KEY,
            request_digest=DIGEST_A,
            response_status=201,
            response_body={"status": "ready"},
            response_headers={"ETag": '"v1"'},
            now=NOW,
        )
    )

    assert session.execute_calls == []
    assert session.added == [record]
    assert isinstance(record.idempotency_record_id, uuid.UUID)
    assert record.principal == PRINCIPAL
    assert record.operation == OPERATION
    assert record.idempotency_key == KEY
    assert record.request_digest == DIGEST_A
    assert record.response_status == 201
    assert record.response_body == {"status": "ready"}
    assert record.response_headers == {"ETag": '"v1"'}
    assert record.created_at == NOW
    assert record.expires_at == NOW + IDEMPOTENCY_RETENTION


@pytest.mark.parametrize(
    "forbidden_header",
    ["Authorization", "Cookie", "Set-Cookie", "X-CSRF-Token"],
)
def test_record_idempotency_outcome_rejects_secret_bearing_headers(
    forbidden_header: str,
) -> None:
    session = _RecordingSession([])

    with pytest.raises(IdempotencyValidationError, match="must not be stored"):
        _run(
            record_idempotency_outcome(
                session,
                principal=PRINCIPAL,
                operation=OPERATION,
                idempotency_key=KEY,
                request_digest=DIGEST_A,
                response_status=201,
                response_body={"status": "ready"},
                response_headers={forbidden_header: "secret"},
                now=NOW,
            )
        )

    assert session.added == []


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("short", "idempotency_key"),
        ("x" * 129, "idempotency_key"),
        ("invalid key!!!!!!!", "idempotency_key"),
    ],
)
def test_record_idempotency_outcome_rejects_invalid_key(key: str, message: str) -> None:
    session = _RecordingSession([])

    with pytest.raises(IdempotencyValidationError, match=message):
        _run(
            record_idempotency_outcome(
                session,
                principal=PRINCIPAL,
                operation=OPERATION,
                idempotency_key=key,
                request_digest=DIGEST_A,
                response_status=201,
                response_body={"status": "ready"},
                now=NOW,
            )
        )

    assert session.added == []
