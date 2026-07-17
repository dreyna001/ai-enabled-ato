"""Package preparation status derivation and authorization-decision attach tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.authorization_decisions import (
    AttachAuthorizationDecisionInput,
    attach_authorization_decision,
    map_authorization_decision_to_domain,
)
from ato_service.domain_mapping import map_package_revision_to_domain
from ato_service.package_preparation_status import (
    PACKAGE_PREPARATION_STATUS_IN_PROGRESS,
    PACKAGE_PREPARATION_STATUS_READY_FOR_EXTERNAL_REVIEW,
    _ExportCandidate,
    resolve_preparation_status_batch,
)

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
PACKAGE_REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REVIEW_REVISION_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
RUN_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
EXPORT_DRAFT_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")
APPROVAL_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
DECISION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
HASH = "a" * 64


def _run(awaitable):
    return asyncio.run(awaitable)


class _Revision:
    package_revision_id = PACKAGE_REVISION_ID
    system_id = SYSTEM_ID
    parent_revision_id = None
    profile_id = "fisma_agency_security"
    certification_class = None
    impact_level = "moderate"
    data_origin = "synthetic"
    sensitivity = "internal_unclassified"
    effective_data_labels = ["synthetic", "internal_unclassified"]
    authority_manifest_id = "authority.v2"
    content_manifest_sha256 = None
    package_content_sha256 = None
    system_context_snapshot_id = None
    revision_version = 1
    status = "ready"
    created_by = "owner@example.test"
    created_at = NOW


class _System:
    owner_group = "owners"
    viewer_groups = ["viewers"]


def _principal(*, actor_id: str, groups: tuple[str, ...]) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=actor_id,
        groups=groups,
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


def _candidate_row(*, export_status: str):
    return MagicMock(
        package_revision_id=PACKAGE_REVISION_ID,
        export_status=export_status,
        review_revision_id=REVIEW_REVISION_ID,
        run_id=RUN_ID,
        draft_hash=HASH,
        approval_hash=HASH,
        expires_at=NOW + timedelta(days=3),
    )


def test_map_package_revision_includes_preparation_status() -> None:
    payload = map_package_revision_to_domain(_Revision())
    assert payload["package_preparation_status"] == PACKAGE_PREPARATION_STATUS_IN_PROGRESS
    payload_ready = map_package_revision_to_domain(
        _Revision(),
        package_preparation_status=PACKAGE_PREPARATION_STATUS_READY_FOR_EXTERNAL_REVIEW,
    )
    assert payload_ready["package_preparation_status"] == "ready_for_external_review"


def test_resolve_preparation_status_defaults_to_in_progress() -> None:
    session = AsyncMock()

    async def _execute(_stmt):
        return MagicMock(all=MagicMock(return_value=[]))

    session.execute = _execute
    statuses = _run(
        resolve_preparation_status_batch(
            session,
            (PACKAGE_REVISION_ID,),
            project_root=MagicMock(),
            now=NOW,
        )
    )
    assert statuses[PACKAGE_REVISION_ID] == PACKAGE_PREPARATION_STATUS_IN_PROGRESS


def test_exported_draft_marks_ready_without_hash_recompute() -> None:
    session = AsyncMock()

    async def _execute(_stmt):
        return MagicMock(all=MagicMock(return_value=[_candidate_row(export_status="exported")]))

    session.execute = _execute
    with patch(
        "ato_service.package_preparation_status.compute_current_payload_manifest_sha256",
        new=AsyncMock(),
    ) as compute_hash:
        statuses = _run(
            resolve_preparation_status_batch(
                session,
                (PACKAGE_REVISION_ID,),
                project_root=MagicMock(),
                now=NOW,
            )
        )
    compute_hash.assert_not_called()
    assert statuses[PACKAGE_REVISION_ID] == PACKAGE_PREPARATION_STATUS_READY_FOR_EXTERNAL_REVIEW


def test_approved_draft_requires_valid_binding() -> None:
    session = AsyncMock()
    run = MagicMock(run_id=RUN_ID, package_revision_id=PACKAGE_REVISION_ID)
    sealed = MagicMock(document={"package": {"profile_id": "fisma_agency_security"}})
    candidate = _ExportCandidate(
        package_revision_id=PACKAGE_REVISION_ID,
        export_status="approved",
        review_revision_id=REVIEW_REVISION_ID,
        run_id=RUN_ID,
        draft_hash=HASH,
        approval_hash=HASH,
        expires_at=NOW + timedelta(days=3),
    )

    with patch(
        "ato_service.package_preparation_status._load_export_candidates",
        new=AsyncMock(return_value=(candidate,)),
    ), patch(
        "ato_service.package_preparation_status._load_review_binding_contexts",
        new=AsyncMock(return_value={REVIEW_REVISION_ID: (run, _Revision(), sealed)}),
    ), patch(
        "ato_service.package_preparation_status.compute_current_payload_manifest_sha256",
        new=AsyncMock(return_value=HASH),
    ):
        ready = _run(
            resolve_preparation_status_batch(
                session,
                (PACKAGE_REVISION_ID,),
                project_root=MagicMock(),
                now=NOW,
            )
        )
    assert ready[PACKAGE_REVISION_ID] == PACKAGE_PREPARATION_STATUS_READY_FOR_EXTERNAL_REVIEW

    with patch(
        "ato_service.package_preparation_status._load_export_candidates",
        new=AsyncMock(return_value=(candidate,)),
    ), patch(
        "ato_service.package_preparation_status._load_review_binding_contexts",
        new=AsyncMock(return_value={REVIEW_REVISION_ID: (run, _Revision(), sealed)}),
    ), patch(
        "ato_service.package_preparation_status.compute_current_payload_manifest_sha256",
        new=AsyncMock(return_value="b" * 64),
    ):
        blocked = _run(
            resolve_preparation_status_batch(
                session,
                (PACKAGE_REVISION_ID,),
                project_root=MagicMock(),
                now=NOW,
            )
        )
    assert blocked[PACKAGE_REVISION_ID] == PACKAGE_PREPARATION_STATUS_IN_PROGRESS


def test_expired_approved_draft_stays_in_progress() -> None:
    session = AsyncMock()
    expired_row = _candidate_row(export_status="approved")
    expired_row.expires_at = NOW - timedelta(minutes=1)

    async def _execute(_stmt):
        return MagicMock(all=MagicMock(return_value=[expired_row]))

    session.execute = _execute
    statuses = _run(
        resolve_preparation_status_batch(
            session,
            (PACKAGE_REVISION_ID,),
            project_root=MagicMock(),
            now=NOW,
        )
    )
    assert statuses[PACKAGE_REVISION_ID] == PACKAGE_PREPARATION_STATUS_IN_PROGRESS


def test_authorization_decision_domain_mapping_uses_contract_schema_version() -> None:
    record = MagicMock(
        authorization_decision_id=DECISION_ID,
        system_id=SYSTEM_ID,
        package_revision_id=PACKAGE_REVISION_ID,
        decision_type="authorization_to_operate",
        decision_date="2026-07-15",
        issuing_authority="Authorizing Official",
        artifact_id=None,
        notes=None,
        attached_by="isso@example.test",
        attached_at=NOW,
    )
    payload = map_authorization_decision_to_domain(record)
    assert payload["schema_version"] == "2.0.0"
    assert payload["object_type"] == "authorization_decision_record"


def test_attach_authorization_decision_does_not_touch_preparation_resolver() -> None:
    session = AsyncMock()

    async def _execute(stmt):
        entity = stmt.column_descriptions[0]["entity"]
        name = getattr(entity, "__name__", "")
        if name == "System":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=_System()))
        if name == "PackageRevision":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=_Revision()))
        if name == "IdempotencyRecord":
            return MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    session.execute = _execute
    session.add = MagicMock()

    with patch(
        "ato_service.authorization_decisions.load_idempotency_replay",
        new=AsyncMock(return_value=None),
    ), patch(
        "ato_service.authorization_decisions.record_idempotency_outcome",
        new=AsyncMock(),
    ), patch(
        "ato_service.authorization_decisions.append_audit_event",
        new=AsyncMock(),
    ), patch(
        "ato_service.package_preparation_status.resolve_preparation_status_batch",
        new=AsyncMock(),
    ) as resolve_status:
        result = _run(
            attach_authorization_decision(
                session,
                principal=_principal(actor_id="isso@example.test", groups=("isso",)),
                system_id=SYSTEM_ID,
                package_revision_id=PACKAGE_REVISION_ID,
                request=AttachAuthorizationDecisionInput(
                    decision_type="authorization_to_operate",
                    decision_date="2026-07-15",
                    issuing_authority="Authorizing Official",
                    artifact_id=None,
                    notes="External AO record",
                ),
                idempotency_key="attach-key-01",
                hmac_key=b"test-hmac-key",
                now=NOW,
            )
        )
    resolve_status.assert_not_called()
    assert result.status == 201
    assert result.payload["decision_type"] == "authorization_to_operate"
