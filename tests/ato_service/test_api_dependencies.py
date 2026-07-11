"""Tests for API runtime dependency helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from ato_service.api_dependencies import (
    AuditDependencyUnavailableError,
    DatabaseSessionUnavailableError,
    RuntimeStateUnavailableError,
    get_audit_hmac_key,
    get_blob_store,
    get_db_session,
    get_runtime_state,
)
from ato_service.blobs import BlobStore
from ato_service.main import (
    RUNTIME_STATE_ATTR,
    AppRuntimeSnapshot,
    AppRuntimeState,
    create_app,
)
from ato_service.audit import AuditUnavailableError, AuditValidationError
from ato_service.problems import PROBLEM_MEDIA_TYPE, register_problem_handlers
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]


def _request_for_app(app: FastAPI) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    return Request(scope)


_VALID_AUDIT_HMAC_KEY = b"a" * 32


def _runtime_state(
    tmp_path: Path,
    *,
    session_factory: MagicMock | None = MagicMock(),
    audit_hmac_key: bytes | None = _VALID_AUDIT_HMAC_KEY,
) -> AppRuntimeState:
    config = load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": str(tmp_path / "storage"),
        },
        base_dir=tmp_path,
    )
    return AppRuntimeState(
        snapshot=AppRuntimeSnapshot(
            config=config,
            storage_root=config.storage_data_path,
            authority_manifest_id="fixture.draft",
            project_root=ROOT,
        ),
        session_factory=session_factory,
        audit_hmac_key=audit_hmac_key,
    )


def test_get_runtime_state_returns_attached_state(tmp_path: Path) -> None:
    runtime_state = _runtime_state(tmp_path)
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=runtime_state,
    )
    request = _request_for_app(app)

    assert get_runtime_state(request) is runtime_state


def test_get_runtime_state_fails_closed_when_missing() -> None:
    app = create_app(readiness_probe=AsyncMock(return_value={}))
    request = _request_for_app(app)

    with pytest.raises(RuntimeStateUnavailableError, match="not available"):
        get_runtime_state(request)


def test_get_db_session_commits_and_closes_on_success(tmp_path: Path) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session_factory = MagicMock(return_value=session)
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=_runtime_state(tmp_path, session_factory=session_factory),
    )
    request = _request_for_app(app)

    async def _consume() -> None:
        dependency = get_db_session(request)
        session_iter = dependency.__aiter__()
        yielded = await session_iter.__anext__()
        assert yielded is session
        with pytest.raises(StopAsyncIteration):
            await session_iter.__anext__()

    asyncio.run(_consume())

    session_factory.assert_called_once_with()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once_with()


def test_get_db_session_rolls_back_reraises_and_closes_on_error(
    tmp_path: Path,
) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session_factory = MagicMock(return_value=session)
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=_runtime_state(tmp_path, session_factory=session_factory),
    )
    request = _request_for_app(app)

    async def _consume() -> None:
        dependency = get_db_session(request)
        session_iter = dependency.__aiter__()
        yielded = await session_iter.__anext__()
        assert yielded is session
        await session_iter.athrow(RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_consume())

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
    session.close.assert_awaited_once_with()


def test_get_db_session_fails_closed_without_session_factory(tmp_path: Path) -> None:
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=_runtime_state(tmp_path, session_factory=None),
    )
    request = _request_for_app(app)

    async def _consume() -> None:
        dependency = get_db_session(request)
        await dependency.__anext__()

    with pytest.raises(DatabaseSessionUnavailableError, match="session factory"):
        asyncio.run(_consume())


def test_get_blob_store_uses_runtime_storage_root(tmp_path: Path) -> None:
    runtime_state = _runtime_state(tmp_path)
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=runtime_state,
    )
    request = _request_for_app(app)

    blob_store = get_blob_store(request)

    assert isinstance(blob_store, BlobStore)
    assert blob_store.storage_root == runtime_state.storage_root.resolve()


def test_get_audit_hmac_key_returns_configured_key(tmp_path: Path) -> None:
    configured_key = b"c" * 32
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=_runtime_state(tmp_path, audit_hmac_key=configured_key),
    )
    request = _request_for_app(app)

    assert get_audit_hmac_key(request) == configured_key


def test_get_audit_hmac_key_fails_closed_when_absent(tmp_path: Path) -> None:
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=_runtime_state(tmp_path, audit_hmac_key=None),
    )
    request = _request_for_app(app)

    with pytest.raises(AuditDependencyUnavailableError, match="not configured"):
        get_audit_hmac_key(request)


def test_get_audit_hmac_key_fails_closed_when_key_is_too_short(tmp_path: Path) -> None:
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=_runtime_state(tmp_path, audit_hmac_key=b"short"),
    )
    request = _request_for_app(app)

    with pytest.raises(AuditDependencyUnavailableError, match="not configured"):
        get_audit_hmac_key(request)


def test_get_audit_hmac_key_does_not_expose_secret_details(tmp_path: Path) -> None:
    app = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=_runtime_state(tmp_path, audit_hmac_key=b"short"),
    )
    request = _request_for_app(app)

    with pytest.raises(AuditDependencyUnavailableError) as exc_info:
        get_audit_hmac_key(request)

    message = str(exc_info.value)
    assert "at least" not in message
    assert "32" not in message


def test_app_runtime_state_repr_hides_audit_key(tmp_path: Path) -> None:
    runtime_state = _runtime_state(tmp_path, audit_hmac_key=b"secret")

    assert "secret" not in repr(runtime_state)


def test_audit_dependency_errors_map_to_safe_reconciliation_required_problem() -> None:
    app = FastAPI()
    register_problem_handlers(app)

    @app.get("/api/v1/audit-validation")
    async def audit_validation() -> None:
        raise AuditValidationError("secret audit validation detail")

    @app.get("/api/v1/audit-unavailable")
    async def audit_unavailable() -> None:
        raise AuditUnavailableError("secret audit HMAC key detail")

    client = TestClient(app, raise_server_exceptions=False)

    for path in ("/api/v1/audit-validation", "/api/v1/audit-unavailable"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        payload = response.json()
        assert payload["error_code"] == "reconciliation_required"
        assert payload["retryable"] is True
        assert "Retry-After" in response.headers
        assert "secret" not in payload["detail"].lower()
