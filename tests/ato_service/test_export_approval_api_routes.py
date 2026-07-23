"""Route-level runtime wiring tests for export approval decisions."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from ato_service.api_dependencies import (
    get_audit_hmac_key,
    get_db_session,
    get_runtime_state,
)
from ato_service.api_router import get_mutation_principal
from ato_service.app_runtime import AppRuntimeSnapshot, AppRuntimeState
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.export_service import ExportMutationResult, SelfApprovalDeniedError
from ato_service.main import create_app
from ato_service.runtime_config import load_runtime_config_from_dict

APPROVAL_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
IDEMPOTENCY_KEY = "route-idempotency-key"


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="operator@example.test",
        groups=("owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


def _app(tmp_path: Path, *, single_user_mode_enabled: bool | None) -> FastAPI:
    document: dict[str, object] = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": "/data/ato-storage",
    }
    if single_user_mode_enabled is not None:
        document["SINGLE_USER_MODE_ENABLED"] = single_user_mode_enabled
    config = load_runtime_config_from_dict(document, base_dir=tmp_path)
    runtime_state = AppRuntimeState(
        snapshot=AppRuntimeSnapshot(
            config=config,
            storage_root=config.storage_data_path,
            authority_manifest_id="fixture.draft",
            project_root=Path(__file__).resolve().parents[2],
        ),
        session_factory=MagicMock(),
        audit_hmac_key=b"audit-test-key",
    )

    async def _db_session_override() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    application = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=runtime_state,
    )
    application.dependency_overrides[get_mutation_principal] = _principal
    application.dependency_overrides[get_db_session] = _db_session_override
    application.dependency_overrides[get_runtime_state] = lambda: runtime_state
    application.dependency_overrides[get_audit_hmac_key] = lambda: b"audit-test-key"
    return application


@pytest.mark.parametrize("single_user_mode_enabled", [True, False])
def test_auth_session_reports_single_user_mode(
    tmp_path: Path,
    single_user_mode_enabled: bool,
) -> None:
    application = _app(
        tmp_path,
        single_user_mode_enabled=single_user_mode_enabled,
    )

    @application.middleware("http")
    async def _inject_principal(request: Request, call_next):
        request.state.authenticated_principal = _principal()
        return await call_next(request)

    response = TestClient(application).get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json()["single_user_mode_enabled"] is single_user_mode_enabled


@pytest.mark.parametrize(
    ("path_suffix", "body", "service_name"),
    [
        ("approve", {"reason": None}, "approve_export"),
        ("reject", {"reason": "operator decision"}, "reject_export"),
    ],
)
def test_approval_routes_pass_enabled_single_user_flag(
    tmp_path: Path,
    path_suffix: str,
    body: dict[str, str | None],
    service_name: str,
) -> None:
    service = AsyncMock(
        return_value=ExportMutationResult(
            payload={
                "approval_id": str(APPROVAL_ID),
                "decision": "approved" if path_suffix == "approve" else "rejected",
            },
            status=200,
            etag='"v1"',
            replayed=False,
        )
    )
    with patch(f"ato_service.extended_api_router.{service_name}", service):
        response = TestClient(
            _app(tmp_path, single_user_mode_enabled=True)
        ).post(
            f"/api/v1/approvals/{APPROVAL_ID}/{path_suffix}",
            json=body,
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        )

    assert response.status_code == 200
    assert service.await_args.kwargs["single_user_mode_enabled"] is True


def test_approval_route_defaults_absent_single_user_flag_to_deny(
    tmp_path: Path,
) -> None:
    service = AsyncMock(side_effect=SelfApprovalDeniedError())
    with patch("ato_service.extended_api_router.approve_export", service):
        response = TestClient(
            _app(tmp_path, single_user_mode_enabled=None)
        ).post(
            f"/api/v1/approvals/{APPROVAL_ID}/approve",
            json={"reason": None},
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": "self_approval_denied",
        "error_code": "self_approval_denied",
    }
    assert service.await_args.kwargs["single_user_mode_enabled"] is False


def test_approval_route_preserves_generic_object_denial_mapping(tmp_path: Path) -> None:
    service = AsyncMock(side_effect=AuthorizationDeniedError())
    with patch("ato_service.extended_api_router.approve_export", service):
        response = TestClient(
            _app(tmp_path, single_user_mode_enabled=True)
        ).post(
            f"/api/v1/approvals/{APPROVAL_ID}/approve",
            json={"reason": None},
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "authorization_denied"
    assert "owner_group" not in response.text
    assert "viewer_groups" not in response.text
