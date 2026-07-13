"""Review, export, preflight, search, chat, and ConMon-lite HTTP routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.api_dependencies import get_audit_hmac_key, get_db_session, get_runtime_state
from ato_service.api_router import get_mutation_principal, get_read_principal
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.authorization_decisions import (
    AttachAuthorizationDecisionInput,
    attach_authorization_decision,
    list_authorization_decisions,
)
from ato_service.change_analysis import build_change_analysis
from ato_service.export_service import (
    SelfApprovalDeniedError,
    approve_export,
    create_export_draft,
    submit_export_draft,
)
from ato_service.package_chat import chat_with_package
from ato_service.package_search import search_revision_content
from ato_service.preflight import PreflightContext, evaluate_preflight
from ato_service.review_revisions import (
    create_review_revision,
    submit_review_revision,
    update_disposition,
)
from ato_service.revision_delta import compute_revision_delta

IdempotencyKeyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


class AttachAuthorizationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_type: str = Field(min_length=1, max_length=64)
    decision_date: str = Field(min_length=1, max_length=32)
    issuing_authority: str = Field(min_length=1, max_length=255)
    artifact_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class DispositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    edited_summary: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    review_revision_id: uuid.UUID | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_extended_router() -> APIRouter:
    router = APIRouter()

    @router.get("/package-revisions/{id}/preflight", tags=["Packages"])
    async def get_package_revision_preflight(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
    ) -> dict[str, Any]:
        from ato_service.db.models import PackageRevision, SealedPackageContent, System

        revision_result = await session.execute(
            select(PackageRevision).where(PackageRevision.package_revision_id == id)
        )
        revision = revision_result.scalar_one_or_none()
        if revision is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        system_result = await session.execute(
            select(System).where(System.system_id == revision.system_id)
        )
        system = system_result.scalar_one_or_none()
        if system is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        sealed_result = await session.execute(
            select(SealedPackageContent).where(
                SealedPackageContent.package_revision_id == id
            )
        )
        sealed = sealed_result.scalar_one_or_none()
        return evaluate_preflight(
            PreflightContext(
                package_revision_id=id,
                profile_id=revision.profile_id,
                status=revision.status,
                sealed_document=sealed.document if sealed is not None else None,
                authority_manifest_id=revision.authority_manifest_id,
                authority_manifest_sha256=revision.content_manifest_sha256 or ("0" * 64),
                project_root=runtime_state.snapshot.project_root,
                evaluated_at=_utc_now(),
            )
        )

    @router.get("/package-revisions/{id}/delta", tags=["Packages"])
    async def get_package_revision_delta(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        from ato_service.db.models import PackageRevision, SealedPackageContent, SourceArtifact

        child_result = await session.execute(
            select(PackageRevision).where(PackageRevision.package_revision_id == id)
        )
        child = child_result.scalar_one_or_none()
        if child is None or child.parent_revision_id is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        parent_result = await session.execute(
            select(PackageRevision).where(
                PackageRevision.package_revision_id == child.parent_revision_id
            )
        )
        parent = parent_result.scalar_one_or_none()
        if parent is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        parent_artifacts = (
            await session.execute(
                select(SourceArtifact).where(
                    SourceArtifact.package_revision_id == parent.package_revision_id
                )
            )
        ).scalars().all()
        child_artifacts = (
            await session.execute(
                select(SourceArtifact).where(
                    SourceArtifact.package_revision_id == child.package_revision_id
                )
            )
        ).scalars().all()
        parent_sealed = (
            await session.execute(
                select(SealedPackageContent).where(
                    SealedPackageContent.package_revision_id == parent.package_revision_id
                )
            )
        ).scalar_one_or_none()
        child_sealed = (
            await session.execute(
                select(SealedPackageContent).where(
                    SealedPackageContent.package_revision_id == child.package_revision_id
                )
            )
        ).scalar_one_or_none()
        return build_change_analysis(
            parent_revision_id=parent.package_revision_id,
            child_revision_id=child.package_revision_id,
            parent_artifacts=list(parent_artifacts),
            child_artifacts=list(child_artifacts),
            parent_document=parent_sealed.document if parent_sealed else None,
            child_document=child_sealed.document if child_sealed else None,
            parent_content_sha256=parent.package_content_sha256,
            child_content_sha256=child.package_content_sha256,
            now=_utc_now(),
        )

    @router.get("/package-revisions/{id}/search", tags=["Packages"])
    async def search_package_revision(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        q: str = "",
        limit: int = 25,
    ) -> dict[str, Any]:
        from ato_service.db.models import PackageRevision, SealedPackageContent, SourceArtifact

        revision_result = await session.execute(
            select(PackageRevision).where(PackageRevision.package_revision_id == id)
        )
        revision = revision_result.scalar_one_or_none()
        if revision is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        sealed = (
            await session.execute(
                select(SealedPackageContent).where(
                    SealedPackageContent.package_revision_id == id
                )
            )
        ).scalar_one_or_none()
        artifacts = (
            await session.execute(
                select(SourceArtifact).where(SourceArtifact.package_revision_id == id)
            )
        ).scalars().all()
        return search_revision_content(
            query=q,
            sealed_document=sealed.document if sealed is not None else None,
            artifacts=artifacts,
            limit=limit,
        )

    @router.post("/package-revisions/{id}/chat", tags=["Packages"])
    async def chat_package_revision(
        id: uuid.UUID,
        body: ChatRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        from ato_service.db.models import PackageRevision, SealedPackageContent, SourceArtifact

        revision_result = await session.execute(
            select(PackageRevision).where(PackageRevision.package_revision_id == id)
        )
        revision = revision_result.scalar_one_or_none()
        if revision is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        sealed = (
            await session.execute(
                select(SealedPackageContent).where(
                    SealedPackageContent.package_revision_id == id
                )
            )
        ).scalar_one_or_none()
        artifacts = (
            await session.execute(
                select(SourceArtifact).where(SourceArtifact.package_revision_id == id)
            )
        ).scalars().all()
        search_hits = search_revision_content(
            query=body.question,
            sealed_document=sealed.document if sealed is not None else None,
            artifacts=artifacts,
            limit=5,
        )["items"]
        return chat_with_package(
            question=body.question,
            sealed_document=sealed.document if sealed is not None else None,
            search_hits=search_hits,
        )

    @router.post("/systems/{system_id}/authorization-decisions", status_code=201, tags=["Packages"])
    async def post_authorization_decision(
        system_id: uuid.UUID,
        body: AttachAuthorizationDecisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
        package_revision_id: uuid.UUID | None = None,
    ) -> JSONResponse:
        result = await attach_authorization_decision(
            session,
            principal=principal,
            system_id=system_id,
            package_revision_id=package_revision_id,
            request=AttachAuthorizationDecisionInput(
                decision_type=body.decision_type,
                decision_date=body.decision_date,
                issuing_authority=body.issuing_authority,
                artifact_id=body.artifact_id,
                notes=body.notes,
            ),
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload)

    @router.get("/systems/{system_id}/authorization-decisions", tags=["Packages"])
    async def get_authorization_decisions(
        system_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        items = await list_authorization_decisions(
            session,
            principal=principal,
            system_id=system_id,
        )
        return {"items": list(items)}

    @router.post("/runs/{run_id}/review-revisions", status_code=201, tags=["Reviews"])
    async def post_review_revision(
        run_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await create_review_revision(
            session,
            principal=principal,
            run_id=run_id,
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload, headers={"ETag": result.etag})

    @router.post("/review-revisions/{id}/submit", tags=["Reviews"])
    async def post_review_revision_submit(
        id: uuid.UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await submit_review_revision(
            session,
            principal=principal,
            review_revision_id=id,
            if_match=request.headers.get("if-match"),
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload, headers={"ETag": result.etag})

    @router.patch("/review-revisions/{id}/dispositions/{row_id}", tags=["Reviews"])
    async def patch_disposition(
        id: uuid.UUID,
        row_id: uuid.UUID,
        body: DispositionRequest,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> JSONResponse:
        payload, etag = await update_disposition(
            session,
            principal=principal,
            review_revision_id=id,
            matrix_row_id=row_id,
            decision=body.decision,
            edited_summary=body.edited_summary,
            notes=body.notes,
            if_match=request.headers.get("if-match"),
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(content=payload, headers={"ETag": etag})

    @router.post("/review-revisions/{id}/export-drafts", status_code=201, tags=["Exports"])
    async def post_export_draft(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await create_export_draft(
            session,
            principal=principal,
            review_revision_id=id,
            project_root=runtime_state.snapshot.project_root,
            authority_manifest_id=runtime_state.authority_manifest_id,
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload, headers={"ETag": result.etag})

    @router.post("/export-drafts/{id}/submit", status_code=201, tags=["Exports"])
    async def post_export_draft_submit(
        id: uuid.UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await submit_export_draft(
            session,
            principal=principal,
            export_draft_id=id,
            if_match=request.headers.get("if-match"),
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload, headers={"ETag": result.etag})

    @router.post("/approvals/{id}/approve", tags=["Exports"])
    async def post_approval_approve(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        try:
            result = await approve_export(
                session,
                principal=principal,
                approval_id=id,
                idempotency_key=idempotency_key,
                hmac_key=audit_hmac_key,
                now=_utc_now(),
            )
        except SelfApprovalDeniedError:
            return JSONResponse(status_code=403, content={"error": "self_approval_denied"})
        return JSONResponse(status_code=result.status, content=result.payload, headers={"ETag": result.etag})

    return router
