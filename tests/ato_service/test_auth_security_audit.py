"""Authentication security audit event tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ato_service.auth_security_audit import (
    record_csrf_rejected,
    record_login_succeeded,
    record_logout_succeeded,
    record_session_revoked,
)
from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.db.models import AuditEvent

HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES


def _run(awaitable):
    return asyncio.run(awaitable)


class _RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.execute_calls: list[object] = []

    async def execute(self, statement: object) -> MagicMock:
        self.execute_calls.append(statement)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    def add(self, obj: object) -> None:
        self.added.append(obj)


def test_record_login_succeeded_writes_redacted_audit_event() -> None:
    session = _RecordingSession()
    session_id = uuid.uuid4()
    _run(
        record_login_succeeded(
            session,  # type: ignore[arg-type]
            hmac_key=HMAC_KEY,
            actor_id="user-123",
            session_id=session_id,
            now=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
        )
    )
    assert len(session.added) == 1
    event = session.added[0]
    assert isinstance(event, AuditEvent)
    assert event.action == "auth.login_succeeded"
    assert event.actor_id == "user-123"
    assert event.object_id == str(session_id).lower()
    assert "token" not in event.metadata
    assert "secret" not in str(event.metadata).lower()


def test_record_logout_revocation_and_csrf_audit_events() -> None:
    session = _RecordingSession()
    session_id = uuid.uuid4()
    _run(
        record_logout_succeeded(
            session,  # type: ignore[arg-type]
            hmac_key=HMAC_KEY,
            actor_id="user-123",
            session_id=session_id,
        )
    )
    _run(
        record_session_revoked(
            session,  # type: ignore[arg-type]
            hmac_key=HMAC_KEY,
            actor_id="user-123",
            session_id=session_id,
            reason="idle_timeout",
        )
    )
    _run(
        record_csrf_rejected(
            session,  # type: ignore[arg-type]
            hmac_key=HMAC_KEY,
            actor_id="user-123",
            route="/api/v1/auth/logout",
        )
    )
    actions = [event.action for event in session.added if isinstance(event, AuditEvent)]
    assert actions == [
        "auth.logout_succeeded",
        "auth.session_revoked",
        "auth.csrf_rejected",
    ]
    for event in session.added:
        assert isinstance(event, AuditEvent)
        metadata_blob = str(event.metadata)
        assert "bearer" not in metadata_blob.lower()
        assert "authorization" not in metadata_blob.lower()
