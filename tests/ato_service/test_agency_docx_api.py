"""Route contract tests for workspace agency DOCX renders."""

from __future__ import annotations

import inspect
from pathlib import Path

from ato_service.ssp_workspace.api import build_ssp_workspace_router

API_SOURCE = Path(__file__).resolve().parents[2] / "src" / "ato_service" / "ssp_workspace" / "api.py"


def test_agency_docx_routes_use_expected_auth_dependencies() -> None:
    router = build_ssp_workspace_router()
    routes = {route.path: route for route in router.routes}

    mutation_paths = {
        "/ssp-workspaces/{workspace_id}/agency-docx-renders",
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/approve",
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/reject",
    }
    read_paths = {
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/preview",
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/download",
    }

    for path in mutation_paths:
        route = routes[path]
        endpoint = route.endpoint
        assert endpoint is not None
        signature = inspect.signature(endpoint)
        assert "get_mutation_principal" in str(signature.parameters["principal"])

    for path in read_paths:
        route = routes[path]
        endpoint = route.endpoint
        assert endpoint is not None
        signature = inspect.signature(endpoint)
        assert "get_read_principal" in str(signature.parameters["principal"])


def test_agency_docx_upload_uses_bounded_file_read() -> None:
    source = API_SOURCE.read_text(encoding="utf-8")
    assert "post_agency_docx_render" in source
    assert "file.read(runtime_state.config.limits.max_single_file_bytes + 1)" in source
    assert "await file.read()" not in source.split("post_agency_docx_render")[1].split(
        "post_agency_docx_approve"
    )[0]
