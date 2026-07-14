"""Session lifecycle, cookie, and purge tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from ato_service.db.models import AuthSession
from ato_service.runtime_config import RuntimeConfig
from ato_service.session_auth import (
    OIDC_LOGIN_STATE_TTL_MINUTES,
    SESSION_COOKIE_DEVELOPMENT,
    SESSION_COOKIE_PRODUCTION,
    SessionExpiredError,
    consume_oidc_login_state,
    create_oidc_login_state,
    load_valid_session,
    purge_expired_auth_artifacts,
    resolve_session_settings,
    session_cookie_attributes,
    session_cookie_name,
)


def _run(awaitable):
    return asyncio.run(awaitable)


def _runtime_config(*, profile: str = "onprem_production") -> RuntimeConfig:
    return RuntimeConfig(
        runtime_profile=profile,
        storage_data_path=__import__("pathlib").Path("/data/ato-storage"),
        document={
            "runtime_profile": profile,
            "STORAGE_DATA_PATH": "/data/ato-storage",
            "IDENTITY_PROVIDER_MODE": "oidc",
            "PORTAL_PUBLIC_ORIGIN": "https://portal.example",
            "OIDC_ISSUER_URL": "https://idp.example.internal",
            "OIDC_AUDIENCE": "ato-analyzer",
            "SESSION_IDLE_TIMEOUT_MINUTES": 30,
            "SESSION_ABSOLUTE_TIMEOUT_HOURS": 8,
        },
    )


def _settings():
    resolved = resolve_session_settings(_runtime_config())
    assert resolved is not None
    return resolved


def _session_row(*, actor_id: str = "actor-1") -> AuthSession:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    settings = _settings()
    return AuthSession(
        session_id=uuid.uuid4(),
        actor_id=actor_id,
        groups=["owners"],
        csrf_token="c" * 32,
        portal_origin=settings.portal_public_origin,
        created_at=now,
        last_seen_at=now,
        absolute_expires_at=now + settings.absolute_timeout,
    )


def test_create_oidc_login_state_expires_after_ttl() -> None:
    db_session = AsyncMock()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    login_state = _run(create_oidc_login_state(db_session, now=now))
    assert login_state.expires_at == now + timedelta(minutes=OIDC_LOGIN_STATE_TTL_MINUTES)
    db_session.add.assert_called_once()
    db_session.flush.assert_awaited_once()


def test_consume_oidc_login_state_rejects_expired_row() -> None:
    db_session = AsyncMock()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    login_state = _run(create_oidc_login_state(db_session, now=now))
    expired_result = MagicMock()
    expired_result.scalar_one_or_none.return_value = login_state
    db_session.execute = AsyncMock(return_value=expired_result)
    with __import__("pytest").raises(SessionExpiredError) as exc_info:
        _run(
            consume_oidc_login_state(
                db_session,
                state_token=login_state.state_token,
                now=now + timedelta(minutes=OIDC_LOGIN_STATE_TTL_MINUTES, seconds=1),
            )
        )
    assert exc_info.value.revocation_reason == "login_state_expired"


def test_load_valid_session_rejects_idle_timeout() -> None:
    settings = _settings()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    session_row = _session_row()
    result = MagicMock()
    result.scalar_one_or_none.return_value = session_row
    db_session = AsyncMock()
    db_session.execute = AsyncMock(return_value=result)
    with __import__("pytest").raises(SessionExpiredError) as exc_info:
        _run(
            load_valid_session(
                db_session,
                session_id=session_row.session_id,
                settings=settings,
                now=now + timedelta(minutes=30, seconds=1),
            )
        )
    assert exc_info.value.revocation_reason == "idle_timeout"
    assert exc_info.value.actor_id == "actor-1"
    db_session.delete.assert_awaited_once_with(session_row)


def test_load_valid_session_rejects_absolute_timeout() -> None:
    settings = _settings()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    session_row = _session_row(actor_id="actor-2")
    session_row.absolute_expires_at = now - timedelta(seconds=1)
    result = MagicMock()
    result.scalar_one_or_none.return_value = session_row
    db_session = AsyncMock()
    db_session.execute = AsyncMock(return_value=result)
    with __import__("pytest").raises(SessionExpiredError) as exc_info:
        _run(
            load_valid_session(
                db_session,
                session_id=session_row.session_id,
                settings=settings,
                now=now,
            )
        )
    assert exc_info.value.revocation_reason == "absolute_timeout"
    assert exc_info.value.actor_id == "actor-2"


def test_session_cookie_name_uses_host_prefix_for_https() -> None:
    settings = _settings()
    assert session_cookie_name(secure_cookie=True) == SESSION_COOKIE_PRODUCTION
    assert session_cookie_name(secure_cookie=False) == SESSION_COOKIE_DEVELOPMENT


def test_session_cookie_attributes_are_secure_for_https_portal() -> None:
    settings = _settings()
    attributes = session_cookie_attributes(settings=settings)
    assert attributes["httponly"] is True
    assert attributes["samesite"] == "lax"
    assert attributes["secure"] is True
    assert attributes["path"] == "/"
    assert "domain" not in attributes


def test_purge_expired_auth_artifacts_returns_delete_counts() -> None:
    db_session = AsyncMock()
    sessions_result = MagicMock()
    sessions_result.rowcount = 2
    login_states_result = MagicMock()
    login_states_result.rowcount = 3
    db_session.execute = AsyncMock(side_effect=[sessions_result, login_states_result])
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    assert _run(purge_expired_auth_artifacts(db_session, now=now)) == (2, 3)
