"""Bounded security audit events for authentication and session lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event

AUTH_SECURITY_OBJECT_TYPE = "auth_session"
ANONYMOUS_ACTOR_ID = "anonymous"


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = frozenset(
        {
            "route",
            "reason",
            "sessions_purged",
            "login_states_purged",
            "detected_headers",
            "revocation_reason",
        }
    )
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in allowed_keys:
            continue
        if key == "detected_headers" and isinstance(value, (list, tuple)):
            sanitized[key] = [
                item
                for item in value
                if isinstance(item, str) and item in UNTRUSTED_IDENTITY_HEADER_NAMES
            ]
            continue
        if isinstance(value, str) and value.strip():
            sanitized[key] = value.strip()
        elif isinstance(value, int) and value >= 0:
            sanitized[key] = value
    return sanitized


UNTRUSTED_IDENTITY_HEADER_NAMES = frozenset(
    {
        "x-remote-user",
        "x-forwarded-user",
        "remote-user",
        "x-user",
        "x-user-id",
        "x-auth-request-user",
        "x-groups",
        "x-forwarded-groups",
    }
)


async def record_auth_security_event(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    action: str,
    actor_id: str,
    object_id: str,
    outcome: str,
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Append a redacted authentication security audit event."""
    effective_now = now or datetime.now(timezone.utc)
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=actor_id,
        action=action,
        object_type=AUTH_SECURITY_OBJECT_TYPE,
        object_id=object_id,
        outcome=outcome,
        reason_code=reason_code,
        metadata=_sanitize_metadata(metadata or {}),
        occurred_at=effective_now,
    )


async def record_login_succeeded(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    actor_id: str,
    session_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    await record_auth_security_event(
        session,
        hmac_key=hmac_key,
        action="auth.login_succeeded",
        actor_id=actor_id,
        object_id=str(session_id).lower(),
        outcome="succeeded",
        metadata={"route": "/api/v1/auth/callback"},
        now=now,
    )


async def record_logout_succeeded(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    actor_id: str,
    session_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    await record_auth_security_event(
        session,
        hmac_key=hmac_key,
        action="auth.logout_succeeded",
        actor_id=actor_id,
        object_id=str(session_id).lower(),
        outcome="succeeded",
        metadata={"route": "/api/v1/auth/logout"},
        now=now,
    )


async def record_session_revoked(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    actor_id: str,
    session_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> None:
    await record_auth_security_event(
        session,
        hmac_key=hmac_key,
        action="auth.session_revoked",
        actor_id=actor_id,
        object_id=str(session_id).lower(),
        outcome="succeeded",
        metadata={"revocation_reason": reason},
        now=now,
    )


async def record_csrf_rejected(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    actor_id: str,
    route: str,
    now: datetime | None = None,
) -> None:
    await record_auth_security_event(
        session,
        hmac_key=hmac_key,
        action="auth.csrf_rejected",
        actor_id=actor_id,
        object_id=uuid.uuid4().hex,
        outcome="denied",
        reason_code="csrf_validation_failed",
        metadata={"route": route},
        now=now,
    )


async def record_identity_header_rejected(
    session: AsyncSession,
    *,
    hmac_key: bytes,
    route: str,
    detected_headers: tuple[str, ...],
    now: datetime | None = None,
) -> None:
    await record_auth_security_event(
        session,
        hmac_key=hmac_key,
        action="auth.identity_header_rejected",
        actor_id=ANONYMOUS_ACTOR_ID,
        object_id=uuid.uuid4().hex,
        outcome="denied",
        reason_code="authorization_denied",
        metadata={"route": route, "detected_headers": list(detected_headers)},
        now=now,
    )
