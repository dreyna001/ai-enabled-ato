"""Long-running background worker for unified package revision intake."""

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
)
from ato_service.intake import (
    INTAKE_LEASE_SECONDS,
    INTAKE_MAX_ATTEMPTS,
    IntakeResult,
    build_intake_lease_owner,
    drain_intake,
    require_intake_runtime,
)
from ato_service.normalization_service import (
    NormalizationDependencies,
    default_text_client_factory,
)
from ato_service.main import RUNTIME_CONFIG_PATH_ENV_VAR, resolve_database_dsn
from ato_service.malware_scan import MalwareScanner, resolve_malware_scanner
from ato_service.runtime_config import (
    RuntimeConfig,
    load_runtime_config,
    resolve_runtime_audit_hmac_key,
)

DEFAULT_POLL_INTERVAL_SECONDS = 2.0

UtcNowFactory = Callable[[], datetime]
ShutdownPredicate = Callable[[], bool]


async def run_intake_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: RuntimeConfig,
    blob_store: BlobStore,
    hmac_key: bytes,
    scanner: MalwareScanner,
    lease_owner: str,
    now_factory: UtcNowFactory | None = None,
    normalization_deps: NormalizationDependencies | None = None,
) -> tuple[IntakeResult, ...]:
    """Process all currently eligible intake operations once."""
    return await drain_intake(
        session_factory,
        config=config,
        blob_store=blob_store,
        hmac_key=hmac_key,
        scanner=scanner,
        lease_owner=lease_owner,
        now_factory=now_factory,
        normalization_deps=normalization_deps,
    )


async def run_intake_worker_loop(
    config: RuntimeConfig,
    *,
    dsn: str | None = None,
    audit_hmac_key: bytes | None = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    should_stop: ShutdownPredicate | None = None,
    now_factory: UtcNowFactory | None = None,
    lease_owner: str | None = None,
) -> None:
    """Run unified intake continuously until shutdown is requested."""
    require_intake_runtime(config)
    scanner = resolve_malware_scanner(config)
    resolved_dsn = dsn if dsn is not None else resolve_database_dsn(config)
    resolved_audit_hmac_key = (
        audit_hmac_key
        if audit_hmac_key is not None
        else resolve_runtime_audit_hmac_key(config)
    )
    resolved_lease_owner = lease_owner or build_intake_lease_owner()
    normalization_deps = NormalizationDependencies(
        config=config,
        storage_root=config.storage_data_path,
        text_client_factory=default_text_client_factory,
    )
    stop_requested = should_stop or (lambda: False)
    engine = create_async_engine_from_url(resolved_dsn)
    session_factory = create_session_factory(engine)
    blob_store = BlobStore(config.storage_data_path)
    try:
        while not stop_requested():
            await run_intake_cycle(
                session_factory,
                config=config,
                blob_store=blob_store,
                hmac_key=resolved_audit_hmac_key,
                scanner=scanner,
                lease_owner=resolved_lease_owner,
                now_factory=now_factory,
                normalization_deps=normalization_deps,
            )
            await asyncio.sleep(poll_interval_seconds)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    """Run the long-lived intake worker until interrupted."""
    parser = argparse.ArgumentParser(
        description=(
            "Continuously process package revision intake work through "
            "malware scan and deterministic extraction"
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
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "INTAKE_LEASE_SECONDS",
    "INTAKE_MAX_ATTEMPTS",
    "main",
    "run_intake_cycle",
    "run_intake_worker_loop",
]
