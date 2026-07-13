"""FastAPI application factory for the ATO product service."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.types import Lifespan

from ato_service.app_runtime import (
    RUNTIME_STATE_ATTR,
    AppRuntimeSnapshot,
    AppRuntimeState,
)
from ato_service.auth_middleware import SessionAuthenticationMiddleware
from ato_service.authority_manifest import verify_authority_manifest
from ato_service.db.dsn import require_database_dsn_from_env
from ato_service.db.session import (
    create_async_engine_from_url,
    create_session_factory,
    require_postgresql_url,
)
from ato_service.local_env import load_local_env_file
from ato_service.health import (
    HEALTH_PATH_SERVER_OVERRIDE,
    ReadinessProbe,
    create_health_router,
)
from ato_service.problems import register_problem_handlers
from ato_service.readiness import ReadinessDependencies, create_readiness_probe
from ato_service.runtime_config import (
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config,
    resolve_runtime_audit_hmac_key,
    resolve_runtime_database_dsn,
)

RUNTIME_CONFIG_PATH_ENV_VAR = "ATO_RUNTIME_CONFIG_PATH"
AUTHORITY_MANIFEST_PATH_ENV_VAR = "ATO_AUTHORITY_MANIFEST_PATH"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
API_VERSION_PREFIX = "/api/v1"


@dataclass(slots=True)
class _AppRuntime:
    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise RuntimeConfigError("Could not locate project root (pyproject.toml not found)")


def default_authority_manifest_path(*, project_root: Path | None = None) -> Path:
    """Return the pinned authority manifest path for the repository."""
    root = project_root or _find_project_root()
    override = os.environ.get(AUTHORITY_MANIFEST_PATH_ENV_VAR)
    if override and override.strip():
        return Path(override.strip()).resolve()
    return (root / "docs" / "contracts" / "authority-manifest.json").resolve()


def resolve_database_dsn(config: RuntimeConfig) -> str:
    """Resolve the PostgreSQL DSN from runtime config or the protected-file contract."""
    reference = config.document.get("DATABASE_DSN_CREDENTIAL_REFERENCE")
    if isinstance(reference, dict):
        return resolve_runtime_database_dsn(config)
    if config.runtime_profile == "dev_local":
        return require_database_dsn_from_env()
    raise RuntimeConfigError(
        "DATABASE_DSN_CREDENTIAL_REFERENCE is required to resolve the database DSN"
    )


def _resolve_audit_hmac_key_for_startup(
    config: RuntimeConfig,
    *,
    injected_key: bytes | None,
) -> bytes | None:
    """Resolve audit HMAC key bytes for startup, honoring explicit test injection."""
    if injected_key is not None:
        return injected_key

    reference = config.document.get("AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE")
    if isinstance(reference, dict):
        return resolve_runtime_audit_hmac_key(config)

    if config.runtime_profile == "onprem_production":
        raise RuntimeConfigError(
            "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE is required for onprem_production"
        )

    return None


def _custom_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=app.servers,
    )
    rewritten_paths: dict[str, Any] = {}
    for path, path_item in schema["paths"].items():
        if path.startswith(f"{API_VERSION_PREFIX}/"):
            rewritten_paths[path.removeprefix(API_VERSION_PREFIX)] = path_item
        else:
            rewritten_paths[path] = path_item
    schema["paths"] = rewritten_paths

    for health_path in ("/health/live", "/health/ready"):
        path_item = schema["paths"][health_path]
        path_item["get"]["security"] = []
        path_item["get"]["servers"] = HEALTH_PATH_SERVER_OVERRIDE
    app.openapi_schema = schema
    return schema


def create_app(
    *,
    readiness_probe: ReadinessProbe,
    lifespan: Lifespan[FastAPI] | None = None,
    runtime_state: AppRuntimeState | None = None,
) -> FastAPI:
    """Create the service application with injected readiness dependencies."""
    app = FastAPI(
        title="ATO Evidence Analysis Portal API",
        version="1.0.0",
        description=(
            "Published P-1 API contract. Implemented in this build: health "
            "endpoints, OIDC session auth, the P1.1 Systems + PackageRevision "
            "slice (systems, package-revisions, file upload, finalize, confirm), "
            "and fact proposal review. Other contract paths remain unimplemented."
        ),
        servers=[{"url": "/api/v1"}],
        lifespan=lifespan,
    )
    register_problem_handlers(app)
    app.add_middleware(SessionAuthenticationMiddleware)
    app.include_router(create_health_router(readiness_probe))
    from ato_service.api_router import create_api_router
    from ato_service.auth_router import create_auth_router

    app.include_router(create_api_router(), prefix="/api/v1")
    app.include_router(create_auth_router(), prefix="/api/v1")
    app.openapi = lambda: _custom_openapi(app)
    if runtime_state is not None:
        setattr(app.state, RUNTIME_STATE_ATTR, runtime_state)
    return app


def _mount_embedded_dev_oidc_if_configured(
    app: FastAPI,
    config: RuntimeConfig,
) -> None:
    """Attach the loopback dev OIDC issuer when configured for dev_local."""
    from ato_service.oidc_auth import DEV_OIDC_PATH_PREFIX, create_dev_oidc_router, is_embedded_dev_oidc_issuer
    from ato_service.session_auth import resolve_session_settings

    session_settings = resolve_session_settings(config)
    if session_settings is None:
        return
    if not is_embedded_dev_oidc_issuer(config, session_settings):
        return
    app.include_router(create_dev_oidc_router(), prefix=DEV_OIDC_PATH_PREFIX)


def build_app_from_config(
    config: RuntimeConfig,
    *,
    dsn: str | None = None,
    authority_manifest_path: Path | None = None,
    project_root: Path | None = None,
    audit_hmac_key: bytes | None = None,
) -> FastAPI:
    """Wire runtime config, database engine lifecycle, and readiness into an app."""
    resolved_dsn = (
        require_postgresql_url(dsn) if dsn is not None else resolve_database_dsn(config)
    )
    root = project_root or _find_project_root()
    manifest_path = authority_manifest_path or default_authority_manifest_path(
        project_root=root
    )
    runtime = _AppRuntime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        manifest = await asyncio.to_thread(
            verify_authority_manifest,
            manifest_path,
            project_root=root,
        )
        resolved_audit_hmac_key = await asyncio.to_thread(
            _resolve_audit_hmac_key_for_startup,
            config,
            injected_key=audit_hmac_key,
        )
        runtime.engine = create_async_engine_from_url(resolved_dsn)
        runtime.session_factory = create_session_factory(runtime.engine)
        setattr(
            _app.state,
            RUNTIME_STATE_ATTR,
            AppRuntimeState(
                snapshot=AppRuntimeSnapshot(
                    config=config,
                    storage_root=config.storage_data_path,
                    authority_manifest_id=manifest["manifest_id"],
                    project_root=root,
                ),
                session_factory=runtime.session_factory,
                audit_hmac_key=resolved_audit_hmac_key,
            ),
        )
        try:
            yield
        finally:
            runtime_state = getattr(_app.state, RUNTIME_STATE_ATTR, None)
            if isinstance(runtime_state, AppRuntimeState):
                runtime_state.session_factory = None
                runtime_state.audit_hmac_key = None
            runtime.session_factory = None
            if runtime.engine is not None:
                await runtime.engine.dispose()
                runtime.engine = None

    readiness_deps = ReadinessDependencies(
        config=config,
        authority_manifest_path=manifest_path,
        project_root=root,
        get_engine=lambda: runtime.engine,
    )

    app = create_app(
        readiness_probe=create_readiness_probe(readiness_deps),
        lifespan=lifespan,
    )
    _mount_embedded_dev_oidc_if_configured(app, config)
    return app


def load_app_from_config_path(
    config_path: Path | str,
    *,
    base_dir: Path | None = None,
    authority_manifest_path: Path | None = None,
    project_root: Path | None = None,
) -> FastAPI:
    """Load runtime configuration from disk and build the service application."""
    config = load_runtime_config(config_path, base_dir=base_dir)
    root = project_root or _find_project_root()
    return build_app_from_config(
        config,
        authority_manifest_path=authority_manifest_path,
        project_root=root,
    )


def main(argv: list[str] | None = None) -> None:
    """Run the service with uvicorn using explicit runtime configuration."""
    parser = argparse.ArgumentParser(description="Run the ATO service API")
    parser.add_argument(
        "--config",
        default=os.environ.get(RUNTIME_CONFIG_PATH_ENV_VAR),
        help=f"Runtime config JSON path (default: {RUNTIME_CONFIG_PATH_ENV_VAR})",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("ATO_HOST", DEFAULT_HOST),
        help="Bind host (default: loopback)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ATO_PORT", str(DEFAULT_PORT))),
        help="Bind port",
    )
    args = parser.parse_args(argv)
    load_local_env_file()

    if not args.config or not str(args.config).strip():
        raise SystemExit(
            f"{RUNTIME_CONFIG_PATH_ENV_VAR} or --config must point to runtime config JSON"
        )

    app = load_app_from_config_path(args.config)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
