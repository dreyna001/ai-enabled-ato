"""Bounded command-line worker for development synthetic JSON intake."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.blobs import BlobStore
from ato_service.db.session import (
    create_async_engine_from_url,
    create_session_factory,
    session_scope,
)
from ato_service.main import RUNTIME_CONFIG_PATH_ENV_VAR, resolve_database_dsn
from ato_service.runtime_config import (
    RuntimeConfig,
    load_runtime_config,
    resolve_runtime_audit_hmac_key,
)
from ato_service.synthetic_intake import (
    SyntheticIntakeResult,
    process_next_synthetic_intake,
    require_synthetic_intake_runtime,
)

UtcNowFactory = Callable[[], datetime]


async def drain_synthetic_intake(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    blob_store: BlobStore,
    hmac_key: bytes,
    now_factory: UtcNowFactory | None = None,
) -> tuple[SyntheticIntakeResult, ...]:
    """Process currently eligible revisions until no transition can be claimed."""
    current_time = now_factory or (lambda: datetime.now(timezone.utc))
    processed: list[SyntheticIntakeResult] = []
    while True:
        async with session_scope(session_factory) as session:
            result = await process_next_synthetic_intake(
                session,
                blob_store=blob_store,
                hmac_key=hmac_key,
                now=current_time(),
            )
        if result is None:
            return tuple(processed)
        processed.append(result)


async def run_synthetic_intake_worker(
    config: RuntimeConfig,
    *,
    dsn: str | None = None,
    audit_hmac_key: bytes | None = None,
    now_factory: UtcNowFactory | None = None,
) -> tuple[SyntheticIntakeResult, ...]:
    """Resolve dependencies, drain synthetic intake, and release the DB pool."""
    require_synthetic_intake_runtime(config)
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
            now_factory=now_factory,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    """Drain currently eligible synthetic JSON intake work and exit."""
    parser = argparse.ArgumentParser(
        description=(
            "Process dev_local synthetic JSON package revisions through "
            "scanning, extracting, and awaiting_confirmation"
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
    print(f"processed {len(processed)} synthetic intake transition(s)")


if __name__ == "__main__":
    main()


__all__ = [
    "drain_synthetic_intake",
    "main",
    "run_synthetic_intake_worker",
]
