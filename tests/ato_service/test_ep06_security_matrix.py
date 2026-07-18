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
from ato_service.export_service import (
    SelfApprovalDeniedError,
    approve_export,
    deliver_export_download,
    reject_export,
)
from ato_service.package_rbac import (
    DEFAULT_PACKAGE_ROLE_GROUPS,
    configure_package_role_groups,
    package_role_groups,
    require_any_package_role,
    require_package_role,
)
from ato_service.route_role_matrix import enforce_route_roles, route_roles

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
APPROVAL_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


class _System:
    owner_group = "owners"
    viewer_groups = ["viewers", "approvers"]


class _Revision:
    created_by = "owner@example.test"


class _UnauthorizedSystem:
    owner_group = "other-owners"
    viewer_groups = ["other-viewers"]


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


def _session_for_approve(approval, export_draft, *, system=None):
    review_revision = MagicMock()
    review_revision.review_revision_id = export_draft.review_revision_id
    review_revision.run_id = uuid.uuid4()
    review_revision.status = "submitted"

    revision = MagicMock()
    revision.profile_id = "fisma_agency_security"
    revision.package_revision_id = uuid.uuid4()
    revision.system_id = uuid.uuid4()

    run = MagicMock()
    run.run_id = review_revision.run_id
    run.package_revision_id = revision.package_revision_id

    sealed = MagicMock()
    sealed.document = {"package": {"profile_id": "fisma_agency_security"}}

    session = AsyncMock()

    async def _execute(stmt):
        entity = stmt.column_descriptions[0]["entity"]
        name = getattr(entity, "__name__", "")
        if name == "Approval":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=approval))
        if name == "ExportDraft":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=export_draft))
        if name == "ReviewRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=review_revision))
        if name == "AnalysisRun":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=run))
        if name == "PackageRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=revision))
        if name == "System":
            return MagicMock(
                scalar_one_or_none=MagicMock(return_value=system or _System())
            )
        if name == "SealedPackageContent":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=sealed))
        if name in {"Disposition", "MatrixRow"}:
            return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    session.execute = AsyncMock(side_effect=_execute)
    return session


def test_self_approval_is_denied() -> None:
    configure_package_role_groups({})
    approval = MagicMock()
    approval.approval_id = APPROVAL_ID
    approval.submitted_by = "reviewer@example.test"
    approval.decision = "pending"
    approval.expires_at = NOW + timedelta(days=7)
    approval.export_draft_id = uuid.uuid4()
    approval.payload_manifest_sha256 = "a" * 64

    export_draft = MagicMock()
    export_draft.export_draft_id = approval.export_draft_id
    export_draft.review_revision_id = uuid.uuid4()
    export_draft.status = "pending_approval"

    session = _session_for_approve(approval, export_draft)
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


def test_self_approval_is_denied_when_flag_explicitly_false() -> None:
    configure_package_role_groups({"SINGLE_USER_MODE_ENABLED": False})
    approval = MagicMock()
    approval.approval_id = APPROVAL_ID
    approval.submitted_by = "reviewer@example.test"
    approval.decision = "pending"
    approval.expires_at = NOW + timedelta(days=7)
    approval.export_draft_id = uuid.uuid4()
    approval.payload_manifest_sha256 = "a" * 64

    export_draft = MagicMock()
    export_draft.export_draft_id = approval.export_draft_id
    export_draft.review_revision_id = uuid.uuid4()
    export_draft.status = "pending_approval"

    session = _session_for_approve(approval, export_draft)
    principal = _principal(actor_id="reviewer@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)):
        with pytest.raises(SelfApprovalDeniedError):
            _run(
                approve_export(
                    session,
                    principal=principal,
                    approval_id=APPROVAL_ID,
                    idempotency_key="idempotency-key-02",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                    single_user_mode_enabled=False,
                )
            )


def test_single_user_mode_allows_self_approval_with_approver_role() -> None:
    configure_package_role_groups(
        {
            "SINGLE_USER_MODE_ENABLED": True,
            "OIDC_GROUP_ROLE_MAPPING": {
                "system_owner": ["owners"],
                "viewer": ["viewers"],
            },
        }
    )
    approval = MagicMock()
    approval.approval_id = APPROVAL_ID
    approval.submitted_by = "owner@example.test"
    approval.decision = "pending"
    approval.submitted_at = NOW - timedelta(days=1)
    approval.expires_at = NOW + timedelta(days=6)
    approval.export_draft_id = uuid.uuid4()
    approval.payload_manifest_sha256 = "a" * 64
    approval.decided_by = None
    approval.decided_at = None
    approval.reason = None

    export_draft = MagicMock()
    export_draft.export_draft_id = approval.export_draft_id
    export_draft.review_revision_id = uuid.uuid4()
    export_draft.status = "pending_approval"
    export_draft.payload_manifest_sha256 = approval.payload_manifest_sha256

    review_revision = MagicMock()
    review_revision.review_revision_id = export_draft.review_revision_id
    review_revision.run_id = uuid.uuid4()
    review_revision.status = "submitted"

    revision = MagicMock()
    revision.profile_id = "fisma_agency_security"
    revision.package_revision_id = uuid.uuid4()
    revision.system_id = uuid.uuid4()

    run = MagicMock()
    run.run_id = review_revision.run_id
    run.package_revision_id = revision.package_revision_id

    sealed = MagicMock()
    sealed.document = {"package": {"profile_id": "fisma_agency_security"}}

    session = AsyncMock()

    async def _execute(stmt):
        entity = stmt.column_descriptions[0]["entity"]
        name = getattr(entity, "__name__", "")
        if name == "Approval":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=approval))
        if name == "ExportDraft":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=export_draft))
        if name == "ReviewRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=review_revision))
        if name == "AnalysisRun":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=run))
        if name == "PackageRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=revision))
        if name == "System":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=_System()))
        if name == "SealedPackageContent":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=sealed))
        if name in {"Disposition", "MatrixRow"}:
            return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    session.execute = AsyncMock(side_effect=_execute)
    principal = _principal(actor_id="owner@example.test", groups=("owners",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)), patch(
        "ato_service.export_service.record_idempotency_outcome",
        AsyncMock(),
    ), patch("ato_service.export_service.append_audit_event", AsyncMock()) as audit_event:
        result = _run(
            approve_export(
                session,
                principal=principal,
                approval_id=APPROVAL_ID,
                idempotency_key="idempotency-key-03",
                hmac_key=b"audit-test-key",
                now=NOW,
                single_user_mode_enabled=True,
            )
        )

    assert result.payload["decision"] == "approved"
    assert result.payload["submitted_by"] == "owner@example.test"
    assert result.payload["decided_by"] == "owner@example.test"
    audit_kwargs = audit_event.await_args.kwargs
    assert audit_kwargs["metadata"]["single_user_mode"] is True
    assert audit_kwargs["metadata"]["self_decision"] is True


def test_single_user_mode_still_denies_viewer_without_approver_role() -> None:
    configure_package_role_groups(
        {
            "SINGLE_USER_MODE_ENABLED": True,
            "OIDC_GROUP_ROLE_MAPPING": {
                "system_owner": ["owners"],
                "viewer": ["viewers"],
            },
        }
    )
    principal = _principal(actor_id="viewer@example.test", groups=("viewers",))
    with pytest.raises(AuthorizationDeniedError):
        require_package_role(
            principal,
            system=_System(),
            revision=_Revision(),
            role="approver",
        )


def test_single_user_mode_maps_owner_groups_to_approver() -> None:
    configure_package_role_groups(
        {
            "SINGLE_USER_MODE_ENABLED": True,
            "OIDC_GROUP_ROLE_MAPPING": {
                "system_owner": ["owners"],
                "viewer": ["viewers"],
            },
        }
    )
    groups = package_role_groups()
    assert "owners" in groups["approver"]
    assert "viewers" not in groups["approver"]


def test_single_user_role_expansion_does_not_leak_across_config_loads() -> None:
    mapping = {
        "system_owner": ["single-user-owners"],
        "reviewer": ["single-user-reviewers"],
    }
    configure_package_role_groups(
        {
            "SINGLE_USER_MODE_ENABLED": True,
            "OIDC_GROUP_ROLE_MAPPING": mapping,
        }
    )
    assert "single-user-owners" in package_role_groups()["approver"]
    assert "single-user-reviewers" in package_role_groups()["approver"]

    configure_package_role_groups(
        {
            "SINGLE_USER_MODE_ENABLED": False,
            "OIDC_GROUP_ROLE_MAPPING": mapping,
        }
    )
    assert package_role_groups()["approver"] == ("approvers",)

    configure_package_role_groups({"runtime_profile": "dev_local"})
    assert package_role_groups() == DEFAULT_PACKAGE_ROLE_GROUPS
    assert package_role_groups()["approver"] == ("approvers",)
    assert "single-user-owners" not in package_role_groups()["ao_custodian"]
    assert "single-user-reviewers" not in package_role_groups()["ao_custodian"]


def test_single_user_actor_with_role_but_wrong_target_scope_is_denied() -> None:
    configure_package_role_groups(
        {
            "SINGLE_USER_MODE_ENABLED": True,
            "OIDC_GROUP_ROLE_MAPPING": {
                "system_owner": ["owners"],
                "reviewer": ["reviewers"],
            },
        }
    )
    approval = MagicMock()
    approval.submitted_by = "owner@example.test"
    approval.export_draft_id = uuid.uuid4()
    export_draft = MagicMock()
    export_draft.export_draft_id = approval.export_draft_id
    export_draft.review_revision_id = uuid.uuid4()
    session = _session_for_approve(
        approval,
        export_draft,
        system=_UnauthorizedSystem(),
    )
    principal = _principal(actor_id="owner@example.test", groups=("owners",))

    with patch(
        "ato_service.export_service.load_idempotency_replay",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(AuthorizationDeniedError) as exc_info:
            _run(
                approve_export(
                    session,
                    principal=principal,
                    approval_id=APPROVAL_ID,
                    idempotency_key="idempotency-key-wrong-single-user-target",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                    single_user_mode_enabled=True,
                )
            )

    assert exc_info.value.error_code == "authorization_denied"
    assert "other-owners" not in str(exc_info.value)
    assert "other-viewers" not in str(exc_info.value)


def test_dedicated_approver_without_target_scope_is_denied() -> None:
    configure_package_role_groups({})
    approval = MagicMock()
    approval.submitted_by = "reviewer@example.test"
    approval.export_draft_id = uuid.uuid4()
    export_draft = MagicMock()
    export_draft.export_draft_id = approval.export_draft_id
    export_draft.review_revision_id = uuid.uuid4()
    session = _session_for_approve(
        approval,
        export_draft,
        system=_UnauthorizedSystem(),
    )
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))

    with patch(
        "ato_service.export_service.load_idempotency_replay",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(AuthorizationDeniedError) as exc_info:
            _run(
                reject_export(
                    session,
                    principal=principal,
                    approval_id=APPROVAL_ID,
                    reason="not authorized for this target",
                    idempotency_key="idempotency-key-wrong-approver-target",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                )
            )

    assert exc_info.value.error_code == "authorization_denied"
    assert "other-owners" not in str(exc_info.value)
    assert "other-viewers" not in str(exc_info.value)


def test_non_self_approval_with_valid_target_scope_is_unchanged() -> None:
    configure_package_role_groups({})
    approval = MagicMock()
    approval.approval_id = APPROVAL_ID
    approval.submitted_by = "reviewer@example.test"
    approval.decision = "pending"
    approval.submitted_at = NOW - timedelta(days=1)
    approval.expires_at = NOW + timedelta(days=6)
    approval.export_draft_id = uuid.uuid4()
    approval.payload_manifest_sha256 = "a" * 64
    approval.decided_by = None
    approval.decided_at = None
    approval.reason = None

    export_draft = MagicMock()
    export_draft.export_draft_id = approval.export_draft_id
    export_draft.review_revision_id = uuid.uuid4()
    export_draft.status = "pending_approval"
    export_draft.payload_manifest_sha256 = approval.payload_manifest_sha256

    review_revision = MagicMock()
    review_revision.review_revision_id = export_draft.review_revision_id
    review_revision.run_id = uuid.uuid4()
    review_revision.status = "submitted"

    revision = MagicMock()
    revision.profile_id = "fisma_agency_security"
    revision.package_revision_id = uuid.uuid4()
    revision.system_id = uuid.uuid4()

    run = MagicMock()
    run.run_id = review_revision.run_id
    run.package_revision_id = revision.package_revision_id

    sealed = MagicMock()
    sealed.document = {"package": {"profile_id": "fisma_agency_security"}}

    session = AsyncMock()

    async def _execute(stmt):
        entity = stmt.column_descriptions[0]["entity"]
        name = getattr(entity, "__name__", "")
        if name == "Approval":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=approval))
        if name == "ExportDraft":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=export_draft))
        if name == "ReviewRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=review_revision))
        if name == "AnalysisRun":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=run))
        if name == "PackageRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=revision))
        if name == "System":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=_System()))
        if name == "SealedPackageContent":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=sealed))
        if name in {"Disposition", "MatrixRow"}:
            return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    session.execute = AsyncMock(side_effect=_execute)
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)), patch(
        "ato_service.export_service.record_idempotency_outcome",
        AsyncMock(),
    ), patch("ato_service.export_service.append_audit_event", AsyncMock()) as audit_event:
        result = _run(
            approve_export(
                session,
                principal=principal,
                approval_id=APPROVAL_ID,
                idempotency_key="idempotency-key-04",
                hmac_key=b"audit-test-key",
                now=NOW,
                single_user_mode_enabled=True,
            )
        )

    assert result.payload["decision"] == "approved"
    assert result.payload["decided_by"] == "approver@example.test"
    assert "self_decision" not in audit_event.await_args.kwargs["metadata"]


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
