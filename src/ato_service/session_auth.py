"""Postgres-backed OIDC session lifecycle for portal authentication."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.db.models import AuthSession, OidcLoginState
from ato_service.runtime_config import RuntimeConfig

SESSION_COOKIE_PRODUCTION = "__Host-ato_session"
SESSION_COOKIE_DEVELOPMENT = "ato_session"
SESSION_IDLE_TIMEOUT_MINUTES = 30
SESSION_ABSOLUTE_TIMEOUT_HOURS = 8
OIDC_LOGIN_STATE_TTL_MINUTES = 10
MIN_CSRF_TOKEN_BYTES = 32


class SessionConfigurationError(Exception):
    """Raised when OIDC/session settings are incomplete for the runtime profile."""

    error_code = "reconciliation_required"


class SessionExpiredError(Exception):
    """Raised when a session cookie references an expired or missing session."""

    error_code = "authentication_required"


@dataclass(frozen=True, slots=True)
class ResolvedSessionSettings:
    """Validated session and portal settings from runtime configuration."""

    portal_public_origin: str
    oidc_issuer_url: str
    oidc_audience: str
    idle_timeout: timedelta
    absolute_timeout: timedelta
    secure_cookie: bool


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def resolve_session_settings(config: RuntimeConfig) -> ResolvedSessionSettings | None:
    """Return session settings when identity is configured; otherwise None."""
    document = config.document
    identity_mode = document.get("IDENTITY_PROVIDER_MODE")
    if identity_mode != "oidc":
        return None

    portal_origin = document.get("PORTAL_PUBLIC_ORIGIN")
    issuer_url = document.get("OIDC_ISSUER_URL")
    audience = document.get("OIDC_AUDIENCE")
    if not isinstance(portal_origin, str) or not portal_origin.strip():
        raise SessionConfigurationError("PORTAL_PUBLIC_ORIGIN is required for OIDC")
    if not isinstance(issuer_url, str) or not issuer_url.strip():
        raise SessionConfigurationError("OIDC_ISSUER_URL is required for OIDC")
    if not isinstance(audience, str) or not audience.strip():
        raise SessionConfigurationError("OIDC_AUDIENCE is required for OIDC")

    secure_cookie = portal_origin.startswith("https://")
    return ResolvedSessionSettings(
        portal_public_origin=portal_origin.strip(),
        oidc_issuer_url=issuer_url.strip(),
        oidc_audience=audience.strip(),
        idle_timeout=timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES),
        absolute_timeout=timedelta(hours=SESSION_ABSOLUTE_TIMEOUT_HOURS),
        secure_cookie=secure_cookie,
    )


def session_cookie_name(*, secure_cookie: bool) -> str:
    """Return the cookie name appropriate for the portal transport."""
    if secure_cookie:
        return SESSION_COOKIE_PRODUCTION
    return SESSION_COOKIE_DEVELOPMENT


def _new_csrf_token() -> str:
    return secrets.token_urlsafe(MIN_CSRF_TOKEN_BYTES)


def principal_from_session(session_row: AuthSession) -> AuthenticatedPrincipal:
    """Map a persisted session row to the injected API principal."""
    return AuthenticatedPrincipal(
        actor_id=session_row.actor_id,
        groups=tuple(session_row.groups),
        csrf_token=session_row.csrf_token,
        allowed_origins=(session_row.portal_origin,),
    )


async def create_auth_session(
    db_session: AsyncSession,
    *,
    actor_id: str,
    groups: list[str],
    portal_origin: str,
    settings: ResolvedSessionSettings,
    now: datetime,
) -> AuthSession:
    """Persist a new authenticated session and return the row."""
    validated_now = _require_aware_utc(now, field_name="now")
    session_row = AuthSession(
        session_id=uuid.uuid4(),
        actor_id=actor_id,
        groups=list(groups),
        csrf_token=_new_csrf_token(),
        portal_origin=portal_origin,
        created_at=validated_now,
        last_seen_at=validated_now,
        absolute_expires_at=validated_now + settings.absolute_timeout,
    )
    db_session.add(session_row)
    await db_session.flush()
    return session_row


async def load_valid_session(
    db_session: AsyncSession,
    *,
    session_id: uuid.UUID,
    settings: ResolvedSessionSettings,
    now: datetime,
) -> AuthSession:
    """Load and touch a session when it is still valid."""
    validated_now = _require_aware_utc(now, field_name="now")
    result = await db_session.execute(
        select(AuthSession).where(AuthSession.session_id == session_id)
    )
    session_row = result.scalar_one_or_none()
    if session_row is None:
        raise SessionExpiredError()

    if validated_now >= session_row.absolute_expires_at:
        await db_session.delete(session_row)
        raise SessionExpiredError()

    idle_deadline = session_row.last_seen_at + settings.idle_timeout
    if validated_now >= idle_deadline:
        await db_session.delete(session_row)
        raise SessionExpiredError()

    session_row.last_seen_at = validated_now
    await db_session.flush()
    return session_row


async def delete_auth_session(
    db_session: AsyncSession,
    *,
    session_id: uuid.UUID,
) -> None:
    """Remove a session row if it exists."""
    await db_session.execute(
        delete(AuthSession).where(AuthSession.session_id == session_id)
    )


async def create_oidc_login_state(
    db_session: AsyncSession,
    *,
    now: datetime,
) -> OidcLoginState:
    """Create a short-lived PKCE login state row."""
    validated_now = _require_aware_utc(now, field_name="now")
    login_state = OidcLoginState(
        state_token=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(48),
        nonce=secrets.token_urlsafe(32),
        created_at=validated_now,
        expires_at=validated_now + timedelta(minutes=OIDC_LOGIN_STATE_TTL_MINUTES),
    )
    db_session.add(login_state)
    await db_session.flush()
    return login_state


async def consume_oidc_login_state(
    db_session: AsyncSession,
    *,
    state_token: str,
    now: datetime,
) -> OidcLoginState:
    """Load and delete a login state when it is still valid."""
    validated_now = _require_aware_utc(now, field_name="now")
    result = await db_session.execute(
        select(OidcLoginState).where(OidcLoginState.state_token == state_token)
    )
    login_state = result.scalar_one_or_none()
    if login_state is None or validated_now >= login_state.expires_at:
        raise SessionExpiredError()
    await db_session.delete(login_state)
    await db_session.flush()
    return login_state


async def purge_expired_auth_artifacts(
    db_session: AsyncSession,
    *,
    now: datetime,
) -> tuple[int, int]:
    """Delete expired sessions and login states; returns (sessions, login_states)."""
    validated_now = _require_aware_utc(now, field_name="now")
    sessions_result = await db_session.execute(
        delete(AuthSession).where(AuthSession.absolute_expires_at <= validated_now)
    )
    login_states_result = await db_session.execute(
        delete(OidcLoginState).where(OidcLoginState.expires_at <= validated_now)
    )
    return (
        int(sessions_result.rowcount or 0),
        int(login_states_result.rowcount or 0),
    )


def session_cookie_attributes(
    *,
    settings: ResolvedSessionSettings,
    max_age_seconds: int | None = None,
) -> dict[str, Any]:
    """Return Set-Cookie attributes for a portal session."""
    attributes: dict[str, Any] = {
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }
    if settings.secure_cookie:
        attributes["secure"] = True
    if max_age_seconds is not None:
        attributes["max_age"] = max_age_seconds
    return attributes
