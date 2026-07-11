"""Transactional append-only audit chain primitives for P1.1 mutations."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.db import constraints as ck
from ato_service.db import enums as ev
from ato_service.db.models import AuditEvent

GENESIS_PREVIOUS_EVENT_HASH = "0" * 64
AUDIT_CHAIN_ADVISORY_LOCK_ID = 4_154_110_001
MIN_AUDIT_HMAC_KEY_BYTES = 32

_AUDIT_ACTION_PATTERN = re.compile(ck.AUDIT_ACTION_REGEX)
_AUDIT_OBJECT_TYPE_PATTERN = re.compile(ck.AUDIT_OBJECT_TYPE_REGEX)
_ERROR_CODE_PATTERN = re.compile(ck.ERROR_CODE_REGEX)
_SHA256_PATTERN = re.compile(ck.SHA256_REGEX)
_METADATA_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:password|passwd|secret|api[_-]?key|private[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer|session|"
    r"prompt|credential)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9]{20,}$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"),
    re.compile(r"^Bearer\s+\S+", re.IGNORECASE),
)


class AuditValidationError(ValueError):
    """Raised when audit event inputs fail validation."""


class AuditUnavailableError(Exception):
    """Raised when audit HMAC credentials are missing or unusable."""

    def __init__(self, message: str = "audit HMAC key is unavailable") -> None:
        super().__init__(message)


def _audit_chain_advisory_lock_statement() -> Any:
    return select(func.pg_advisory_xact_lock(AUDIT_CHAIN_ADVISORY_LOCK_ID))


def _load_latest_audit_event_statement() -> Any:
    return (
        select(AuditEvent)
        .order_by(
            AuditEvent.occurred_at.desc(),
            AuditEvent.audit_event_id.desc(),
        )
        .limit(1)
        .with_for_update()
    )


def canonical_audit_event_payload(
    *,
    audit_event_id: uuid.UUID,
    occurred_at: datetime,
    actor_type: str,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    outcome: str,
    reason_code: str | None,
    metadata: Mapping[str, Any],
    previous_event_hash: str,
) -> dict[str, Any]:
    """Build the canonical hash input excluding ``event_hash``."""
    return {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "audit_event_id": str(audit_event_id),
        "action": action,
        "metadata": dict(metadata),
        "object_id": object_id,
        "object_type": object_type,
        "occurred_at": _format_occurred_at(occurred_at),
        "outcome": outcome,
        "previous_event_hash": previous_event_hash,
        "reason_code": reason_code,
    }


def compute_audit_event_hash(
    *,
    hmac_key: bytes,
    audit_event_id: uuid.UUID,
    occurred_at: datetime,
    actor_type: str,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    outcome: str,
    reason_code: str | None,
    metadata: Mapping[str, Any],
    previous_event_hash: str,
) -> str:
    """Return the HMAC-SHA-256 hex digest for an audit event payload."""
    validated_key = require_audit_hmac_key(hmac_key)
    payload = canonical_audit_event_payload(
        audit_event_id=audit_event_id,
        occurred_at=occurred_at,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        outcome=outcome,
        reason_code=reason_code,
        metadata=metadata,
        previous_event_hash=previous_event_hash,
    )
    message = _canonical_json_bytes(payload)
    return hmac.new(validated_key, message, hashlib.sha256).hexdigest()


def verify_audit_event_hash(
    *,
    hmac_key: bytes,
    audit_event_id: uuid.UUID,
    occurred_at: datetime,
    actor_type: str,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    outcome: str,
    reason_code: str | None,
    metadata: Mapping[str, Any],
    previous_event_hash: str,
    event_hash: str,
) -> bool:
    """Return whether ``event_hash`` matches the canonical HMAC for the event."""
    if _SHA256_PATTERN.fullmatch(event_hash) is None:
        return False
    expected = compute_audit_event_hash(
        hmac_key=hmac_key,
        audit_event_id=audit_event_id,
        occurred_at=occurred_at,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        outcome=outcome,
        reason_code=reason_code,
        metadata=metadata,
        previous_event_hash=previous_event_hash,
    )
    return hmac.compare_digest(expected, event_hash)


def require_audit_hmac_key(hmac_key: bytes | None) -> bytes:
    """Validate caller-supplied audit HMAC key bytes without storing them."""
    if hmac_key is None:
        raise AuditUnavailableError("audit HMAC key is required")
    if len(hmac_key) < MIN_AUDIT_HMAC_KEY_BYTES:
        raise AuditUnavailableError(
            f"audit HMAC key must be at least {MIN_AUDIT_HMAC_KEY_BYTES} bytes"
        )
    return hmac_key


async def append_audit_event(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    actor_type: str,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    outcome: str,
    reason_code: str | None,
    metadata: Mapping[str, Any] | None,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    """Append one audit event to the global hash chain without committing.

  The caller must invoke this inside an open database transaction. A
  transaction-scoped PostgreSQL advisory lock serializes chain-tail access so
  concurrent genesis appends cannot observe an empty table without
  serialization. This helper does not commit.
    """
    validated_key = require_audit_hmac_key(hmac_key)
    validated_actor_type = _require_actor_type(actor_type)
    validated_actor_id = _require_actor_id(actor_id)
    validated_action = _require_action(action)
    validated_object_type = _require_object_type(object_type)
    validated_object_id = _require_object_id(object_id)
    validated_outcome = _require_outcome(outcome)
    validated_reason_code = _require_reason_code(reason_code)
    validated_metadata = _require_metadata(metadata or {})
    validated_occurred_at = _require_aware_utc(
        occurred_at or datetime.now(timezone.utc),
        field_name="occurred_at",
    )

    await session.execute(_audit_chain_advisory_lock_statement())

    result = await session.execute(_load_latest_audit_event_statement())
    latest = result.scalar_one_or_none()
    previous_event_hash = (
        latest.event_hash if latest is not None else GENESIS_PREVIOUS_EVENT_HASH
    )

    audit_event_id = uuid.uuid4()
    event_hash = compute_audit_event_hash(
        hmac_key=validated_key,
        audit_event_id=audit_event_id,
        occurred_at=validated_occurred_at,
        actor_type=validated_actor_type,
        actor_id=validated_actor_id,
        action=validated_action,
        object_type=validated_object_type,
        object_id=validated_object_id,
        outcome=validated_outcome,
        reason_code=validated_reason_code,
        metadata=validated_metadata,
        previous_event_hash=previous_event_hash,
    )

    event = AuditEvent(
        audit_event_id=audit_event_id,
        occurred_at=validated_occurred_at,
        actor_type=validated_actor_type,
        actor_id=validated_actor_id,
        action=validated_action,
        object_type=validated_object_type,
        object_id=validated_object_id,
        outcome=validated_outcome,
        reason_code=validated_reason_code,
        metadata_=validated_metadata,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
    )
    session.add(event)
    return event


def _canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _format_occurred_at(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    text = normalized.strftime("%Y-%m-%dT%H:%M:%S")
    if normalized.microsecond:
        text += f".{normalized.microsecond:06d}".rstrip("0").rstrip(".")
    return f"{text}Z"


def _require_actor_type(actor_type: str) -> str:
    if actor_type not in ev.AUDIT_ACTOR_TYPE_VALUES:
        raise AuditValidationError("actor_type must be a supported audit actor type")
    return actor_type


def _require_actor_id(actor_id: str) -> str:
    if not actor_id or len(actor_id) > 255:
        raise AuditValidationError("actor_id must be between 1 and 255 characters")
    return actor_id


def _require_action(action: str) -> str:
    if _AUDIT_ACTION_PATTERN.fullmatch(action) is None:
        raise AuditValidationError("action must match the stable audit-action pattern")
    return action


def _require_object_type(object_type: str) -> str:
    if _AUDIT_OBJECT_TYPE_PATTERN.fullmatch(object_type) is None:
        raise AuditValidationError(
            "object_type must match the stable audit-object-type pattern"
        )
    return object_type


def _require_object_id(object_id: str) -> str:
    if not object_id or len(object_id) > 255:
        raise AuditValidationError("object_id must be between 1 and 255 characters")
    return object_id


def _require_outcome(outcome: str) -> str:
    if outcome not in ev.AUDIT_OUTCOME_VALUES:
        raise AuditValidationError("outcome must be a supported audit outcome")
    return outcome


def _require_reason_code(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    if _ERROR_CODE_PATTERN.fullmatch(reason_code) is None:
        raise AuditValidationError("reason_code must match the stable error-code pattern")
    return reason_code


def _require_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise AuditValidationError("metadata must be a JSON object")
    if len(metadata) > 50:
        raise AuditValidationError("metadata must contain at most 50 properties")

    normalized: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise AuditValidationError("metadata keys must be strings")
        if not key or len(key) > 128:
            raise AuditValidationError(
                "metadata keys must be between 1 and 128 characters"
            )
        if _METADATA_KEY_PATTERN.fullmatch(key) is None:
            raise AuditValidationError("metadata keys must match the contract pattern")
        if _SECRET_KEY_PATTERN.search(key):
            raise AuditValidationError(
                f"metadata key {key!r} uses a secret-bearing field name"
            )
        normalized[key] = _require_metadata_value(value, key_path=key)
    return normalized


def _require_metadata_value(value: Any, *, key_path: str) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 4000:
            raise AuditValidationError(
                f"metadata value for {key_path} exceeds the maximum length"
            )
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                raise AuditValidationError(
                    f"metadata value for {key_path} contains secret-like material"
                )
        return value
    raise AuditValidationError(
        f"metadata value for {key_path} must be string, number, integer, boolean, or null"
    )


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuditValidationError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)
