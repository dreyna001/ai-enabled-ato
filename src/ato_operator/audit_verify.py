"""Audit chain verification for the operator CLI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ato_service.audit import GENESIS_PREVIOUS_EVENT_HASH, verify_audit_event_hash
from ato_service.db.models import AuditEvent
from ato_service.runtime_config import RuntimeConfig, resolve_runtime_audit_hmac_key, resolve_runtime_database_dsn


@dataclass(frozen=True, slots=True)
class AuditVerificationReport:
    verified_events: int
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verified_events": self.verified_events,
            "detail": self.detail,
        }


async def _verify_chain(session: AsyncSession, *, hmac_key: bytes) -> AuditVerificationReport:
    stmt = select(AuditEvent).order_by(AuditEvent.occurred_at.asc(), AuditEvent.audit_event_id.asc())
    result = await session.execute(stmt)
    events = list(result.scalars())
    if not events:
        return AuditVerificationReport(
            verified_events=0,
            passed=True,
            detail="no audit events present",
        )

    expected_previous = GENESIS_PREVIOUS_EVENT_HASH
    for index, event in enumerate(events):
        if event.previous_event_hash != expected_previous:
            return AuditVerificationReport(
                verified_events=index,
                passed=False,
                detail=f"chain break at event index {index}",
            )
        if not verify_audit_event_hash(
            hmac_key=hmac_key,
            audit_event_id=event.audit_event_id,
            occurred_at=event.occurred_at,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            action=event.action,
            object_type=event.object_type,
            object_id=event.object_id,
            outcome=event.outcome,
            reason_code=event.reason_code,
            metadata=event.metadata or {},
            previous_event_hash=event.previous_event_hash,
            event_hash=event.event_hash,
        ):
            return AuditVerificationReport(
                verified_events=index,
                passed=False,
                detail=f"HMAC mismatch at event index {index}",
            )
        expected_previous = event.event_hash

    return AuditVerificationReport(
        verified_events=len(events),
        passed=True,
        detail="chain intact",
    )


async def verify_audit_chain(config: RuntimeConfig) -> AuditVerificationReport:
    """Verify the append-only audit hash chain when database access is available."""
    try:
        hmac_key = resolve_runtime_audit_hmac_key(config)
        dsn = resolve_runtime_database_dsn(config)
    except Exception as exc:
        return AuditVerificationReport(
            verified_events=0,
            passed=False,
            detail=f"dependency resolution failed: {exc.__class__.__name__}",
        )

    engine = create_async_engine(dsn, pool_pre_ping=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            return await _verify_chain(session, hmac_key=hmac_key)
    except Exception as exc:
        return AuditVerificationReport(
            verified_events=0,
            passed=False,
            detail=f"database query failed: {exc.__class__.__name__}",
        )
    finally:
        await engine.dispose()


def verify_audit_chain_sync(config: RuntimeConfig) -> AuditVerificationReport:
    return asyncio.run(verify_audit_chain(config))


__all__ = ["AuditVerificationReport", "verify_audit_chain", "verify_audit_chain_sync"]
