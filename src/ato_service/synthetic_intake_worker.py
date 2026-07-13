"""Bounded command-line worker alias for development unified intake."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.blobs import BlobStore
from ato_service.db.session import (
    create_async_engine_from_url,
    create_session_factory,
)
from ato_service.intake import (
    IntakeResult,
    build_intake_lease_owner,
    drain_intake,
    require_intake_runtime,
)
from ato_service.main import RUNTIME_CONFIG_PATH_ENV_VAR, resolve_database_dsn
from ato_service.malware_scan import resolve_malware_scanner
from ato_service.runtime_config import (
    RuntimeConfig,
    load_runtime_config,
    resolve_runtime_audit_hmac_key,
)

UtcNowFactory = Callable[[], datetime]


async def drain_synthetic_intake(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    blob_store: BlobStore,
    hmac_key: bytes,
    config: RuntimeConfig,
    now_factory: UtcNowFactory | None = None,
) -> tuple[IntakeResult, ...]:
    """Drain unified intake work; preserved alias for WSL timer compatibility."""
    return await drain_intake(
        session_factory,
        config=config,
        blob_store=blob_store,
        hmac_key=hmac_key,
        scanner=resolve_malware_scanner(config),
        lease_owner=build_intake_lease_owner(token="oneshot"),
        now_factory=now_factory,
    )


async def run_synthetic_intake_worker(
    config: RuntimeConfig,
    *,
    dsn: str | None = None,
    audit_hmac_key: bytes | None = None,
    now_factory: UtcNowFactory | None = None,
) -> tuple[IntakeResult, ...]:
    """Resolve dependencies, drain unified intake, and release the DB pool."""
    require_intake_runtime(config)
    resolved_dsn = dsn if dsn is not None else resolve_database_dsn(config)
    resolved_audit_hmac_key = (
        audit_hmac_key
        if audit_hmac_key is not None
        else resolve_runtime_audit_hmac_key(config)
    )
    engine = create_async_engine_from_url(resolved_dsn)
    session_factory = create_session_factory(engine)
    try:
        return await drain_synthetic_intake(
            session_factory,
            blob_store=BlobStore(config.storage_data_path),
            hmac_key=resolved_audit_hmac_key,
            config=config,
            now_factory=now_factory,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    """Drain currently eligible unified intake work and exit."""
    parser = argparse.ArgumentParser(
        description=(
            "Process dev_local package revisions through malware scan, "
            "deterministic extraction, and awaiting_confirmation"
        )
    )
    parser.add_argument(
        "--config",
        default=os.environ.get(RUNTIME_CONFIG_PATH_ENV_VAR),
        help=f"Runtime config JSON path (default: {RUNTIME_CONFIG_PATH_ENV_VAR})",
    )
    args = parser.parse_args(argv)
    if not args.config or not str(args.config).strip():
        raise SystemExit(
            f"{RUNTIME_CONFIG_PATH_ENV_VAR} or --config must point to runtime config JSON"
        )

    config = load_runtime_config(Path(args.config))
    processed = asyncio.run(run_synthetic_intake_worker(config))
    print(f"processed {len(processed)} intake operation(s)")


if __name__ == "__main__":
    main()


__all__ = [
    "drain_synthetic_intake",
    "main",
    "run_synthetic_intake_worker",
]
