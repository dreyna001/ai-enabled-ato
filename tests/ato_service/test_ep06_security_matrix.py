"""EP-06 RBAC, CSRF, and self-approval security matrix tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    CsrfValidationError,
    require_mutation_context,
)
from ato_service.export_service import SelfApprovalDeniedError, approve_export, deliver_export_download
from ato_service.package_rbac import require_any_package_role, require_package_role
from ato_service.route_role_matrix import enforce_route_roles, route_roles

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
APPROVAL_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


class _System:
    owner_group = "owners"
    viewer_groups = ["viewers"]


class _Revision:
    created_by = "owner@example.test"


def _principal(*, actor_id: str, groups: tuple[str, ...]) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=actor_id,
        groups=groups,
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


def _request(principal: AuthenticatedPrincipal) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/approvals/approve",
        "headers": [],
        "state": {"authenticated_principal": principal},
    }
    return Request(scope)


def _run(awaitable):
    return asyncio.run(awaitable)


def test_package_rbac_denies_viewer_mutation_roles() -> None:
    principal = _principal(actor_id="viewer@example.test", groups=("viewers",))
    with pytest.raises(AuthorizationDeniedError):
        require_package_role(
            principal,
            system=_System(),
            revision=_Revision(),
            role="reviewer",
        )


def test_package_rbac_allows_owner_as_reviewer() -> None:
    principal = _principal(actor_id="owner@example.test", groups=("owners",))
    require_package_role(
        principal,
        system=_System(),
        revision=_Revision(),
        role="reviewer",
    )


def test_package_rbac_allows_dedicated_approver_group() -> None:
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))
    require_package_role(
        principal,
        system=_System(),
        revision=_Revision(),
        role="approver",
    )


def test_mutation_context_requires_csrf_and_origin() -> None:
    principal = _principal(actor_id="owner@example.test", groups=("owners",))
    request = _request(principal)
    with pytest.raises(CsrfValidationError) as exc_info:
        require_mutation_context(request, "wrong-token" + ("e" * 24), "https://portal.example")
    assert exc_info.value.error_code == "csrf_validation_failed"


def test_self_approval_is_denied() -> None:
    approval = MagicMock()
    approval.approval_id = APPROVAL_ID
    approval.submitted_by = "reviewer@example.test"
    approval.decision = "pending"
    approval.expires_at = NOW + timedelta(days=7)
    approval.export_draft_id = uuid.uuid4()
    approval.payload_manifest_sha256 = "a" * 64

    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=approval),
        )
    )

    principal = _principal(actor_id="reviewer@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)):
        with pytest.raises(SelfApprovalDeniedError):
            _run(
                approve_export(
                    session,
                    principal=principal,
                    approval_id=APPROVAL_ID,
                    idempotency_key="idempotency-key-01",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                )
            )


def test_assessor_may_start_runs_but_not_cancel() -> None:
    principal = _principal(actor_id="assessor@example.test", groups=("assessors",))
    enforce_route_roles(
        principal,
        method="POST",
        path="/package-revisions/{id}/runs",
        system=_System(),
        revision=_Revision(),
    )
    with pytest.raises(AuthorizationDeniedError):
        enforce_route_roles(
            principal,
            method="POST",
            path="/runs/{run_id}/cancel",
            system=_System(),
            revision=_Revision(),
        )


def test_control_owner_may_upload_but_not_finalize() -> None:
    principal = _principal(actor_id="control@example.test", groups=("control-owners",))
    enforce_route_roles(
        principal,
        method="POST",
        path="/package-revisions/{id}/files",
        system=_System(),
        revision=_Revision(),
    )
    with pytest.raises(AuthorizationDeniedError):
        enforce_route_roles(
            principal,
            method="POST",
            path="/package-revisions/{id}/finalize",
            system=_System(),
            revision=_Revision(),
        )


def test_isso_may_confirm_but_viewer_may_not() -> None:
    isso = _principal(actor_id="isso@example.test", groups=("isso",))
    enforce_route_roles(
        isso,
        method="POST",
        path="/package-revisions/{id}/confirm",
        system=_System(),
        revision=_Revision(),
    )
    viewer = _principal(actor_id="viewer@example.test", groups=("viewers",))
    with pytest.raises(AuthorizationDeniedError):
        enforce_route_roles(
            viewer,
            method="POST",
            path="/package-revisions/{id}/confirm",
            system=_System(),
            revision=_Revision(),
        )


def test_ao_custodian_may_attach_authorization_decisions() -> None:
    principal = _principal(actor_id="custodian@example.test", groups=("ao-custodians",))
    roles = route_roles(method="POST", path="/systems/{system_id}/authorization-decisions")
    require_any_package_role(principal, system=_System(), roles=roles)


def test_viewer_is_denied_reviewer_export_submit() -> None:
    principal = _principal(actor_id="viewer@example.test", groups=("viewers",))
    with pytest.raises(AuthorizationDeniedError):
        enforce_route_roles(
            principal,
            method="POST",
            path="/export-drafts/{id}/submit",
            system=_System(),
            revision=_Revision(),
        )


def test_export_download_denies_unauthorized_viewer() -> None:
    export_id = uuid.UUID("99999999-9999-4999-8999-999999999999")
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    principal = _principal(actor_id="outsider@example.test", groups=("public",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)):
        with pytest.raises(Exception):
            _run(
                deliver_export_download(
                    session,
                    principal=principal,
                    export_id=export_id,
                    storage_root=MagicMock(),
                    project_root=MagicMock(),
                    authority_manifest_id="fixture.draft",
                    idempotency_key="download-key-01",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                )
            )
