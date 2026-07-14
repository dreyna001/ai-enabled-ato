"""Review/export lifecycle, approval expiry, and comment contract tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from ato_service.auth_context import (
    AuthenticatedPrincipal,
    CsrfValidationError,
    require_mutation_context,
)
from ato_service.concurrency import EtagMismatchError
from ato_service.export_service import (
    ExportValidationError,
    SelfApprovalDeniedError,
    approve_export,
    process_approval_expiry,
    reject_export,
)
from ato_service.review_revisions import (
    ReviewRevisionValidationError,
    _validate_disposition_decision,
    create_review_comment,
    update_disposition,
)

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
APPROVAL_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
EXPORT_DRAFT_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")
REVIEW_REVISION_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
RUN_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


class _System:
    owner_group = "owners"
    viewer_groups = ["viewers"]


class _Revision:
    created_by = "owner@example.test"
    profile_id = "fisma_agency_security"
    package_revision_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    system_id = uuid.UUID("22222222-2222-4222-8222-222222222222")


class _Run:
    run_id = RUN_ID
    package_revision_id = _Revision.package_revision_id


class _ReviewRevision:
    review_revision_id = REVIEW_REVISION_ID
    run_id = RUN_ID
    version = 2
    status = "draft"


class _SubmittedReviewRevision:
    review_revision_id = REVIEW_REVISION_ID
    run_id = RUN_ID
    version = 3
    status = "submitted"


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
        "path": "/api/v1/approvals/reject",
        "headers": [],
        "state": {"authenticated_principal": principal},
    }
    return Request(scope)


def _run(awaitable):
    return asyncio.run(awaitable)


def _approval(*, decision: str = "pending", submitted_by: str = "reviewer@example.test"):
    approval = MagicMock()
    approval.approval_id = APPROVAL_ID
    approval.export_draft_id = EXPORT_DRAFT_ID
    approval.payload_manifest_sha256 = "a" * 64
    approval.submitted_by = submitted_by
    approval.decided_by = None
    approval.decision = decision
    approval.submitted_at = NOW - timedelta(days=1)
    approval.decided_at = None
    approval.expires_at = NOW + timedelta(days=6)
    approval.reason = None
    return approval


def _export_draft(*, status: str = "pending_approval"):
    export_draft = MagicMock()
    export_draft.export_draft_id = EXPORT_DRAFT_ID
    export_draft.review_revision_id = REVIEW_REVISION_ID
    export_draft.payload_manifest_sha256 = "a" * 64
    export_draft.status = status
    return export_draft


def _session_for_approval(approval, export_draft):
    review_revision = MagicMock()
    review_revision.review_revision_id = REVIEW_REVISION_ID
    review_revision.run_id = RUN_ID
    review_revision.status = "submitted"

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
            return MagicMock(scalar_one_or_none=MagicMock(return_value=_Run()))
        if name == "PackageRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=_Revision()))
        if name == "System":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=_System()))
        if name == "SealedPackageContent":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=sealed))
        if name == "Disposition":
            return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        if name == "MatrixRow":
            return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    session.execute = AsyncMock(side_effect=_execute)
    return session


def test_validate_disposition_requires_edited_summary() -> None:
    with pytest.raises(ReviewRevisionValidationError) as exc_info:
        _validate_disposition_decision(decision="edited", edited_summary=None)
    assert exc_info.value.error_code == "request_schema_invalid"


def test_reject_export_happy_path() -> None:
    approval = _approval()
    export_draft = _export_draft()
    session = _session_for_approval(approval, export_draft)
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)), patch(
        "ato_service.export_service.record_idempotency_outcome",
        AsyncMock(),
    ), patch("ato_service.export_service.append_audit_event", AsyncMock()):
        result = _run(
            reject_export(
                session,
                principal=principal,
                approval_id=APPROVAL_ID,
                reason="payload incomplete",
                idempotency_key="reject-key-01",
                hmac_key=b"audit-test-key",
                now=NOW,
            )
        )

    assert result.payload["decision"] == "rejected"
    assert result.payload["reason"] == "payload incomplete"
    assert export_draft.status == "rejected"


def test_reject_export_denies_self_action() -> None:
    approval = _approval(submitted_by="approver@example.test")
    session = _session_for_approval(approval, _export_draft())
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)):
        with pytest.raises(SelfApprovalDeniedError):
            _run(
                reject_export(
                    session,
                    principal=principal,
                    approval_id=APPROVAL_ID,
                    reason="no",
                    idempotency_key="reject-key-02",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                )
            )


def test_approve_after_reject_is_denied() -> None:
    approval = _approval(decision="rejected")
    approval.decided_by = "approver@example.test"
    approval.decided_at = NOW
    export_draft = _export_draft(status="rejected")
    session = _session_for_approval(approval, export_draft)
    principal = _principal(actor_id="other-approver@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)):
        with pytest.raises(ExportValidationError) as exc_info:
            _run(
                approve_export(
                    session,
                    principal=principal,
                    approval_id=APPROVAL_ID,
                    idempotency_key="approve-key-01",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                )
            )
    assert exc_info.value.error_code == "approval_already_decided"


def test_approve_after_expired_pending_approval_transitions_draft() -> None:
    approval = _approval()
    approval.submitted_at = NOW - timedelta(days=8)
    export_draft = _export_draft()
    session = _session_for_approval(approval, export_draft)
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=None)), patch(
        "ato_service.export_service.append_audit_event",
        AsyncMock(),
    ):
        with pytest.raises(ExportValidationError) as exc_info:
            _run(
                approve_export(
                    session,
                    principal=principal,
                    approval_id=APPROVAL_ID,
                    idempotency_key="approve-key-02",
                    hmac_key=b"audit-test-key",
                    now=NOW,
                    approval_expiry_days=7,
                )
            )
    assert exc_info.value.error_code == "approval_expired"
    assert export_draft.status == "expired"


def test_reject_export_replays_idempotently() -> None:
    from ato_service.idempotency import IdempotencyReplay

    replay = IdempotencyReplay(
        response_status=200,
        response_body={"decision": "rejected", "approval_id": str(APPROVAL_ID).lower()},
        response_headers={"ETag": '"v1"'},
    )
    session = AsyncMock()
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))

    with patch("ato_service.export_service.load_idempotency_replay", AsyncMock(return_value=replay)):
        result = _run(
            reject_export(
                session,
                principal=principal,
                approval_id=APPROVAL_ID,
                reason="payload incomplete",
                idempotency_key="reject-key-03",
                hmac_key=b"audit-test-key",
                now=NOW,
            )
        )

    assert result.replayed is True
    assert result.payload["decision"] == "rejected"


def test_process_approval_expiry_uses_injected_clock() -> None:
    pending_approval = _approval()
    pending_approval.submitted_at = NOW - timedelta(days=8)
    pending_export = _export_draft(status="pending_approval")

    approved = _approval(decision="approved")
    approved.decided_at = NOW - timedelta(days=8)
    approved.decided_by = "approver@example.test"
    approved_export = _export_draft(status="approved")

    session = AsyncMock()

    async def _execute(stmt):
        if hasattr(stmt, "column_descriptions"):
            return MagicMock(all=MagicMock(return_value=[]))
        return MagicMock(all=MagicMock(return_value=[]))

    pending_rows = [(pending_approval, pending_export)]
    approved_rows = [(approved, approved_export)]

    call_count = {"value": 0}

    async def _execute_side_effect(stmt):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return MagicMock(all=MagicMock(return_value=pending_rows))
        if call_count["value"] == 2:
            return MagicMock(all=MagicMock(return_value=approved_rows))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    session.execute = AsyncMock(side_effect=_execute_side_effect)

    with patch("ato_service.export_service.append_audit_event", AsyncMock()):
        result = _run(
            process_approval_expiry(
                session,
                now=NOW,
                approval_expiry_days=7,
                hmac_key=b"audit-test-key",
            )
        )

    assert result.pending_expired == 1
    assert result.approved_expired == 1
    assert pending_export.status == "expired"
    assert approved_export.status == "expired"


def test_disposition_update_rejects_submitted_review() -> None:
    review_revision = _SubmittedReviewRevision()
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=review_revision))
    )
    principal = _principal(actor_id="owner@example.test", groups=("owners",))

    with pytest.raises(ReviewRevisionValidationError) as exc_info:
        _run(
            update_disposition(
                session,
                principal=principal,
                review_revision_id=REVIEW_REVISION_ID,
                matrix_row_id=uuid.uuid4(),
                decision="accepted",
                edited_summary=None,
                notes=None,
                if_match='"v3"',
                hmac_key=b"audit-test-key",
                now=NOW,
            )
        )
    assert exc_info.value.error_code == "illegal_state_transition"


def test_create_comment_denied_after_submit() -> None:
    review_revision = _SubmittedReviewRevision()
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=review_revision))
    )
    principal = _principal(actor_id="owner@example.test", groups=("owners",))

    with pytest.raises(ReviewRevisionValidationError) as exc_info:
        _run(
            create_review_comment(
                session,
                principal=principal,
                review_revision_id=REVIEW_REVISION_ID,
                matrix_row_id=None,
                body="late comment",
                idempotency_key="comment-key-01",
                hmac_key=b"audit-test-key",
                now=NOW,
            )
        )
    assert exc_info.value.error_code == "illegal_state_transition"


def test_mutation_context_requires_csrf_for_reject_route() -> None:
    principal = _principal(actor_id="approver@example.test", groups=("approvers",))
    request = _request(principal)
    with pytest.raises(CsrfValidationError) as exc_info:
        require_mutation_context(request, "wrong-token" + ("e" * 24), "https://portal.example")
    assert exc_info.value.error_code == "csrf_validation_failed"


def test_stale_disposition_etag_is_rejected() -> None:
    from ato_service.concurrency import assert_if_match

    with pytest.raises(EtagMismatchError):
        assert_if_match('"v1"', 2)
