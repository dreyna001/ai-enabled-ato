"""HTTP route tests for the P1.1 Systems and PackageRevision API slice."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from ato_service.api_dependencies import (
    get_audit_hmac_key,
    get_blob_store,
    get_db_session,
    get_runtime_state,
)
from ato_service.api_router import get_mutation_principal, get_read_principal
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.blobs import BlobStore
from ato_service.concurrency import IfMatchRequiredError
from ato_service.main import (
    AppRuntimeSnapshot,
    AppRuntimeState,
    create_app,
)
from ato_service.package_revisions import (
    PackageRevisionListResult,
    PackageRevisionMutationResult,
)
from ato_service.problems import PROBLEM_MEDIA_TYPE
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.source_artifacts import UploadSourceArtifactResult
from ato_service.systems import CreateSystemResult, SystemsPage

ROOT = Path(__file__).resolve().parents[2]

SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
PACKAGE_REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
IDEMPOTENCY_KEY = "idempotency-key-01"
CSRF_TOKEN = "c" * 32
ORIGIN = "https://portal.example"

SYSTEM_PAYLOAD: dict[str, Any] = {
    "schema_version": "2.0.0",
    "object_type": "system",
    "system_id": str(SYSTEM_ID).lower(),
    "display_name": "Fixture System",
    "external_system_id": None,
    "customer_enterprise_id": "dev-local-enterprise",
    "owner_group": "owners",
    "viewer_groups": ["viewers"],
    "created_at": "2026-07-10T20:00:00Z",
    "archived_at": None,
}

PACKAGE_REVISION_PAYLOAD: dict[str, Any] = {
    "schema_version": "2.0.0",
    "object_type": "package_revision",
    "package_revision_id": str(PACKAGE_REVISION_ID).lower(),
    "system_id": str(SYSTEM_ID).lower(),
    "parent_revision_id": None,
    "profile_id": "fisma_agency_security",
    "certification_class": None,
    "impact_level": "moderate",
    "data_origin": "synthetic",
    "sensitivity": "internal_unclassified",
    "effective_data_labels": ["internal_unclassified", "synthetic"],
    "authority_manifest_id": "fixture.draft",
    "content_manifest_sha256": None,
    "revision_version": 1,
    "status": "uploading",
    "created_by": "actor-1",
    "created_at": "2026-07-10T20:00:00Z",
}

SOURCE_ARTIFACT_PAYLOAD: dict[str, Any] = {
    "schema_version": "2.0.0",
    "object_type": "source_artifact",
    "artifact_id": "33333333-3333-4333-8333-333333333333",
    "package_revision_id": str(PACKAGE_REVISION_ID).lower(),
    "display_filename": "evidence.json",
    "storage_key": "ab/" + ("a" * 62),
    "sha256": "a" * 64,
    "size_bytes": 12,
    "declared_media_type": "application/json",
    "detected_media_type": "application/json",
    "artifact_kind": "evidence_document",
    "malware_scan_status": "pending",
    "extraction_status": "pending",
    "source_date": None,
    "uploaded_at": "2026-07-10T20:00:00Z",
}


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="actor-1",
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


def _assert_problem(
    response: Any,
    *,
    status: int,
    error_code: str,
) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    payload = response.json()
    assert payload["error_code"] == error_code
    assert payload["status"] == status


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def app(tmp_path: Path, mock_session: MagicMock) -> FastAPI:
    runtime_state = _runtime_state(tmp_path)
    principal = _principal()

    async def _db_session_override() -> AsyncIterator[MagicMock]:
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
    application.dependency_overrides[get_blob_store] = lambda: BlobStore(
        runtime_state.storage_root
    )
    return application


@pytest.fixture
def unauthenticated_app(tmp_path: Path, mock_session: MagicMock) -> FastAPI:
    runtime_state = _runtime_state(tmp_path)

    async def _db_session_override() -> AsyncIterator[MagicMock]:
        yield mock_session

    application = create_app(
        readiness_probe=AsyncMock(return_value={}),
        runtime_state=runtime_state,
    )
    application.dependency_overrides[get_db_session] = _db_session_override
    application.dependency_overrides[get_runtime_state] = lambda: runtime_state
    application.dependency_overrides[get_audit_hmac_key] = lambda: b"audit-test-key"
    application.dependency_overrides[get_blob_store] = lambda: BlobStore(
        runtime_state.storage_root
    )
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


def _mock_system_row() -> MagicMock:
    system = MagicMock()
    system.system_id = SYSTEM_ID
    system.display_name = SYSTEM_PAYLOAD["display_name"]
    system.external_system_id = None
    system.customer_enterprise_id = SYSTEM_PAYLOAD["customer_enterprise_id"]
    system.owner_group = "owners"
    system.viewer_groups = ["viewers"]
    system.created_at = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    system.archived_at = None
    return system


def test_analysis_run_routes_are_mounted(
    client: TestClient,
    mock_session: MagicMock,
) -> None:
    from ato_service.analysis_runs import AnalysisRunsPage

    async def _list_runs_override(*args: object, **kwargs: object) -> AnalysisRunsPage:
        return AnalysisRunsPage(items=[], next_cursor=None)

    with patch(
        "ato_service.api_router.list_runs",
        new=AsyncMock(side_effect=_list_runs_override),
    ):
        response = client.get(f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/runs")
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/api/v1/systems", {"json": {"display_name": "x", "external_system_id": None, "owner_group": "owners", "viewer_groups": []}}),
        ("get", "/api/v1/systems", {}),
        (f"get", f"/api/v1/systems/{SYSTEM_ID}", {}),
        ("post", f"/api/v1/systems/{SYSTEM_ID}/package-revisions", {"json": {"parent_revision_id": None, "profile_id": "fisma_agency_security", "certification_class": None, "impact_level": "moderate", "data_origin": "synthetic", "sensitivity": "internal_unclassified"}}),
        ("get", f"/api/v1/systems/{SYSTEM_ID}/package-revisions", {}),
        ("get", f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}", {}),
        ("post", f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/files", {"data": {"artifact_kind": "evidence_document"}, "files": {"file": ("evidence.json", b"{}", "application/json")}}),
        ("post", f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/finalize", {}),
        ("post", f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/confirm", {}),
    ],
)
def test_routes_require_authentication_when_principal_dependency_not_overridden(
    unauthenticated_app: FastAPI,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    mutation_headers: dict[str, str],
) -> None:
    if method == "post":
        headers = dict(mutation_headers)
        headers.update(kwargs.pop("headers", {}))
        kwargs["headers"] = headers
    with TestClient(unauthenticated_app) as unauth_client:
        response = unauth_client.request(method, path, **kwargs)
    _assert_problem(response, status=401, error_code="authentication_required")


def test_create_system_returns_payload(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    with patch(
        "ato_service.api_router.create_system",
        new_callable=AsyncMock,
        return_value=CreateSystemResult(payload=SYSTEM_PAYLOAD, status=201, replayed=False),
    ) as create_system:
        response = client.post(
            "/api/v1/systems",
            headers=mutation_headers,
            json={
                "display_name": "Fixture System",
                "external_system_id": None,
                "owner_group": "owners",
                "viewer_groups": ["viewers"],
            },
        )

    assert response.status_code == 201
    assert response.json() == SYSTEM_PAYLOAD
    assert "ETag" not in response.headers
    create_system.assert_awaited_once()


def test_create_system_rejects_extra_request_fields(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/systems",
        headers=mutation_headers,
        json={
            "display_name": "Fixture System",
            "external_system_id": None,
            "owner_group": "owners",
            "viewer_groups": [],
            "unexpected": True,
        },
    )
    assert response.status_code == 422
    _assert_problem(response, status=422, error_code="request_schema_invalid")


def test_create_system_rejects_duplicate_viewer_groups(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/systems",
        headers=mutation_headers,
        json={
            "display_name": "Fixture System",
            "external_system_id": None,
            "owner_group": "owners",
            "viewer_groups": ["viewers", "viewers"],
        },
    )
    _assert_problem(response, status=422, error_code="request_schema_invalid")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "display_name": "Fixture System",
            "owner_group": "owners",
            "viewer_groups": [],
        },
        {
            "display_name": "Fixture System",
            "external_system_id": None,
            "owner_group": "owners",
        },
    ],
)
def test_create_system_requires_nullable_fields_in_request_body(
    client: TestClient,
    mutation_headers: dict[str, str],
    payload: dict[str, Any],
) -> None:
    response = client.post(
        "/api/v1/systems",
        headers=mutation_headers,
        json=payload,
    )
    _assert_problem(response, status=422, error_code="request_schema_invalid")


@pytest.mark.parametrize(
    "omitted_field",
    ["parent_revision_id", "certification_class", "impact_level"],
)
def test_create_package_revision_requires_nullable_fields_in_request_body(
    client: TestClient,
    mutation_headers: dict[str, str],
    omitted_field: str,
) -> None:
    payload = {
        "parent_revision_id": None,
        "profile_id": "fisma_agency_security",
        "certification_class": None,
        "impact_level": "moderate",
        "data_origin": "synthetic",
        "sensitivity": "internal_unclassified",
    }
    del payload[omitted_field]
    response = client.post(
        f"/api/v1/systems/{SYSTEM_ID}/package-revisions",
        headers=mutation_headers,
        json=payload,
    )
    _assert_problem(response, status=422, error_code="request_schema_invalid")


def test_create_system_requires_csrf_and_origin(
    unauthenticated_app: FastAPI,
    mutation_headers: dict[str, str],
) -> None:
    @unauthenticated_app.middleware("http")
    async def inject_principal(request, call_next):  # type: ignore[no-untyped-def]
        request.state.authenticated_principal = _principal()
        return await call_next(request)

    with TestClient(unauthenticated_app) as csrf_client:
        response = csrf_client.post(
            "/api/v1/systems",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={
                "display_name": "Fixture System",
                "external_system_id": None,
                "owner_group": "owners",
                "viewer_groups": [],
            },
        )
        _assert_problem(response, status=403, error_code="csrf_validation_failed")

        response = csrf_client.post(
            "/api/v1/systems",
            headers={
                "Idempotency-Key": IDEMPOTENCY_KEY,
                "X-CSRF-Token": "wrong" + ("x" * 27),
                "Origin": ORIGIN,
            },
            json={
                "display_name": "Fixture System",
                "external_system_id": None,
                "owner_group": "owners",
                "viewer_groups": [],
            },
        )
        _assert_problem(response, status=403, error_code="csrf_validation_failed")


def test_list_systems_returns_items_and_next_cursor(client: TestClient) -> None:
    with patch(
        "ato_service.api_router.list_systems",
        new_callable=AsyncMock,
        return_value=SystemsPage(items=[SYSTEM_PAYLOAD], next_cursor="cursor-1"),
    ):
        response = client.get("/api/v1/systems", params={"limit": 10})

    assert response.status_code == 200
    assert response.json() == {"items": [SYSTEM_PAYLOAD], "next_cursor": "cursor-1"}


def test_get_system_returns_payload(client: TestClient) -> None:
    with patch(
        "ato_service.api_router.get_system",
        new_callable=AsyncMock,
        return_value=_mock_system_row(),
    ):
        response = client.get(f"/api/v1/systems/{SYSTEM_ID}")

    assert response.status_code == 200
    assert response.json() == SYSTEM_PAYLOAD


def test_create_package_revision_returns_etag(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    with patch(
        "ato_service.api_router.create_package_revision",
        new_callable=AsyncMock,
        return_value=PackageRevisionMutationResult(
            payload=PACKAGE_REVISION_PAYLOAD,
            status=201,
            etag='"v1"',
            replayed=False,
        ),
    ):
        response = client.post(
            f"/api/v1/systems/{SYSTEM_ID}/package-revisions",
            headers=mutation_headers,
            json={
                "parent_revision_id": None,
                "profile_id": "fisma_agency_security",
                "certification_class": None,
                "impact_level": "moderate",
                "data_origin": "synthetic",
                "sensitivity": "internal_unclassified",
            },
        )

    assert response.status_code == 201
    assert response.json() == PACKAGE_REVISION_PAYLOAD
    assert response.headers["ETag"] == '"v1"'


def test_list_package_revisions_returns_items_and_next_cursor(client: TestClient) -> None:
    with patch(
        "ato_service.api_router.list_package_revisions",
        new_callable=AsyncMock,
        return_value=PackageRevisionListResult(
            items=(PACKAGE_REVISION_PAYLOAD,),
            next_cursor=None,
        ),
    ):
        response = client.get(f"/api/v1/systems/{SYSTEM_ID}/package-revisions")

    assert response.status_code == 200
    assert response.json() == {
        "items": [PACKAGE_REVISION_PAYLOAD],
        "next_cursor": None,
    }


def test_get_package_revision_returns_etag(client: TestClient) -> None:
    with patch(
        "ato_service.api_router.get_package_revision",
        new_callable=AsyncMock,
        return_value=PACKAGE_REVISION_PAYLOAD,
    ):
        response = client.get(f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}")

    assert response.status_code == 200
    assert response.json() == PACKAGE_REVISION_PAYLOAD
    assert response.headers["ETag"] == '"v1"'


def test_upload_package_file_returns_etag_and_closes_upload(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    with (
        patch(
            "ato_service.api_router.upload_source_artifact",
            new_callable=AsyncMock,
            return_value=UploadSourceArtifactResult(
                status=201,
                payload=SOURCE_ARTIFACT_PAYLOAD,
                etag='"v2"',
                replayed=False,
            ),
        ) as upload_source_artifact,
        patch.object(StarletteUploadFile, "close", autospec=True) as close_upload,
    ):
        response = client.post(
            f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/files",
            headers=mutation_headers,
            data={"artifact_kind": "evidence_document"},
            files={"file": ("evidence.json", b'{"ok": true}', "application/json")},
        )

    assert response.status_code == 201
    assert response.json() == SOURCE_ARTIFACT_PAYLOAD
    assert response.headers["ETag"] == '"v2"'
    close_upload.assert_awaited()
    upload_source_artifact.assert_awaited_once()


def test_finalize_package_revision_returns_accepted_with_etag(
    client: TestClient,
    mutation_headers: dict[str, str],
    app: FastAPI,
) -> None:
    finalized_payload = {
        **PACKAGE_REVISION_PAYLOAD,
        "status": "scanning",
        "revision_version": 2,
        "content_manifest_sha256": "b" * 64,
    }
    with patch(
        "ato_service.api_router.finalize_package_revision",
        new_callable=AsyncMock,
        return_value=PackageRevisionMutationResult(
            payload=finalized_payload,
            status=202,
            etag='"v2"',
            replayed=False,
        ),
    ) as finalize_package_revision:
        response = client.post(
            f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/finalize",
            headers=mutation_headers,
        )

    assert response.status_code == 202
    assert response.json() == finalized_payload
    assert response.headers["ETag"] == '"v2"'
    runtime_state = app.dependency_overrides[get_runtime_state]()
    finalize_package_revision.assert_awaited_once()
    call_kwargs = finalize_package_revision.await_args.kwargs
    assert call_kwargs["project_root"] == ROOT
    assert call_kwargs["storage_root"] == runtime_state.storage_root


def test_confirm_package_revision_returns_ok_with_etag(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    confirmed_payload = {
        **PACKAGE_REVISION_PAYLOAD,
        "status": "ready",
        "revision_version": 3,
    }
    headers = {**mutation_headers, "If-Match": '"v2"'}
    with patch(
        "ato_service.api_router.confirm_package_revision",
        new_callable=AsyncMock,
        return_value=PackageRevisionMutationResult(
            payload=confirmed_payload,
            status=200,
            etag='"v3"',
            replayed=False,
        ),
    ) as confirm_package_revision:
        response = client.post(
            f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/confirm",
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json() == confirmed_payload
    assert response.headers["ETag"] == '"v3"'
    assert confirm_package_revision.await_args.kwargs["if_match"] == '"v2"'


def test_confirm_without_if_match_maps_to_service_level_required_error(
    client: TestClient,
    mutation_headers: dict[str, str],
) -> None:
    with patch(
        "ato_service.api_router.confirm_package_revision",
        new_callable=AsyncMock,
        side_effect=IfMatchRequiredError(),
    ):
        response = client.post(
            f"/api/v1/package-revisions/{PACKAGE_REVISION_ID}/confirm",
            headers=mutation_headers,
        )
    _assert_problem(response, status=428, error_code="if_match_required")
