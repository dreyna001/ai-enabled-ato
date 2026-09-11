"""API tests for SSP workspace categorization."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ato_service.api_dependencies import get_runtime_state
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.problems import register_problem_handlers
from ato_service.ssp_workspace.api import (
    build_ssp_workspace_router,
    get_audit_hmac_key,
    get_db_session,
    get_mutation_principal,
)
from ato_service.ssp_workspace.categorization import CategorizationValidationError


def _test_app(principal: AuthenticatedPrincipal) -> FastAPI:
    app = FastAPI()
    register_problem_handlers(app)
    app.include_router(build_ssp_workspace_router(), prefix="/api/v1")
    app.dependency_overrides[get_mutation_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    app.dependency_overrides[get_audit_hmac_key] = lambda: b"0" * 32
    app.dependency_overrides[get_runtime_state] = lambda: MagicMock(config=MagicMock())
    return app


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="isso@example.gov",
        groups=("system-owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example.gov",),
    )


def test_post_categorization_invalid_evidence_returns_field_errors() -> None:
    workspace_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    revision_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    app = _test_app(_principal())

    with (
        patch(
            "ato_service.ssp_workspace.api._authorize_workspace",
            new=AsyncMock(),
        ),
        patch(
            "ato_service.ssp_workspace.api.save_system_categorization",
            new=AsyncMock(
                side_effect=CategorizationValidationError(
                    "confidentiality evidence is required",
                    field="confidentiality_evidence",
                )
            ),
        ),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/v1/ssp-workspaces/{workspace_id}/categorization",
                json={
                    "expected_revision_id": str(revision_id),
                    "confidentiality": "moderate",
                    "integrity": "moderate",
                    "availability": "moderate",
                    "confidentiality_rationale": "Rationale",
                    "integrity_rationale": "Rationale",
                    "availability_rationale": "Rationale",
                    "confidentiality_evidence": [],
                    "integrity_evidence": [],
                    "availability_evidence": [],
                },
                headers={
                    "Origin": "https://portal.example.gov",
                    "X-CSRF-Token": "c" * 32,
                },
            )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == "request_schema_invalid"
    assert payload["field_errors"][0]["path"] == "confidentiality_evidence"


def test_post_categorization_analyze_returns_updated_envelope() -> None:
    workspace_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    revision_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    app = _test_app(_principal())

    envelope = {
        "workspace_id": str(workspace_id),
        "system_id": str(uuid.uuid4()),
        "status": "draft",
        "system": {"display_name": "Example"},
        "profile": {
            "profile_version_id": str(uuid.uuid4()),
            "profile_id": "agency-fisma-nist-sp800-53-rev5",
            "version": "1.4.0",
            "status": "active",
            "impact_level": None,
            "provisional_impact_level": "moderate",
        },
        "current_revision": {
            "revision_id": str(revision_id),
            "version": 2,
            "status": "draft",
            "content_sha256": "abc",
            "created_at": "2026-09-06T12:00:00+00:00",
            "content": {
                "facts": [
                    {
                        "key": "system.confidentiality_impact",
                        "value": "moderate",
                        "provenance": "agent_generated",
                        "evidence": [],
                    }
                ],
                "sections": [],
                "controls": [],
                "questions": [],
            },
        },
        "evidence": [],
        "approvals": [],
        "agent_patches": [],
        "requirements": [],
        "satisfied_requirement_ids": [],
        "metrics": {},
        "control_response": {
            "implementation_statuses": ["implemented"],
            "responsibilities": ["system_specific"],
            "question_owner_types": ["isso"],
            "evidence_required_for_agent_statement": True,
        },
        "agency_docx_renders": [],
    }

    with (
        patch(
            "ato_service.ssp_workspace.api._authorize_workspace",
            new=AsyncMock(),
        ),
        patch(
            "ato_service.ssp_workspace.api._model_adapter",
            new=lambda _runtime_state: AsyncMock(return_value="{}"),
        ),
        patch(
            "ato_service.ssp_workspace.api.analyze_workspace_categorization",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "ato_service.ssp_workspace.api.load_workspace_envelope",
            new=AsyncMock(return_value=envelope),
        ),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/v1/ssp-workspaces/{workspace_id}/categorization/analyze",
                json={"expected_revision_id": str(revision_id)},
                headers={
                    "Origin": "https://portal.example.gov",
                    "X-CSRF-Token": "c" * 32,
                },
            )

    assert response.status_code == 200
    payload = response.json()
    fact = payload["current_revision"]["content"]["facts"][0]
    assert fact["provenance"] == "agent_generated"
