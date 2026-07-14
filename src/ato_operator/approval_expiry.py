"""Bounded approval expiry processing for the operator CLI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ato_service.export_service import process_approval_expiry
from ato_service.runtime_config import (
    RuntimeConfig,
    resolve_runtime_audit_hmac_key,
    resolve_runtime_database_dsn,
)


@dataclass(frozen=True, slots=True)
class ApprovalExpiryReport:
    pending_expired: int
    approved_expired: int
    now: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending_expired": self.pending_expired,
            "approved_expired": self.approved_expired,
            "now": self.now,
        }


async def _run_expiry(
    session: AsyncSession,
    *,
    now: datetime,
    approval_expiry_days: int,
    hmac_key: bytes,
) -> ApprovalExpiryReport:
    result = await process_approval_expiry(
        session,
        now=now,
        approval_expiry_days=approval_expiry_days,
        hmac_key=hmac_key,
    )
    await session.commit()
    return ApprovalExpiryReport(
        pending_expired=result.pending_expired,
        approved_expired=result.approved_expired,
        now=now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def process_approval_expiry_sync(
    config: RuntimeConfig,
    *,
    now: datetime | None = None,
) -> ApprovalExpiryReport:
    """Expire pending and approved export drafts past configured deadlines."""
    effective_now = now or datetime.now(timezone.utc)
    dsn = resolve_runtime_database_dsn(config)
    hmac_key = resolve_runtime_audit_hmac_key(config)
    approval_expiry_days = config.limits.approval_expiry_days

    async def _execute() -> ApprovalExpiryReport:
        engine = create_async_engine(dsn)
        session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with session_factory() as session:
                return await _run_expiry(
                    session,
                    now=effective_now,
                    approval_expiry_days=approval_expiry_days,
                    hmac_key=hmac_key,
                )
        finally:
            await engine.dispose()

    return asyncio.run(_execute())
