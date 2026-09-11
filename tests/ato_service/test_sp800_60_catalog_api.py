"""API tests for the SP 800-60 information type catalog."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.problems import register_problem_handlers
from ato_service.ssp_workspace.api import build_ssp_workspace_router, get_read_principal


def test_get_sp800_60_catalog_returns_bundled_entries() -> None:
    principal = AuthenticatedPrincipal(
        actor_id="isso@example.gov",
        groups=("system-owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example.gov",),
    )
    app = FastAPI()
    register_problem_handlers(app)
    app.include_router(build_ssp_workspace_router(), prefix="/api/v1")
    app.dependency_overrides[get_read_principal] = lambda: principal

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/ssp-workspaces/sp800-60-catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_id"] == "nist-sp-800-60-rev2"
    assert any(
        item["identifier"] == "D.14.2"
        for item in payload["information_types"]
    )
