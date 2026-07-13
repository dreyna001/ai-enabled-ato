"""Export draft, approval, and hash-bound download workflow (Component E)."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.concurrency import assert_if_match, format_package_revision_etag
from ato_service.domain_mapping import format_uuid
from ato_service.export_readiness import evaluate_export_readiness
from ato_service.idempotency import (
    IdempotencyReplay,
    load_idempotency_replay,
    record_idempotency_outcome,
    request_digest_from_payload,
)
from ato_service.package_rbac import require_package_role
from ato_service.profile_artifacts import generate_profile_artifacts

APPROVAL_EXPIRY_DAYS = 7
AI_DISCLOSURE = (
    "AI Disclosure: This report was produced with machine assistance. All findings,\n"
    "summaries, and status labels are draft inference bound to the evidence provided\n"
    "in the package. They do not constitute an official compliance determination,\n"
    "risk acceptance, certification, or authorization decision. A qualified human\n"
    "reviewer must review and approve the content before use in an authoritative\n"
    "government or customer process."
)

OPERATION_CREATE_DRAFT = "export_drafts.create"
OPERATION_SUBMIT = "export_drafts.submit"
OPERATION_APPROVE = "approvals.approve"


class ExportNotFoundError(Exception):
    error_code = "resource_not_found"


class ExportValidationError(ValueError):
    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class SelfApprovalDeniedError(Exception):
    error_code = "self_approval_denied"


@dataclass(frozen=True, slots=True)
class ExportMutationResult:
    payload: dict[str, Any]
    status: int
    etag: str
    replayed: bool


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifest_sha256(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def _load_review_context(session: AsyncSession, *, review_revision_id: uuid.UUID) -> tuple[Any, ...]:
    from ato_service.db.models import AnalysisRun, PackageRevision, ReviewRevision, SealedPackageContent, System

    review_result = await session.execute(
        select(ReviewRevision).where(ReviewRevision.review_revision_id == review_revision_id)
    )
    review_revision = review_result.scalar_one_or_none()
    if review_revision is None:
        raise ExportNotFoundError()
    run_result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.run_id == review_revision.run_id)
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise ExportNotFoundError()
    revision_result = await session.execute(
        select(PackageRevision).where(
            PackageRevision.package_revision_id == run.package_revision_id
        )
    )
    revision = revision_result.scalar_one_or_none()
    if revision is None:
        raise ExportNotFoundError()
    system_result = await session.execute(
        select(System).where(System.system_id == revision.system_id)
    )
    system = system_result.scalar_one_or_none()
    if system is None:
        raise ExportNotFoundError()
    sealed_result = await session.execute(
        select(SealedPackageContent).where(
            SealedPackageContent.package_revision_id == revision.package_revision_id
        )
    )
    sealed = sealed_result.scalar_one_or_none()
    return review_revision, run, revision, system, sealed


async def create_export_draft(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    review_revision_id: uuid.UUID,
    project_root: Path,
    authority_manifest_id: str,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> ExportMutationResult:
    from ato_service.db.models import ExportDraft

    review_revision, run, revision, system, sealed = await _load_review_context(
        session, review_revision_id=review_revision_id
    )
    try:
        require_package_role(principal, system=system, revision=revision, role="reviewer")
    except AuthorizationDeniedError:
        raise
    if review_revision.status != "submitted":
        raise ExportValidationError(
            "export draft requires a submitted review revision",
            error_code="review_not_submitted",
        )
    if sealed is None:
        raise ExportValidationError("sealed package content is required", error_code="package_not_ready")

    readiness = evaluate_export_readiness(
        profile_id=revision.profile_id,
        sealed_document=sealed.document,
        project_root=project_root,
    )
    if readiness.blockers:
        raise ExportValidationError(
            "export readiness blockers remain",
            error_code="export_not_ready",
        )

    artifacts = generate_profile_artifacts(
        profile_id=revision.profile_id,
        sealed_document=sealed.document,
        review_revision_id=review_revision_id,
        run_id=run.run_id,
    )
    manifest = {
        "schema_version": "1.0.0",
        "profile_id": revision.profile_id,
        "package_revision_id": str(revision.package_revision_id).lower(),
        "run_id": str(run.run_id).lower(),
        "review_revision_id": str(review_revision_id).lower(),
        "authority_manifest_id": authority_manifest_id,
        "files": artifacts.files,
    }
    payload_sha256 = _manifest_sha256(manifest)

    request_digest = request_digest_from_payload(
        {"review_revision_id": str(review_revision_id).lower()}
    )
    replay = await load_idempotency_replay(
        session,
        operation=OPERATION_CREATE_DRAFT,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    if isinstance(replay, IdempotencyReplay):
        return ExportMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            etag=replay.response_headers.get("ETag", '"v1"'),
            replayed=True,
        )

    export_draft_id = uuid.uuid4()
    export_draft = ExportDraft(
        export_draft_id=export_draft_id,
        review_revision_id=review_revision_id,
        payload_manifest_sha256=payload_sha256,
        destination_type="download",
        status="draft",
        created_by=principal.actor_id,
        created_at=now,
    )
    session.add(export_draft)
    payload = {
        "schema_version": "2.0.0",
        "object_type": "export_draft",
        "export_draft_id": format_uuid(export_draft_id),
        "review_revision_id": format_uuid(review_revision_id),
        "payload_manifest_sha256": payload_sha256,
        "destination_type": "download",
        "status": "draft",
        "created_by": principal.actor_id,
        "created_at": _format_utc(now),
    }
    etag = '"v1"'
    await record_idempotency_outcome(
        session,
        operation=OPERATION_CREATE_DRAFT,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=201,
        response_body=payload,
        response_headers={"ETag": etag},
        now=now,
    )
    return ExportMutationResult(payload=payload, status=201, etag=etag, replayed=False)


async def submit_export_draft(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    export_draft_id: uuid.UUID,
    if_match: str | None,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> ExportMutationResult:
    from ato_service.db.models import Approval, ExportDraft, ReviewRevision

    draft_result = await session.execute(
        select(ExportDraft).where(ExportDraft.export_draft_id == export_draft_id)
    )
    export_draft = draft_result.scalar_one_or_none()
    if export_draft is None:
        raise ExportNotFoundError()
    review_revision, run, revision, system, _ = await _load_review_context(
        session, review_revision_id=export_draft.review_revision_id
    )
    try:
        require_package_role(principal, system=system, revision=revision, role="reviewer")
    except AuthorizationDeniedError:
        raise
    assert_if_match(if_match=if_match, current_version=1)
    if export_draft.status != "draft":
        raise ExportValidationError("export draft is not in draft status")

    approval_id = uuid.uuid4()
    expires_at = now + timedelta(days=APPROVAL_EXPIRY_DAYS)
    approval = Approval(
        approval_id=approval_id,
        export_draft_id=export_draft_id,
        payload_manifest_sha256=export_draft.payload_manifest_sha256,
        submitted_by=principal.actor_id,
        decided_by=None,
        decision="pending",
        submitted_at=now,
        decided_at=None,
        expires_at=expires_at,
        reason=None,
    )
    export_draft.status = "pending_approval"
    session.add(approval)
    payload = {
        "schema_version": "2.0.0",
        "object_type": "approval",
        "approval_id": format_uuid(approval_id),
        "export_draft_id": format_uuid(export_draft_id),
        "payload_manifest_sha256": export_draft.payload_manifest_sha256,
        "submitted_by": principal.actor_id,
        "decided_by": None,
        "decision": "pending",
        "submitted_at": _format_utc(now),
        "decided_at": None,
        "expires_at": _format_utc(expires_at),
        "reason": None,
    }
    return ExportMutationResult(payload=payload, status=201, etag='"v1"', replayed=False)


async def approve_export(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    approval_id: uuid.UUID,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> ExportMutationResult:
    from ato_service.db.models import Approval, ExportDraft

    approval_result = await session.execute(
        select(Approval).where(Approval.approval_id == approval_id)
    )
    approval = approval_result.scalar_one_or_none()
    if approval is None:
        raise ExportNotFoundError()
    if approval.submitted_by == principal.actor_id:
        raise SelfApprovalDeniedError()
    if approval.decision != "pending":
        raise ExportValidationError("approval is not pending")
    if now >= approval.expires_at:
        raise ExportValidationError("approval has expired", error_code="approval_expired")

    draft_result = await session.execute(
        select(ExportDraft).where(ExportDraft.export_draft_id == approval.export_draft_id)
    )
    export_draft = draft_result.scalar_one_or_none()
    if export_draft is None:
        raise ExportNotFoundError()
    review_revision, run, revision, system, sealed = await _load_review_context(
        session, review_revision_id=export_draft.review_revision_id
    )
    try:
        require_package_role(principal, system=system, revision=revision, role="approver")
    except AuthorizationDeniedError:
        raise

    approval.decision = "approved"
    approval.decided_by = principal.actor_id
    approval.decided_at = now
    export_draft.status = "approved"
    payload = {
        "schema_version": "2.0.0",
        "object_type": "approval",
        "approval_id": format_uuid(approval_id),
        "export_draft_id": format_uuid(approval.export_draft_id),
        "payload_manifest_sha256": approval.payload_manifest_sha256,
        "submitted_by": approval.submitted_by,
        "decided_by": principal.actor_id,
        "decision": "approved",
        "submitted_at": _format_utc(approval.submitted_at),
        "decided_at": _format_utc(now),
        "expires_at": _format_utc(approval.expires_at),
        "reason": approval.reason,
    }
    return ExportMutationResult(payload=payload, status=200, etag='"v1"', replayed=False)
