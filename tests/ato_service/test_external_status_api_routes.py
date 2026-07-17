"""HTTP route tests for external preparation and authorization-decision views."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ato_service.api_dependencies import (
    get_audit_hmac_key,
    get_db_session,
    get_runtime_state,
)
from ato_service.api_router import get_mutation_principal, get_read_principal
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.authorization_decisions import (
    AuthorizationDecisionMutationResult,
    AuthorizationDecisionNotFoundError,
)
from ato_service.main import AppRuntimeSnapshot, AppRuntimeState, create_app
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
PACKAGE_REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
AUTHORIZATION_DECISION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CSRF_TOKEN = "c" * 32
ORIGIN = "https://portal.example"
IDEMPOTENCY_KEY = "authorization-decision-key-01"

PACKAGE_REVISION_PAYLOAD: dict[str, Any] = {
    "schema_version": "2.0.0",
    "object_type": "package_revision",
    "package_revision_id": str(PACKAGE_REVISION_ID),
    "system_id": str(SYSTEM_ID),
    "parent_revision_id": None,
    "profile_id": "fisma_agency_security",
    "certification_class": None,
    "impact_level": "moderate",
    "data_origin": "synthetic",
    "sensitivity": "internal_unclassified",
    "effective_data_labels": ["internal_unclassified", "synthetic"],
    "authority_manifest_id": "fixture.draft",
    "content_manifest_sha256": "a" * 64,
    "package_content_sha256": "b" * 64,
    "system_context_snapshot_id": "33333333-3333-4333-8333-333333333333",
    "revision_version": 4,
    "status": "ready",
    "package_preparation_status": "ready_for_external_review",
    "created_by": "owner@example.test",
    "created_at": "2026-07-17T03:00:00Z",
}

AUTHORIZATION_DECISION_PAYLOAD: dict[str, Any] = {
    "schema_version": "2.0.0",
    "object_type": "authorization_decision_record",
    "authorization_decision_id": str(AUTHORIZATION_DECISION_ID),
    "system_id": str(SYSTEM_ID),
    "package_revision_id": str(PACKAGE_REVISION_ID),
    "decision_type": "authorization_to_operate",
    "decision_date": "2026-07-16",
    "issuing_authority": "Customer Authorizing Official",
    "artifact_id": None,
    "notes": "Externally issued decision.",
    "attached_by": "owner@example.test",
    "attached_at": "2026-07-17T03:05:00Z",
}

ATTACH_REQUEST = {
    "decision_type": "authorization_to_operate",
    "decision_date": "2026-07-16",
    "issuing_authority": "Customer Authorizing Official",
    "artifact_id": None,
    "notes": "Externally issued decision.",
}


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="owner@example.test",
        groups=("owners",),
        csrf_token=CSRF_TOKEN,
        allowed_origins=(ORIGIN,),
    )


def _runtime_state(tmp_path: Path) -> AppRuntimeState:
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
        session_factory=MagicMock(),
        audit_hmac_key=b"audit-test-key",
    )


@pytest.fixture
def mock_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def app(tmp_path: Path, mock_session: AsyncMock) -> FastAPI:
    runtime_state = _runtime_state(tmp_path)
    principal = _principal()

    async def _db_session_override() -> AsyncIterator[AsyncMock]:
        yield mock_session

    application = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=runtime_state,
    )
    application.dependency_overrides[get_read_principal] = lambda: principal
    application.dependency_overrides[get_mutation_principal] = lambda: principal
    application.dependency_overrides[get_db_session] = _db_session_override
    application.dependency_overrides[get_runtime_state] = lambda: runtime_state
    application.dependency_overrides[get_audit_hmac_key] = lambda: b"audit-test-key"
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def mutation_headers() -> dict[str, str]:
    return {
        "Idempotency-Key": IDEMPOTENCY_KEY,
        "X-CSRF-Token": CSRF_TOKEN,
        "Origin": ORIGIN,
    }


def _mutation_result(*, replayed: bool = False) -> AuthorizationDecisionMutationResult:
    return AuthorizationDecisionMutationResult(
        payload=AUTHORIZATION_DECISION_PAYLOAD,
        status=201,
        etag='"v1"',
        replayed=replayed,
    )


def test_get_package_revision_includes_computed_preparation_status(
    client: TestClient,
    mock_session: AsyncMock,
) -> None:
    revision = MagicMock(
        package_revision_id=PACKAGE_REVISION_ID,
        system_id=SYSTEM_ID,
        parent_revision_id=None,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="synthetic",
        sensitivity="internal_unclassified",
        effective_data_labels=["internal_unclassified", "synthetic"],
        authority_manifest_id="fixture.draft",
        content_manifest_sha256="a" * 64,
        package_content_sha256="b" * 64,
        system_context_snapshot_id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
        revision_version=4,
        status="ready",
        created_by="owner@example.test",
        created_at=datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc),
    )
    system = MagicMock(owner_group="owners", viewer_groups=["viewers"])
    exported_candidate = MagicMock(
        package_revision_id=PACKAGE_REVISION_ID,
        export_status="exported",
        review_revision_id=uuid.UUID("44444444-4444-4444-8444-444444444444"),
        run_id=uuid.UUID("55555555-5555-4555-8555-555555555555"),
        draft_hash="c" * 64,
        approval_hash="c" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    mock_session.execute.side_effect = (
        MagicMock(one_or_none=MagicMock(return_value=(revision, system))),
        MagicMock(all=MagicMock(return_value=[exported_candidate])),
    )

    response = client.get(f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}")

    assert response.status_code == 200
    assert response.json()["package_preparation_status"] == "ready_for_external_review"
    assert mock_session.execute.await_count == 2


def test_authorization_decision_post_and_get_success(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    with (
        patch(
            "ato_service.extended_api_router.attach_authorization_decision",
            new=AsyncMock(return_value=_mutation_result()),
        ) as attach,
        patch(
            "ato_service.extended_api_router.list_authorization_decisions",
            new=AsyncMock(return_value=(AUTHORIZATION_DECISION_PAYLOAD,)),
        ) as list_decisions,
    ):
        post_response = client.post(
            f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions",
            params={"package_revision_id": str(PACKAGE_REVISION_ID)},
            headers=mutation_headers,
            json=ATTACH_REQUEST,
        )
        get_response = client.get(
            f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions"
        )

    assert post_response.status_code == 201
    assert post_response.json() == AUTHORIZATION_DECISION_PAYLOAD
    assert get_response.status_code == 200
    assert get_response.json() == {"items": [AUTHORIZATION_DECISION_PAYLOAD]}
    assert attach.await_args.kwargs["idempotency_key"] == IDEMPOTENCY_KEY
    list_decisions.assert_awaited_once()


@pytest.mark.parametrize("method", ("post", "get"))
def test_authorization_decision_routes_return_403_for_denied_access(
    client: TestClient,
    mutation_headers: dict[str, str],
    method: str,
) -> None:
    target = (
        "ato_service.extended_api_router.attach_authorization_decision"
        if method == "post"
        else "ato_service.extended_api_router.list_authorization_decisions"
    )
    with patch(target, new=AsyncMock(side_effect=AuthorizationDeniedError())):
        if method == "post":
            response = client.post(
                f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions",
                headers=mutation_headers,
                json=ATTACH_REQUEST,
            )
        else:
            response = client.get(
                f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions"
            )

    assert response.status_code == 403
    assert response.json()["error"] == "authorization_denied"


@pytest.mark.parametrize("method", ("post", "get"))
def test_authorization_decision_routes_return_404_for_missing_system(
    client: TestClient,
    mutation_headers: dict[str, str],
    method: str,
) -> None:
    target = (
        "ato_service.extended_api_router.attach_authorization_decision"
        if method == "post"
        else "ato_service.extended_api_router.list_authorization_decisions"
    )
    with patch(target, new=AsyncMock(side_effect=AuthorizationDecisionNotFoundError())):
        if method == "post":
            response = client.post(
                f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions",
                headers=mutation_headers,
                json=ATTACH_REQUEST,
            )
        else:
            response = client.get(
                f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions"
            )

    assert response.status_code == 404
    assert response.json()["error"] == "resource_not_found"


def test_authorization_decision_post_returns_replayed_outcome(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    attach = AsyncMock(
        side_effect=(
            _mutation_result(replayed=False),
            _mutation_result(replayed=True),
        )
    )
    with patch(
        "ato_service.extended_api_router.attach_authorization_decision",
        new=attach,
    ):
        first = client.post(
            f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions",
            headers=mutation_headers,
            json=ATTACH_REQUEST,
        )
        replay = client.post(
            f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions",
            headers=mutation_headers,
            json=ATTACH_REQUEST,
        )

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json() == AUTHORIZATION_DECISION_PAYLOAD
    assert attach.await_count == 2
    assert all(
        call.kwargs["idempotency_key"] == IDEMPOTENCY_KEY
        for call in attach.await_args_list
    )


def test_attaching_external_decision_does_not_change_preparation_status(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    get_revision = AsyncMock(return_value=PACKAGE_REVISION_PAYLOAD)
    with (
        patch("ato_service.api_router.get_package_revision", new=get_revision),
        patch(
            "ato_service.extended_api_router.attach_authorization_decision",
            new=AsyncMock(return_value=_mutation_result()),
        ),
    ):
        before = client.get(f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}")
        attached = client.post(
            f"/api/v1/systems/{SYSTEM_ID}/authorization-decisions",
            params={"package_revision_id": str(PACKAGE_REVISION_ID)},
            headers=mutation_headers,
            json=ATTACH_REQUEST,
        )
        after = client.get(f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}")

    assert attached.status_code == 201
    assert (
        before.json()["package_preparation_status"]
        == after.json()["package_preparation_status"]
        == "ready_for_external_review"
    )
    assert get_revision.await_count == 2
