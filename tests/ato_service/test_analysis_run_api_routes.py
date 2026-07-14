"""HTTP route tests for analysis run API slice."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ato_service.analysis_runs import (
    AnalysisRunMutationResult,
    AnalysisRunsPage,
    MatrixRowsPage,
)
from ato_service.api_dependencies import (
    get_audit_hmac_key,
    get_db_session,
    get_runtime_state,
)
from ato_service.api_router import get_mutation_principal, get_read_principal
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.lifecycle_transitions import IllegalStateTransitionError
from ato_service.main import AppRuntimeSnapshot, AppRuntimeState, create_app
from ato_service.problems import PROBLEM_MEDIA_TYPE
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
CSRF_TOKEN = "c" * 32
ORIGIN = "https://portal.example"

RUN_PAYLOAD: dict[str, Any] = {
    "schema_version": "2.0.0",
    "object_type": "analysis_run",
    "run_id": str(RUN_ID).lower(),
    "package_revision_id": str(REVISION_ID).lower(),
    "parent_run_id": None,
    "run_type": "deterministic_only",
    "status": "queued",
    "requested_by": "actor-1",
    "requested_at": "2026-07-11T18:00:00Z",
    "started_at": None,
    "completed_at": None,
    "authority_manifest_id": "ato-authorities-2026-07-10-draft",
    "analysis_profile_sha256": "a" * 64,
    "config_fingerprint": "b" * 64,
    "prompt_bundle_sha256": "c" * 64,
    "model_profile": "deterministic",
    "artifact_manifest_sha256": None,
    "llm_call_count": 0,
    "error_code": None,
    "error_retryable": None,
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
            authority_manifest_id="ato-authorities-2026-07-10-draft",
            project_root=ROOT,
        ),
        session_factory=MagicMock(),
        audit_hmac_key=b"audit-test-key",
    )


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    runtime_state = _runtime_state(tmp_path)
    principal = _principal()

    async def _db_session_override() -> AsyncIterator[MagicMock]:
        yield MagicMock()

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


def test_post_start_run_returns_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start(*_args: Any, **_kwargs: Any) -> AnalysisRunMutationResult:
        return AnalysisRunMutationResult(payload=RUN_PAYLOAD, status=202, replayed=False)

    monkeypatch.setattr("ato_service.api_router.start_run", fake_start)
    response = client.post(
        f"/api/v1/package-revisions/{REVISION_ID}/runs",
        headers={
            "Idempotency-Key": "idempotency-key-00000001",
            "X-CSRF-Token": CSRF_TOKEN,
            "Origin": ORIGIN,
        },
        json={
            "run_type": "deterministic_only",
            "parent_run_id": None,
            "assessment_item_ids": [],
        },
    )
    assert response.status_code == 202
    assert response.json()["run_type"] == "deterministic_only"


def test_get_run_returns_payload(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return RUN_PAYLOAD

    monkeypatch.setattr("ato_service.api_router.get_run", fake_get_run)
    response = client.get(f"/api/v1/runs/{RUN_ID}")
    assert response.status_code == 200
    assert response.json()["run_id"] == str(RUN_ID).lower()


def test_post_cancel_returns_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = dict(RUN_PAYLOAD)
    cancelled["status"] = "cancelled"

    async def fake_cancel(*_args: Any, **_kwargs: Any) -> AnalysisRunMutationResult:
        return AnalysisRunMutationResult(payload=cancelled, status=202, replayed=False)

    monkeypatch.setattr("ato_service.api_router.cancel_run", fake_cancel)
    response = client.post(
        f"/api/v1/runs/{RUN_ID}/cancel",
        headers={
            "X-CSRF-Token": CSRF_TOKEN,
            "Origin": ORIGIN,
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"


def test_get_matrix_returns_envelope(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_matrix(*_args: Any, **_kwargs: Any) -> MatrixRowsPage:
        return MatrixRowsPage(
            items=[
                {
                    "schema_version": "2.0.0",
                    "object_type": "matrix_row",
                    "matrix_row_id": "44444444-4444-4444-8444-444444444444",
                    "assessment_item_type": "nist_control",
                    "assessment_item_id": "AC-1",
                    "model_proposed_status": "insufficient_evidence",
                    "system_status": "insufficient_evidence",
                    "finding_summary": "No evidence",
                    "gaps": [],
                    "assessor_questions": [],
                    "citations": [],
                    "context_complete": False,
                    "producing_run_id": str(RUN_ID).lower(),
                    "source_run_id": str(RUN_ID).lower(),
                }
            ],
            next_cursor=None,
            total=1,
        )

    monkeypatch.setattr("ato_service.api_router.get_run_matrix", fake_get_matrix)
    response = client.get(f"/api/v1/runs/{RUN_ID}/matrix")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["assessment_item_id"] == "AC-1"


def test_list_runs_returns_envelope(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list(*_args: Any, **_kwargs: Any) -> AnalysisRunsPage:
        return AnalysisRunsPage(items=[RUN_PAYLOAD], next_cursor=None)

    monkeypatch.setattr("ato_service.api_router.list_runs", fake_list)
    response = client.get(f"/api/v1/package-revisions/{REVISION_ID}/runs")
    assert response.status_code == 200
    assert response.json()["items"][0]["run_id"] == str(RUN_ID).lower()


def test_cancel_conflict_maps_to_problem(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_cancel(*_args: Any, **_kwargs: Any) -> AnalysisRunMutationResult:
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state="succeeded",
            target_state="cancelled",
        )

    monkeypatch.setattr("ato_service.api_router.cancel_run", fake_cancel)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/api/v1/runs/{RUN_ID}/cancel",
        headers={
            "X-CSRF-Token": CSRF_TOKEN,
            "Origin": ORIGIN,
        },
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
