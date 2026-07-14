"""Export draft, approval, and hash-bound download workflow (Component E)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.concurrency import assert_if_match
from ato_service.domain_mapping import format_uuid
from ato_service.export_assembly import ExportAssemblyError, assemble_export_bundle
from ato_service.export_readiness import evaluate_export_readiness
from ato_service.idempotency import (
    IdempotencyReplay,
    load_idempotency_replay,
    record_idempotency_outcome,
    request_digest_from_payload,
)
from ato_service.package_rbac import require_package_role
from ato_service.profile_artifacts import generate_profile_artifacts

_DEFAULT_APPROVAL_EXPIRY_DAYS = 7
_SUPERSEDEABLE_EXPORT_DRAFT_STATUSES = frozenset({"draft", "pending_approval", "approved"})

OPERATION_CREATE_DRAFT = "export_drafts.create"
OPERATION_SUBMIT = "export_drafts.submit"
OPERATION_APPROVE = "approvals.approve"
OPERATION_REJECT = "approvals.reject"
OPERATION_DOWNLOAD = "exports.download"


class ExportNotFoundError(Exception):
    error_code = "resource_not_found"


class ExportValidationError(ValueError):
    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class SelfApprovalDeniedError(Exception):
    error_code = "self_approval_denied"


class ApprovalAlreadyDecidedError(Exception):
    error_code = "approval_already_decided"


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


def _approval_payload_digest(
    *,
    approval_id: uuid.UUID,
    decision: str,
    reason: str | None,
) -> str:
    return request_digest_from_payload(
        {
            "approval_id": str(approval_id).lower(),
            "decision": decision,
            "reason": reason,
        }
    )


def _map_approval_to_domain(approval: Any) -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "object_type": "approval",
        "approval_id": format_uuid(approval.approval_id),
        "export_draft_id": format_uuid(approval.export_draft_id),
        "payload_manifest_sha256": approval.payload_manifest_sha256,
        "submitted_by": approval.submitted_by,
        "decided_by": approval.decided_by,
        "decision": approval.decision,
        "submitted_at": _format_utc(approval.submitted_at),
        "decided_at": _format_utc(approval.decided_at) if approval.decided_at is not None else None,
        "expires_at": _format_utc(approval.expires_at),
        "reason": approval.reason,
    }


async def _supersede_stale_export_drafts(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
    payload_manifest_sha256: str,
    hmac_key: bytes,
    actor_id: str,
    now: datetime,
) -> None:
    from ato_service.db.models import ExportDraft

    draft_result = await session.execute(
        select(ExportDraft).where(ExportDraft.review_revision_id == review_revision_id)
    )
    for export_draft in draft_result.scalars().all():
        if export_draft.status not in _SUPERSEDEABLE_EXPORT_DRAFT_STATUSES:
            continue
        if export_draft.payload_manifest_sha256 == payload_manifest_sha256:
            continue
        export_draft.status = "superseded"
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="user",
            actor_id=actor_id,
            action="export_draft.superseded",
            object_type="export_draft",
            object_id=str(export_draft.export_draft_id).lower(),
            outcome="succeeded",
            reason_code="approval_payload_mismatch",
            metadata={
                "review_revision_id": str(review_revision_id).lower(),
                "payload_manifest_sha256": export_draft.payload_manifest_sha256,
            },
            now=now,
        )


async def _compute_current_payload_manifest_sha256(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
    run_id: uuid.UUID,
    revision: Any,
    sealed: Any,
    project_root: Path,
    authority_manifest_id: str,
) -> str:
    dispositions, matrix_rows = await _load_export_bundle_inputs(
        session,
        review_revision_id=review_revision_id,
        run_id=run_id,
    )
    artifacts = generate_profile_artifacts(
        profile_id=revision.profile_id,
        sealed_document=sealed.document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
    )
    manifest = {
        "schema_version": "1.0.0",
        "profile_id": revision.profile_id,
        "package_revision_id": str(revision.package_revision_id).lower(),
        "run_id": str(run_id).lower(),
        "review_revision_id": str(review_revision_id).lower(),
        "authority_manifest_id": authority_manifest_id,
        "files": artifacts.files,
    }
    return _manifest_sha256(manifest)


async def _assert_approval_pending_and_fresh(
    session: AsyncSession,
    *,
    approval: Any,
    export_draft: Any,
    approval_expiry_days: int,
    hmac_key: bytes,
    actor_id: str,
    now: datetime,
) -> None:
    if approval.decision != "pending":
        raise ApprovalAlreadyDecidedError()
    if export_draft.status != "pending_approval":
        raise ExportValidationError(
            "export draft is not pending approval",
            error_code="illegal_state_transition",
        )
    pending_deadline = approval.submitted_at + timedelta(days=approval_expiry_days)
    if now >= pending_deadline:
        export_draft.status = "expired"
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="service",
            actor_id=actor_id,
            action="approval.expired",
            object_type="approval",
            object_id=str(approval.approval_id).lower(),
            outcome="succeeded",
            reason_code="approval_expired",
            metadata={"export_draft_id": str(approval.export_draft_id).lower()},
            now=now,
        )
        raise ExportValidationError("approval has expired", error_code="approval_expired")


async def _verify_approval_payload_binding(
    session: AsyncSession,
    *,
    approval: Any,
    export_draft: Any,
    review_revision_id: uuid.UUID,
    run_id: uuid.UUID,
    revision: Any,
    sealed: Any,
    project_root: Path,
    authority_manifest_id: str,
    hmac_key: bytes,
    actor_id: str,
    now: datetime,
) -> None:
    current_hash = await _compute_current_payload_manifest_sha256(
        session,
        review_revision_id=review_revision_id,
        run_id=run_id,
        revision=revision,
        sealed=sealed,
        project_root=project_root,
        authority_manifest_id=authority_manifest_id,
    )
    if current_hash == approval.payload_manifest_sha256:
        return
    if export_draft.status in _SUPERSEDEABLE_EXPORT_DRAFT_STATUSES:
        export_draft.status = "superseded"
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="user",
            actor_id=actor_id,
            action="export_draft.superseded",
            object_type="export_draft",
            object_id=str(export_draft.export_draft_id).lower(),
            outcome="denied",
            reason_code="approval_payload_mismatch",
            metadata={
                "expected_payload_manifest_sha256": approval.payload_manifest_sha256,
                "current_payload_manifest_sha256": current_hash,
            },
            now=now,
        )
    raise ExportValidationError(
        "approval payload hash mismatch",
        error_code="approval_payload_mismatch",
    )


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


async def _load_export_bundle_inputs(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
    run_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from ato_service.db.models import Disposition, MatrixRow

    disposition_result = await session.execute(
        select(Disposition).where(Disposition.review_revision_id == review_revision_id)
    )
    dispositions = [
        {
            "matrix_row_id": str(item.matrix_row_id).lower(),
            "decision": item.decision,
            "edited_summary": item.edited_summary,
            "notes": item.notes,
            "version": item.version,
            "decided_by": item.decided_by,
            "decided_at": _format_utc(item.decided_at),
        }
        for item in disposition_result.scalars().all()
    ]
    matrix_result = await session.execute(
        select(MatrixRow).where(MatrixRow.run_id == run_id)
    )
    matrix_rows = [
        {
            "matrix_row_id": str(item.matrix_row_id).lower(),
            "assessment_item_id": item.assessment_item_id,
            "model_proposed_status": item.model_proposed_status,
            "system_status": item.system_status,
            "finding_summary": item.finding_summary,
            "citations": list(item.citations),
        }
        for item in matrix_result.scalars().all()
    ]
    return dispositions, matrix_rows


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

    dispositions, matrix_rows = await _load_export_bundle_inputs(
        session,
        review_revision_id=review_revision_id,
        run_id=run.run_id,
    )
    artifacts = generate_profile_artifacts(
        profile_id=revision.profile_id,
        sealed_document=sealed.document,
        review_revision_id=review_revision_id,
        run_id=run.run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
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
        principal.actor_id,
        OPERATION_CREATE_DRAFT,
        idempotency_key,
        request_digest,
        now,
    )
    if isinstance(replay, IdempotencyReplay):
        return ExportMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            etag=replay.response_headers.get("ETag", '"v1"'),
            replayed=True,
        )

    await _supersede_stale_export_drafts(
        session,
        review_revision_id=review_revision_id,
        payload_manifest_sha256=payload_sha256,
        hmac_key=hmac_key,
        actor_id=principal.actor_id,
        now=now,
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
        principal=principal.actor_id,
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
    approval_expiry_days: int = _DEFAULT_APPROVAL_EXPIRY_DAYS,
) -> ExportMutationResult:
    from ato_service.db.models import Approval, ExportDraft

    request_digest = request_digest_from_payload(
        {"export_draft_id": str(export_draft_id).lower()}
    )
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_SUBMIT,
        idempotency_key,
        request_digest,
        now,
    )
    if isinstance(replay, IdempotencyReplay):
        return ExportMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            etag=replay.response_headers.get("ETag", '"v1"'),
            replayed=True,
        )

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
        raise ExportValidationError(
            "export draft is not in draft status",
            error_code="illegal_state_transition",
        )

    approval_id = uuid.uuid4()
    expires_at = now + timedelta(days=approval_expiry_days)
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
    payload = _map_approval_to_domain(approval)
    etag = '"v1"'
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="export_draft.submit",
        object_type="export_draft",
        object_id=str(export_draft_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={"approval_id": str(approval_id).lower()},
        now=now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_SUBMIT,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=201,
        response_body=payload,
        response_headers={"ETag": etag},
        now=now,
    )
    return ExportMutationResult(payload=payload, status=201, etag=etag, replayed=False)


@dataclass(frozen=True, slots=True)
class ExportDownloadResult:
    zip_bytes: bytes
    filename: str
    export_id: uuid.UUID
    replayed: bool
    status: int


async def deliver_export_download(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    export_id: uuid.UUID,
    storage_root: Path,
    project_root: Path,
    authority_manifest_id: str,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> ExportDownloadResult:
    from ato_service.db.models import (
        Approval,
        ExportDraft,
        ExportRecord,
    )

    request_digest = request_digest_from_payload({"export_id": str(export_id).lower()})
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_DOWNLOAD,
        idempotency_key,
        request_digest,
        now,
    )
    if isinstance(replay, IdempotencyReplay):
        stored_export_id = uuid.UUID(replay.response_body["export_id"])
        record_result = await session.execute(
            select(ExportRecord).where(ExportRecord.export_id == stored_export_id)
        )
        record = record_result.scalar_one_or_none()
        if record is None:
            raise ExportNotFoundError()
        zip_bytes = _read_export_zip(storage_root=storage_root, storage_key=record.storage_key)
        return ExportDownloadResult(
            zip_bytes=zip_bytes,
            filename=_export_filename(record.export_id),
            export_id=record.export_id,
            replayed=True,
            status=replay.response_status,
        )

    record_result = await session.execute(
        select(ExportRecord).where(ExportRecord.export_id == export_id)
    )
    existing = record_result.scalar_one_or_none()
    if existing is not None:
        review_revision, run, revision, system, _ = await _load_review_context(
            session, review_revision_id=existing.review_revision_id
        )
        try:
            require_package_role(principal, system=system, revision=revision, role="viewer")
        except AuthorizationDeniedError:
            raise
        zip_bytes = _read_export_zip(storage_root=storage_root, storage_key=existing.storage_key)
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="user",
            actor_id=principal.actor_id,
            action="export.download",
            object_type="export",
            object_id=str(existing.export_id).lower(),
            outcome="succeeded",
            reason_code=None,
            metadata={"replayed": True},
            now=now,
        )
        return ExportDownloadResult(
            zip_bytes=zip_bytes,
            filename=_export_filename(existing.export_id),
            export_id=existing.export_id,
            replayed=True,
            status=200,
        )

    approval_result = await session.execute(
        select(Approval).where(Approval.approval_id == export_id)
    )
    approval = approval_result.scalar_one_or_none()
    if approval is None:
        raise ExportNotFoundError()
    if approval.decision != "approved":
        raise ExportValidationError("approval is not approved", error_code="authorization_denied")
    if now >= approval.expires_at:
        raise ExportValidationError("approval has expired", error_code="export_expired")

    draft_result = await session.execute(
        select(ExportDraft).where(ExportDraft.export_draft_id == approval.export_draft_id)
    )
    export_draft = draft_result.scalar_one_or_none()
    if export_draft is None:
        raise ExportNotFoundError()
    if export_draft.status == "superseded":
        raise ExportValidationError(
            "export draft has been superseded",
            error_code="approval_payload_mismatch",
        )
    if export_draft.status == "expired":
        raise ExportValidationError("export has expired", error_code="export_expired")
    if export_draft.status != "approved":
        raise ExportValidationError("export draft is not approved", error_code="illegal_state_transition")
    if export_draft.payload_manifest_sha256 != approval.payload_manifest_sha256:
        raise ExportAssemblyError(
            "approval payload hash mismatch",
            error_code="approval_payload_mismatch",
        )

    review_revision, run, revision, system, sealed = await _load_review_context(
        session, review_revision_id=export_draft.review_revision_id
    )
    try:
        require_package_role(principal, system=system, revision=revision, role="viewer")
    except AuthorizationDeniedError:
        raise
    if sealed is None:
        raise ExportValidationError("sealed package content is required", error_code="package_not_ready")

    await _verify_approval_payload_binding(
        session,
        approval=approval,
        export_draft=export_draft,
        review_revision_id=export_draft.review_revision_id,
        run_id=run.run_id,
        revision=revision,
        sealed=sealed,
        project_root=project_root,
        authority_manifest_id=authority_manifest_id,
        hmac_key=hmac_key,
        actor_id=principal.actor_id,
        now=now,
    )

    dispositions, matrix_rows = await _load_export_bundle_inputs(
        session,
        review_revision_id=export_draft.review_revision_id,
        run_id=run.run_id,
    )

    created_at = _format_utc(now)
    try:
        bundle = assemble_export_bundle(
            export_id=str(export_id).lower(),
            profile_id=revision.profile_id,
            system_id=str(revision.system_id).lower(),
            package_revision_id=str(revision.package_revision_id).lower(),
            run_id=str(run.run_id).lower(),
            review_revision_id=str(export_draft.review_revision_id).lower(),
            approval_id=str(approval.approval_id).lower(),
            authority_manifest_id=authority_manifest_id,
            created_at=created_at,
            sealed_document=sealed.document,
            dispositions=dispositions,
            matrix_rows=matrix_rows,
            expected_payload_manifest_sha256=export_draft.payload_manifest_sha256,
        )
    except ExportAssemblyError as exc:
        raise ExportValidationError(exc.message, error_code=exc.error_code) from exc

    _write_export_zip(
        storage_root=storage_root,
        storage_key=bundle.storage_key,
        payload=bundle.zip_bytes,
    )
    export_record = ExportRecord(
        export_id=export_id,
        approval_id=approval.approval_id,
        profile_id=revision.profile_id,
        system_id=revision.system_id,
        package_revision_id=revision.package_revision_id,
        run_id=run.run_id,
        review_revision_id=export_draft.review_revision_id,
        payload_manifest_sha256=export_draft.payload_manifest_sha256,
        storage_key=bundle.storage_key,
        created_at=now,
    )
    session.add(export_record)
    export_draft.status = "exported"

    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="export.download",
        object_type="export",
        object_id=str(export_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "approval_id": str(approval.approval_id).lower(),
            "payload_manifest_sha256": export_draft.payload_manifest_sha256,
        },
        now=now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_DOWNLOAD,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=200,
        response_body={"export_id": str(export_id).lower()},
        response_headers={
            "Content-Disposition": f'attachment; filename="{_export_filename(export_id)}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
        now=now,
    )
    return ExportDownloadResult(
        zip_bytes=bundle.zip_bytes,
        filename=_export_filename(export_id),
        export_id=export_id,
        replayed=False,
        status=200,
    )


def _export_filename(export_id: uuid.UUID) -> str:
    return f"ato-export-{str(export_id).lower()}.zip"


def _write_export_zip(*, storage_root: Path, storage_key: str, payload: bytes) -> None:
    target = storage_root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".zip.part")
    temp.write_bytes(payload)
    temp.replace(target)


def _read_export_zip(*, storage_root: Path, storage_key: str) -> bytes:
    target = storage_root / storage_key
    if not target.is_file():
        raise ExportNotFoundError()
    return target.read_bytes()


async def approve_export(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    approval_id: uuid.UUID,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
    reason: str | None = None,
    project_root: Path | None = None,
    authority_manifest_id: str | None = None,
    approval_expiry_days: int = _DEFAULT_APPROVAL_EXPIRY_DAYS,
) -> ExportMutationResult:
    from ato_service.db.models import Approval, ExportDraft

    request_digest = _approval_payload_digest(
        approval_id=approval_id,
        decision="approved",
        reason=reason,
    )
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_APPROVE,
        idempotency_key,
        request_digest,
        now,
    )
    if isinstance(replay, IdempotencyReplay):
        return ExportMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            etag=replay.response_headers.get("ETag", '"v1"'),
            replayed=True,
        )

    approval_result = await session.execute(
        select(Approval).where(Approval.approval_id == approval_id)
    )
    approval = approval_result.scalar_one_or_none()
    if approval is None:
        raise ExportNotFoundError()
    if approval.submitted_by == principal.actor_id:
        raise SelfApprovalDeniedError()

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

    try:
        await _assert_approval_pending_and_fresh(
            session,
            approval=approval,
            export_draft=export_draft,
            approval_expiry_days=approval_expiry_days,
            hmac_key=hmac_key,
            actor_id=principal.actor_id,
            now=now,
        )
    except ApprovalAlreadyDecidedError:
        raise ExportValidationError(
            "approval is already decided",
            error_code="approval_already_decided",
        ) from None

    if project_root is not None and authority_manifest_id is not None and sealed is not None:
        await _verify_approval_payload_binding(
            session,
            approval=approval,
            export_draft=export_draft,
            review_revision_id=export_draft.review_revision_id,
            run_id=run.run_id,
            revision=revision,
            sealed=sealed,
            project_root=project_root,
            authority_manifest_id=authority_manifest_id,
            hmac_key=hmac_key,
            actor_id=principal.actor_id,
            now=now,
        )

    approval.decision = "approved"
    approval.decided_by = principal.actor_id
    approval.decided_at = now
    approval.expires_at = now + timedelta(days=approval_expiry_days)
    approval.reason = reason
    export_draft.status = "approved"
    payload = _map_approval_to_domain(approval)
    etag = '"v1"'
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="approval.approve",
        object_type="approval",
        object_id=str(approval_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "export_draft_id": str(approval.export_draft_id).lower(),
            "payload_manifest_sha256": approval.payload_manifest_sha256,
        },
        now=now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_APPROVE,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=200,
        response_body=payload,
        response_headers={"ETag": etag},
        now=now,
    )
    return ExportMutationResult(payload=payload, status=200, etag=etag, replayed=False)


async def reject_export(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    approval_id: uuid.UUID,
    reason: str,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
    project_root: Path | None = None,
    authority_manifest_id: str | None = None,
    approval_expiry_days: int = _DEFAULT_APPROVAL_EXPIRY_DAYS,
) -> ExportMutationResult:
    from ato_service.db.models import Approval, ExportDraft

    if not isinstance(reason, str) or not reason.strip():
        raise ExportValidationError("reason is required", error_code="request_schema_invalid")

    request_digest = _approval_payload_digest(
        approval_id=approval_id,
        decision="rejected",
        reason=reason.strip(),
    )
    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_REJECT,
        idempotency_key,
        request_digest,
        now,
    )
    if isinstance(replay, IdempotencyReplay):
        return ExportMutationResult(
            payload=replay.response_body,
            status=replay.response_status,
            etag=replay.response_headers.get("ETag", '"v1"'),
            replayed=True,
        )

    approval_result = await session.execute(
        select(Approval).where(Approval.approval_id == approval_id)
    )
    approval = approval_result.scalar_one_or_none()
    if approval is None:
        raise ExportNotFoundError()
    if approval.submitted_by == principal.actor_id:
        raise SelfApprovalDeniedError()

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

    try:
        await _assert_approval_pending_and_fresh(
            session,
            approval=approval,
            export_draft=export_draft,
            approval_expiry_days=approval_expiry_days,
            hmac_key=hmac_key,
            actor_id=principal.actor_id,
            now=now,
        )
    except ApprovalAlreadyDecidedError:
        raise ExportValidationError(
            "approval is already decided",
            error_code="approval_already_decided",
        ) from None

    if project_root is not None and authority_manifest_id is not None and sealed is not None:
        await _verify_approval_payload_binding(
            session,
            approval=approval,
            export_draft=export_draft,
            review_revision_id=export_draft.review_revision_id,
            run_id=run.run_id,
            revision=revision,
            sealed=sealed,
            project_root=project_root,
            authority_manifest_id=authority_manifest_id,
            hmac_key=hmac_key,
            actor_id=principal.actor_id,
            now=now,
        )

    approval.decision = "rejected"
    approval.decided_by = principal.actor_id
    approval.decided_at = now
    approval.reason = reason.strip()
    export_draft.status = "rejected"
    payload = _map_approval_to_domain(approval)
    etag = '"v1"'
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="approval.reject",
        object_type="approval",
        object_id=str(approval_id).lower(),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "export_draft_id": str(approval.export_draft_id).lower(),
            "payload_manifest_sha256": approval.payload_manifest_sha256,
            "reason": reason.strip(),
        },
        now=now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_REJECT,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=200,
        response_body=payload,
        response_headers={"ETag": etag},
        now=now,
    )
    return ExportMutationResult(payload=payload, status=200, etag=etag, replayed=False)


@dataclass(frozen=True, slots=True)
class ApprovalExpiryResult:
    pending_expired: int
    approved_expired: int


async def process_approval_expiry(
    session: AsyncSession,
    *,
    now: datetime,
    approval_expiry_days: int,
    hmac_key: bytes,
    actor_id: str = "ato-operator",
) -> ApprovalExpiryResult:
    from ato_service.db.models import Approval, ExportDraft, ExportRecord

    pending_expired = 0
    approved_expired = 0

    pending_result = await session.execute(
        select(Approval, ExportDraft)
        .join(ExportDraft, ExportDraft.export_draft_id == Approval.export_draft_id)
        .where(
            Approval.decision == "pending",
            ExportDraft.status == "pending_approval",
        )
    )
    for approval, export_draft in pending_result.all():
        deadline = approval.submitted_at + timedelta(days=approval_expiry_days)
        if now < deadline:
            continue
        export_draft.status = "expired"
        pending_expired += 1
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="service",
            actor_id=actor_id,
            action="approval.expired",
            object_type="approval",
            object_id=str(approval.approval_id).lower(),
            outcome="succeeded",
            reason_code="approval_expired",
            metadata={"export_draft_id": str(export_draft.export_draft_id).lower()},
            now=now,
        )

    approved_result = await session.execute(
        select(Approval, ExportDraft)
        .join(ExportDraft, ExportDraft.export_draft_id == Approval.export_draft_id)
        .where(
            Approval.decision == "approved",
            ExportDraft.status == "approved",
        )
    )
    for approval, export_draft in approved_result.all():
        if approval.decided_at is None:
            continue
        record_result = await session.execute(
            select(ExportRecord).where(ExportRecord.approval_id == approval.approval_id)
        )
        if record_result.scalar_one_or_none() is not None:
            continue
        deadline = approval.decided_at + timedelta(days=approval_expiry_days)
        if now < deadline:
            continue
        export_draft.status = "expired"
        approved_expired += 1
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="service",
            actor_id=actor_id,
            action="export_draft.expired",
            object_type="export_draft",
            object_id=str(export_draft.export_draft_id).lower(),
            outcome="succeeded",
            reason_code="export_expired",
            metadata={"approval_id": str(approval.approval_id).lower()},
            now=now,
        )

    return ApprovalExpiryResult(
        pending_expired=pending_expired,
        approved_expired=approved_expired,
    )
