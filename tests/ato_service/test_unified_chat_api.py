"""Private chatbot HTTP authorization, CSRF, and failure boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.problems import register_problem_handlers
from ato_service.ssp_workspace import api
from ato_service.ssp_workspace.chat_contracts import ChatHistory
from ato_service.ssp_workspace.model_policy import SspModelPolicyError
from ato_service.ssp_workspace.model_runtime import SspContextBudgetError
from ato_service.ssp_workspace.persistence import StaleWorkspaceRevisionError


WORKSPACE_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PATH = f"/api/v1/ssp-workspaces/{WORKSPACE_ID}/chat"
PRINCIPAL = AuthenticatedPrincipal(
    actor_id="private-user", groups=("owners",), csrf_token="c" * 32,
    allowed_origins=("https://portal.example.gov",),
)
HEADERS = {"Origin": "https://portal.example.gov", "X-CSRF-Token": "c" * 32}


def test_native_context_budget_error_is_safe_and_actionable() -> None:
    response = api._error_response(SspContextBudgetError("private prompt detail"))
    assert response.status_code == 422
    assert b"model_context_budget_exceeded" in response.body
    assert b"private prompt detail" not in response.body


def _body() -> dict[str, object]:
    return {
        "message": "What evidence supports the boundary?",
        "expected_revision_id": str(uuid.uuid4()),
        "request_id": str(uuid.uuid4()),
        "expected_sequence": 0,
    }


def _history() -> ChatHistory:
    return ChatHistory(
        workspace_id=WORKSPACE_ID, sequence=0, messages=[],
        has_more=False, next_before_sequence=None,
    )


def _app(*, authenticated: bool = True) -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    register_problem_handlers(app)
    if authenticated:
        @app.middleware("http")
        async def identity(request, call_next):
            request.state.authenticated_principal = PRINCIPAL
            return await call_next(request)
    session = AsyncMock()
    app.dependency_overrides[api.get_db_session] = lambda: session
    app.dependency_overrides[api.get_runtime_state] = lambda: SimpleNamespace(config=object())
    app.include_router(api.build_ssp_workspace_router(), prefix="/api/v1")
    return app, session


def test_history_uses_authenticated_owner_and_never_requires_model() -> None:
    app, _ = _app()
    with (
        patch.object(api, "_authorize_workspace", new=AsyncMock()) as authorize,
        patch.object(api, "load_chat_history", new=AsyncMock(return_value=_history())) as load,
        patch.object(api, "_model_adapter", side_effect=AssertionError("no model")),
        TestClient(app) as client,
    ):
        response = client.get(PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert load.call_args.kwargs["actor_id"] == "private-user"
    assert authorize.call_args.kwargs["roles"] == ("viewer",)


@pytest.mark.parametrize("method", ["get", "post"])
def test_chat_requires_authenticated_identity(method: str) -> None:
    app, _ = _app(authenticated=False)
    with TestClient(app) as client:
        response = client.get(PATH) if method == "get" else client.post(PATH, json=_body(), headers=HEADERS)
    assert response.status_code == 401


def test_successful_turn_returns_private_history_after_access_recheck() -> None:
    app, session = _app()
    with (
        patch.object(api, "_authorize_workspace", new=AsyncMock()) as authorize,
        patch.object(api, "_finish_read_only_boundary", new=AsyncMock()),
        patch.object(api, "_model_adapter", return_value=object()),
        patch.object(api, "send_chat_message", new=AsyncMock(return_value=_history())) as send,
        TestClient(app) as client,
    ):
        response = client.post(PATH, json=_body(), headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert authorize.await_count == 2
    assert send.call_args.kwargs["actor_id"] == PRINCIPAL.actor_id
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("method", ["get", "post"])
def test_no_history_or_model_access_for_unauthorized_system(method: str) -> None:
    app, session = _app()
    with (
        patch.object(api, "_authorize_workspace", new=AsyncMock(side_effect=AuthorizationDeniedError())),
        patch.object(api, "load_chat_history", new=AsyncMock()) as load,
        patch.object(api, "send_chat_message", new=AsyncMock()) as send,
        TestClient(app) as client,
    ):
        response = client.get(PATH) if method == "get" else client.post(PATH, json=_body(), headers=HEADERS)
    assert response.status_code == 403
    load.assert_not_awaited()
    send.assert_not_awaited()
    session.rollback.assert_awaited()


@pytest.mark.parametrize("method", ["get", "post"])
def test_global_owner_role_does_not_bypass_actual_system_membership(method: str) -> None:
    app, session = _app()
    rows = MagicMock()
    rows.one_or_none.return_value = (
        SimpleNamespace(workspace_id=WORKSPACE_ID),
        SimpleNamespace(owner_group="different-system-owners", viewer_groups=[]),
    )
    session.execute.return_value = rows
    with (
        patch.object(api, "load_chat_history", new=AsyncMock()) as load,
        patch.object(api, "send_chat_message", new=AsyncMock()) as send,
        TestClient(app) as client,
    ):
        response = client.get(PATH) if method == "get" else client.post(PATH, json=_body(), headers=HEADERS)
    assert response.status_code == 403
    load.assert_not_awaited()
    send.assert_not_awaited()


def test_shared_edit_proposals_also_require_selected_system_membership() -> None:
    app, session = _app()
    app.dependency_overrides[api.get_audit_hmac_key] = lambda: b"k" * 32
    rows = MagicMock()
    rows.one_or_none.return_value = (
        SimpleNamespace(workspace_id=WORKSPACE_ID),
        SimpleNamespace(owner_group="different-system-owners", viewer_groups=[]),
    )
    session.execute.return_value = rows
    with patch.object(api, "propose_agent_patch", new=AsyncMock()) as propose, TestClient(app) as client:
        response = client.post(
            f"/api/v1/ssp-workspaces/{WORKSPACE_ID}/agent/patches",
            json={"expected_revision_id": str(uuid.uuid4()), "instruction": "Explain the system boundary."},
            headers=HEADERS,
        )
    assert response.status_code == 403
    propose.assert_not_awaited()


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example", "X-CSRF-Token": "c" * 32}])
def test_chat_post_requires_csrf_and_approved_origin(headers: dict[str, str]) -> None:
    app, _ = _app()
    with patch.object(api, "send_chat_message", new=AsyncMock()) as send, TestClient(app) as client:
        response = client.post(PATH, json=_body(), headers=headers)
    assert response.status_code == 403
    send.assert_not_awaited()


def test_chat_does_not_accept_client_selected_history_owner() -> None:
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post(PATH, json={**_body(), "actor_id": "someone-else"}, headers=HEADERS)
    assert response.status_code == 422


@pytest.mark.parametrize("message", ["   ", "x" * 8001])
def test_chat_rejects_blank_or_oversized_messages(message: str) -> None:
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post(PATH, json={**_body(), "message": message}, headers=HEADERS)
    assert response.status_code == 422


@pytest.mark.parametrize("query", ["?limit=101", "?before_sequence=0"])
def test_history_pagination_is_bounded(query: str) -> None:
    app, _ = _app()
    with TestClient(app) as client:
        assert client.get(PATH + query).status_code == 422


def test_chat_rechecks_access_before_exposing_pending_answer() -> None:
    app, session = _app()
    with (
        patch.object(api, "_authorize_workspace", new=AsyncMock(side_effect=[None, AuthorizationDeniedError()])),
        patch.object(api, "_finish_read_only_boundary", new=AsyncMock()),
        patch.object(api, "_model_adapter", return_value=object()),
        patch.object(api, "send_chat_message", new=AsyncMock(return_value=_history())),
        TestClient(app) as client,
    ):
        response = client.post(PATH, json=_body(), headers=HEADERS)
    assert response.status_code == 403
    assert "messages" not in response.json()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_changed_revision_is_conflict_and_rolls_back_pending_turn() -> None:
    app, session = _app()
    with (
        patch.object(api, "_authorize_workspace", new=AsyncMock()),
        patch.object(api, "_finish_read_only_boundary", new=AsyncMock()),
        patch.object(api, "_model_adapter", return_value=object()),
        patch.object(api, "send_chat_message", new=AsyncMock(side_effect=StaleWorkspaceRevisionError())),
        TestClient(app) as client,
    ):
        response = client.post(PATH, json=_body(), headers=HEADERS)
    assert response.status_code == 409
    session.rollback.assert_awaited_once()


def test_chat_disabled_model_never_reaches_generation() -> None:
    app, _ = _app()
    with (
        patch.object(api, "_authorize_workspace", new=AsyncMock()),
        patch.object(api, "_finish_read_only_boundary", new=AsyncMock()),
        patch.object(api, "_model_adapter", side_effect=SspModelPolicyError("model_policy_not_approved")),
        patch.object(api, "send_chat_message", new=AsyncMock()) as send,
        TestClient(app) as client,
    ):
        response = client.post(PATH, json=_body(), headers=HEADERS)
    assert response.status_code == 403
    send.assert_not_awaited()
