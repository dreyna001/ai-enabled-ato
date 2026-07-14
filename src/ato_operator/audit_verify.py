"""Audit chain verification for the operator CLI."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ato_service.audit_chain_verify import (
    AuditChainVerificationReport,
    verify_audit_chain_session,
)
from ato_service.runtime_config import RuntimeConfig, resolve_runtime_audit_hmac_key, resolve_runtime_database_dsn


async def verify_audit_chain(config: RuntimeConfig) -> AuditChainVerificationReport:
    """Verify the append-only audit hash chain when database access is available."""
    try:
        hmac_key = resolve_runtime_audit_hmac_key(config)
        dsn = resolve_runtime_database_dsn(config)
    except Exception as exc:
        return AuditChainVerificationReport(
            passed=False,
            verified_events=0,
            total_events=0,
            genesis_hash="0" * 64,
            head_hash=None,
            checkpoints=(),
            failure_reason=None,
            failure_event_index=None,
            failure_audit_event_id=None,
            verification_scope="global",
            matching_events=0,
            detail=f"dependency resolution failed: {exc.__class__.__name__}",
        )

    engine = create_async_engine(dsn, pool_pre_ping=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            return await verify_audit_chain_session(session, hmac_key=hmac_key)
    except Exception as exc:
        return AuditChainVerificationReport(
            passed=False,
            verified_events=0,
            total_events=0,
            genesis_hash="0" * 64,
            head_hash=None,
            checkpoints=(),
            failure_reason=None,
            failure_event_index=None,
            failure_audit_event_id=None,
            verification_scope="global",
            matching_events=0,
            detail=f"database query failed: {exc.__class__.__name__}",
        )
    finally:
        await engine.dispose()


def verify_audit_chain_sync(config: RuntimeConfig) -> AuditChainVerificationReport:
    return asyncio.run(verify_audit_chain(config))


def format_verify_audit_report(report: AuditChainVerificationReport) -> str:
    """Return a single-line operator summary without secret material."""
    failure = report.failure_reason.value if report.failure_reason is not None else "none"
    return (
        "verify-audit "
        f"passed={report.passed} "
        f"events={report.verified_events}/{report.total_events} "
        f"head={report.head_hash or 'none'} "
        f"failure={failure} "
        f"detail={report.detail}"
    )


__all__ = [
    "AuditChainVerificationReport",
    "format_verify_audit_report",
    "verify_audit_chain",
    "verify_audit_chain_sync",
]
