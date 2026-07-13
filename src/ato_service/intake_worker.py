"""Long-running background worker for synthetic JSON intake transitions."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
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
from ato_service.synthetic_intake_worker import drain_synthetic_intake

DEFAULT_POLL_INTERVAL_SECONDS = 2.0

UtcNowFactory = Callable[[], datetime]
ShutdownPredicate = Callable[[], bool]


async def run_intake_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    blob_store: BlobStore,
    hmac_key: bytes,
    now_factory: UtcNowFactory | None = None,
) -> tuple[SyntheticIntakeResult, ...]:
    """Process all currently eligible intake transitions once."""
    return await drain_synthetic_intake(
        session_factory,
        blob_store=blob_store,
        hmac_key=hmac_key,
        now_factory=now_factory,
    )


async def run_intake_worker_loop(
    config: RuntimeConfig,
    *,
    dsn: str | None = None,
    audit_hmac_key: bytes | None = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    should_stop: ShutdownPredicate | None = None,
    now_factory: UtcNowFactory | None = None,
) -> None:
    """Run synthetic intake continuously until shutdown is requested."""
    require_synthetic_intake_runtime(config)
    resolved_dsn = dsn if dsn is not None else resolve_database_dsn(config)
    resolved_audit_hmac_key = (
        audit_hmac_key
        if audit_hmac_key is not None
        else resolve_runtime_audit_hmac_key(config)
    )
    stop_requested = should_stop or (lambda: False)
    engine = create_async_engine_from_url(resolved_dsn)
    session_factory = create_session_factory(engine)
    blob_store = BlobStore(config.storage_data_path)
    try:
        while not stop_requested():
            await run_intake_cycle(
                session_factory,
                blob_store=blob_store,
                hmac_key=resolved_audit_hmac_key,
                now_factory=now_factory,
            )
            await asyncio.sleep(poll_interval_seconds)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    """Run the long-lived intake worker until interrupted."""
    parser = argparse.ArgumentParser(
        description=(
            "Continuously process dev_local synthetic JSON package revisions "
            "through scanning, extracting, and awaiting_confirmation"
        )
    )
    parser.add_argument(
        "--config",
        default=os.environ.get(RUNTIME_CONFIG_PATH_ENV_VAR),
        help=f"Runtime config JSON path (default: {RUNTIME_CONFIG_PATH_ENV_VAR})",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Seconds to wait between intake drain attempts",
    )
    args = parser.parse_args(argv)
    if not args.config or not str(args.config).strip():
        raise SystemExit(
            f"{RUNTIME_CONFIG_PATH_ENV_VAR} or --config must point to runtime config JSON"
        )
    if args.poll_interval_seconds <= 0:
        raise SystemExit("--poll-interval-seconds must be positive")

    config = load_runtime_config(Path(args.config))
    shutdown = False

    def _request_shutdown(*_args: object) -> None:
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    asyncio.run(
        run_intake_worker_loop(
            config,
            poll_interval_seconds=args.poll_interval_seconds,
            should_stop=lambda: shutdown,
            now_factory=lambda: datetime.now(timezone.utc),
        )
    )


if __name__ == "__main__":
    main()


__all__ = [
    "main",
    "run_intake_cycle",
    "run_intake_worker_loop",
]
