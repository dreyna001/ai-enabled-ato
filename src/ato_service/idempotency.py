"""Transactional idempotency primitives for replay-safe P1.1 mutations."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.db import constraints as ck
from ato_service.db.models import IdempotencyRecord

IDEMPOTENCY_RETENTION = timedelta(hours=24)

_IDEMPOTENCY_KEY_PATTERN = re.compile(ck.IDEMPOTENCY_KEY_REGEX)
_SHA256_PATTERN = re.compile(ck.SHA256_REGEX)
_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:password|passwd|secret|api[_-]?key|private[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer|session)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9]{20,}$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"),
    re.compile(r"^Bearer\s+\S+", re.IGNORECASE),
)


class IdempotencyValidationError(ValueError):
    """Raised when idempotency inputs fail validation."""


class IdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with a different request digest."""

    def __init__(
        self,
        *,
        principal: str,
        operation: str,
        idempotency_key: str,
    ) -> None:
        self.principal = principal
        self.operation = operation
        self.idempotency_key = idempotency_key
        super().__init__(
            "idempotency key reused with a different normalized request digest"
        )


@dataclass(frozen=True, slots=True)
class IdempotencyReplay:
    """Immutable stored outcome for a replay-safe request."""

    response_status: int
    response_body: dict[str, Any]


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    """Return canonical JSON bytes for digesting replay-safe request payloads.

    Canonical form uses UTF-8, recursively sorted object keys, compact
    separators (`,` and `:` with no extra whitespace), and no trailing newline.
    """
    _reject_obvious_secrets(document)
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def request_digest_from_payload(payload: Mapping[str, Any]) -> str:
    """Return the SHA-256 hex digest of a canonical replay-safe request payload."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _load_idempotency_select_statement(
    *,
    principal: str,
    operation: str,
    idempotency_key: str,
) -> Any:
    return (
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.principal == principal,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )


async def load_idempotency_replay(
    session: AsyncSession,
    principal: str,
    operation: str,
    idempotency_key: str,
    request_digest: str,
    now: datetime,
) -> IdempotencyReplay | None:
    """Load and lock an idempotency row for replay or conflict detection.

  The caller must invoke this inside an open database transaction. This helper
  does not commit. Concurrent first-time inserts for the same
  ``(principal, operation, idempotency_key)`` are resolved by the unique
  constraint on ``idempotency_records``; the caller should treat
  ``IntegrityError`` as a race and retry or reconcile within the same
  transaction boundary.
    """
    validated_principal = _require_principal(principal)
    validated_operation = _require_operation(operation)
    validated_key = _require_idempotency_key(idempotency_key)
    validated_digest = _require_request_digest(request_digest)
    validated_now = _require_aware_utc(now, field_name="now")

    result = await session.execute(
        _load_idempotency_select_statement(
            principal=validated_principal,
            operation=validated_operation,
            idempotency_key=validated_key,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        return None

    if record.expires_at <= validated_now:
        await session.delete(record)
        return None

    if record.request_digest == validated_digest:
        return IdempotencyReplay(
            response_status=record.response_status,
            response_body=dict(record.response_body),
        )

    raise IdempotencyConflictError(
        principal=validated_principal,
        operation=validated_operation,
        idempotency_key=validated_key,
    )


async def record_idempotency_outcome(
    session: AsyncSession,
    *,
    principal: str,
    operation: str,
    idempotency_key: str,
    request_digest: str,
    response_status: int,
    response_body: Mapping[str, Any],
    now: datetime,
) -> IdempotencyRecord:
    """Insert a replay-safe idempotency outcome row without committing.

  The caller must commit atomically with domain mutations and audit writes.
  Concurrent inserts for the same key may raise ``IntegrityError`` from the
  unique constraint; callers should handle that race explicitly.
    """
    validated_principal = _require_principal(principal)
    validated_operation = _require_operation(operation)
    validated_key = _require_idempotency_key(idempotency_key)
    validated_digest = _require_request_digest(request_digest)
    validated_status = _require_response_status(response_status)
    validated_body = _require_response_body(response_body)
    validated_now = _require_aware_utc(now, field_name="now")

    record = IdempotencyRecord(
        idempotency_record_id=uuid.uuid4(),
        principal=validated_principal,
        operation=validated_operation,
        idempotency_key=validated_key,
        request_digest=validated_digest,
        response_status=validated_status,
        response_body=validated_body,
        created_at=validated_now,
        expires_at=validated_now + IDEMPOTENCY_RETENTION,
    )
    session.add(record)
    return record


def _require_principal(principal: str) -> str:
    if not principal or len(principal) > 255:
        raise IdempotencyValidationError(
            "principal must be between 1 and 255 characters"
        )
    return principal


def _require_operation(operation: str) -> str:
    if not operation or len(operation) > 128:
        raise IdempotencyValidationError(
            "operation must be between 1 and 128 characters"
        )
    return operation


def _require_idempotency_key(idempotency_key: str) -> str:
    if _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise IdempotencyValidationError(
            "idempotency_key must match the contract idempotency-key pattern"
        )
    return idempotency_key


def _require_request_digest(request_digest: str) -> str:
    if _SHA256_PATTERN.fullmatch(request_digest) is None:
        raise IdempotencyValidationError(
            "request_digest must be a lowercase SHA-256 hex digest"
        )
    return request_digest


def _require_response_status(response_status: int) -> int:
    if response_status < 100 or response_status > 599:
        raise IdempotencyValidationError(
            "response_status must be between 100 and 599"
        )
    return response_status


def _require_response_body(response_body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(response_body, Mapping):
        raise IdempotencyValidationError("response_body must be a JSON object")
    normalized = dict(response_body)
    _reject_obvious_secrets(normalized)
    return normalized


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IdempotencyValidationError(
            f"{field_name} must be a timezone-aware datetime"
        )
    return value.astimezone(timezone.utc)


def _reject_obvious_secrets(value: Any, *, key_path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise IdempotencyValidationError("payload keys must be strings")
            nested_path = f"{key_path}.{key}" if key_path else key
            if _SECRET_KEY_PATTERN.search(key):
                raise IdempotencyValidationError(
                    f"{nested_path} uses a secret-bearing field name"
                )
            _reject_obvious_secrets(nested, key_path=nested_path)
        return

    if isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_obvious_secrets(nested, key_path=f"{key_path}[{index}]")
        return

    if isinstance(value, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                raise IdempotencyValidationError(
                    f"{key_path or '<root>'} contains secret-like material"
                )
