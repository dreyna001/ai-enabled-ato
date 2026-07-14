"""Bounded expired auth artifact purge for the operator CLI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ato_service.auth_security_audit import record_auth_security_event
from ato_service.runtime_config import (
    RuntimeConfig,
    resolve_runtime_audit_hmac_key,
    resolve_runtime_database_dsn,
)
from ato_service.session_auth import purge_expired_auth_artifacts


@dataclass(frozen=True, slots=True)
class AuthPurgeReport:
    sessions_purged: int
    login_states_purged: int
    now: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessions_purged": self.sessions_purged,
            "login_states_purged": self.login_states_purged,
            "now": self.now,
        }


async def _run_purge(
    session: AsyncSession,
    *,
    now: datetime,
    hmac_key: bytes,
) -> AuthPurgeReport:
    sessions_purged, login_states_purged = await purge_expired_auth_artifacts(
        session,
        now=now,
    )
    if sessions_purged or login_states_purged:
        await record_auth_security_event(
            session,
            hmac_key=hmac_key,
            action="auth.artifacts_purged",
            actor_id="ato-operator",
            object_id=now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            outcome="succeeded",
            metadata={
                "sessions_purged": sessions_purged,
                "login_states_purged": login_states_purged,
            },
            now=now,
        )
    await session.commit()
    return AuthPurgeReport(
        sessions_purged=sessions_purged,
        login_states_purged=login_states_purged,
        now=now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def purge_expired_auth_artifacts_sync(
    config: RuntimeConfig,
    *,
    now: datetime | None = None,
) -> AuthPurgeReport:
    """Delete expired auth sessions and OIDC login states."""
    effective_now = now or datetime.now(timezone.utc)
    dsn = resolve_runtime_database_dsn(config)
    hmac_key = resolve_runtime_audit_hmac_key(config)

    async def _execute() -> AuthPurgeReport:
        engine = create_async_engine(dsn)
        session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with session_factory() as session:
                return await _run_purge(session, now=effective_now, hmac_key=hmac_key)
        finally:
            await engine.dispose()

    return asyncio.run(_execute())
