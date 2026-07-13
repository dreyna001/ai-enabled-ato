"""P1.1 Systems and PackageRevision HTTP routes."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.api_dependencies import (
    get_audit_hmac_key,
    get_blob_store,
    get_db_session,
    get_runtime_state,
)
from ato_service.analysis_runs import (
    AnalysisRunNotFoundError,
    AnalysisRunPolicyError,
    AnalysisRunValidationError,
    StartRunInput,
    cancel_run,
    get_run,
    get_run_matrix,
    list_runs,
    start_run,
)
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    require_authenticated_principal,
    require_mutation_context,
)
from ato_service.blobs import BlobStore
from ato_service.concurrency import format_package_revision_etag
from ato_service.domain_mapping import map_system_to_domain
from ato_service.fact_proposals import (
    accept_fact_proposal,
    list_fact_proposals,
    reject_fact_proposal,
)
from ato_service.package_revisions import (
    CreatePackageRevisionInput,
    PackageRevisionMutationResult,
    confirm_package_revision,
    create_package_revision,
    finalize_package_revision,
    get_package_revision,
    list_package_revisions,
)
from ato_service.source_artifacts import UploadSourceArtifactResult, upload_source_artifact
from ato_service.systems import create_system, get_system, list_systems

ProfileId = Literal[
    "fedramp_20x_program",
    "fedramp_rev5_transition",
    "fisma_agency_security",
]
RunType = Literal["full", "targeted", "deterministic_only"]
DataOrigin = Literal[
    "synthetic",
    "redacted_nonproduction",
    "customer_production",
]
Sensitivity = Literal[
    "public",
    "internal_unclassified",
    "customer_sensitive",
    "cui",
    "classified",
    "unknown",
]
ViewerGroupId = Annotated[str, Field(min_length=1, max_length=255)]


class CreateSystemRequest(BaseModel):
    """OpenAPI-aligned create-system payload."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=255)
    external_system_id: str | None = Field(max_length=255)
    owner_group: str = Field(min_length=1, max_length=255)
    viewer_groups: list[ViewerGroupId] = Field(max_length=100)

    @field_validator("viewer_groups")
    @classmethod
    def validate_unique_viewer_groups(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate group id")
        return value


class CreatePackageRevisionRequest(BaseModel):
    """OpenAPI-aligned create-package-revision payload."""

    model_config = ConfigDict(extra="forbid")

    parent_revision_id: uuid.UUID | None
    profile_id: ProfileId
    certification_class: Literal["B", "C"] | None
    impact_level: Literal["low", "moderate", "high"] | None
    data_origin: DataOrigin
    sensitivity: Sensitivity


class PaginatedSystemsResponse(BaseModel):
    """OpenAPI-aligned systems list envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)


class PaginatedFactProposalsResponse(BaseModel):
    """OpenAPI-aligned fact proposal list envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)


class AcceptProposalRequest(BaseModel):
    """OpenAPI-aligned accept/edit proposal payload."""

    model_config = ConfigDict(extra="forbid")

    edited_value: Any | None


class RejectProposalRequest(BaseModel):
    """OpenAPI-aligned reject proposal payload."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class PaginatedPackageRevisionsResponse(BaseModel):
    """OpenAPI-aligned package revision list envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)


class StartRunRequest(BaseModel):
    """OpenAPI-aligned start-run payload."""

    model_config = ConfigDict(extra="forbid")

    run_type: RunType
    parent_run_id: uuid.UUID | None
    assessment_item_ids: list[str] = Field(max_length=500)

    @field_validator("assessment_item_ids")
    @classmethod
    def validate_unique_assessment_item_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate assessment item id")
        return value


class PaginatedRunsResponse(BaseModel):
    """OpenAPI-aligned analysis run list envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)


class PaginatedMatrixRowsResponse(BaseModel):
    """OpenAPI-aligned matrix row list envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    total: int = Field(ge=0)


IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{16,128}$",
    ),
]


def get_read_principal(request: Request) -> AuthenticatedPrincipal:
    """Return the injected authenticated principal for read routes."""
    return require_authenticated_principal(request)


def get_mutation_principal(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    origin: Annotated[str | None, Header()] = None,
) -> AuthenticatedPrincipal:
    """Validate CSRF and Origin for mutating package routes."""
    return require_mutation_context(request, x_csrf_token, origin)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _package_revision_json_response(result: PackageRevisionMutationResult) -> JSONResponse:
    return JSONResponse(
        status_code=result.status,
        content=result.payload,
        headers={"ETag": result.etag},
    )


def _upload_json_response(result: UploadSourceArtifactResult) -> JSONResponse:
    return JSONResponse(
        status_code=result.status,
        content=result.payload,
        headers={"ETag": result.etag},
    )


def create_api_router() -> APIRouter:
    """Build the P1.1 Systems and PackageRevision route table."""
    router = APIRouter()

    @router.post("/systems", status_code=201, tags=["Systems"])
    async def post_systems(
        body: CreateSystemRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await create_system(
            session,
            principal=principal,
            audit_hmac_key=audit_hmac_key,
            idempotency_key=idempotency_key,
            display_name=body.display_name,
            external_system_id=body.external_system_id,
            owner_group=body.owner_group,
            viewer_groups=body.viewer_groups,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload)

    @router.get("/systems", tags=["Systems"])
    async def get_systems(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        cursor: str | None = None,
        limit: int | None = None,
    ) -> PaginatedSystemsResponse:
        page = await list_systems(
            session,
            principal=principal,
            cursor=cursor,
            limit=limit,
        )
        return PaginatedSystemsResponse(
            items=page.items,
            next_cursor=page.next_cursor,
        )

    @router.get("/systems/{system_id}", tags=["Systems"])
    async def get_system_by_id(
        system_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        system = await get_system(
            session,
            principal=principal,
            system_id=system_id,
        )
        return map_system_to_domain(system)

    @router.post(
        "/systems/{system_id}/package-revisions",
        status_code=201,
        tags=["Packages"],
    )
    async def post_package_revisions(
        system_id: uuid.UUID,
        body: CreatePackageRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await create_package_revision(
            session,
            principal=principal,
            system_id=system_id,
            request=CreatePackageRevisionInput(
                parent_revision_id=body.parent_revision_id,
                profile_id=body.profile_id,
                certification_class=body.certification_class,
                impact_level=body.impact_level,
                data_origin=body.data_origin,
                sensitivity=body.sensitivity,
            ),
            authority_manifest_id=runtime_state.authority_manifest_id,
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return _package_revision_json_response(result)

    @router.get(
        "/systems/{system_id}/package-revisions",
        tags=["Packages"],
    )
    async def get_package_revisions(
        system_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        cursor: str | None = None,
        limit: int | None = None,
    ) -> PaginatedPackageRevisionsResponse:
        page = await list_package_revisions(
            session,
            principal=principal,
            system_id=system_id,
            cursor=cursor,
            limit=limit,
        )
        return PaginatedPackageRevisionsResponse(
            items=list(page.items),
            next_cursor=page.next_cursor,
        )

    @router.get("/package-revisions/{id}", tags=["Packages"])
    async def get_package_revision_by_id(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> JSONResponse:
        payload = await get_package_revision(
            session,
            principal=principal,
            package_revision_id=id,
        )
        etag = format_package_revision_etag(payload["revision_version"])
        return JSONResponse(content=payload, headers={"ETag": etag})

    @router.post(
        "/package-revisions/{id}/files",
        status_code=201,
        tags=["Packages"],
    )
    async def post_package_revision_files(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        blob_store: Annotated[BlobStore, Depends(get_blob_store)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
        file: Annotated[UploadFile, File()],
        artifact_kind: Annotated[str, Form()],
        source_date: Annotated[date | None, Form()] = None,
    ) -> JSONResponse:
        declared_media_type = file.content_type or ""
        display_filename = file.filename or ""
        try:
            result = await upload_source_artifact(
                session,
                principal=principal,
                audit_hmac_key=audit_hmac_key,
                blob_store=blob_store,
                limits=runtime_state.config.limits,
                package_revision_id=id,
                idempotency_key=idempotency_key,
                source=file.file,
                display_filename=display_filename,
                declared_media_type=declared_media_type,
                artifact_kind=artifact_kind,
                source_date=source_date,
                now=_utc_now(),
            )
        finally:
            await file.close()
        return _upload_json_response(result)

    @router.post(
        "/package-revisions/{id}/finalize",
        status_code=202,
        tags=["Packages"],
    )
    async def post_package_revision_finalize(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await finalize_package_revision(
            session,
            principal=principal,
            package_revision_id=id,
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            storage_root=runtime_state.storage_root,
            project_root=runtime_state.snapshot.project_root,
            limits=runtime_state.config.limits,
            now=_utc_now(),
        )
        return _package_revision_json_response(result)

    @router.post(
        "/package-revisions/{id}/confirm",
        tags=["Packages"],
    )
    async def post_package_revision_confirm(
        id: uuid.UUID,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        if_match = request.headers.get("if-match")
        result = await confirm_package_revision(
            session,
            principal=principal,
            package_revision_id=id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return _package_revision_json_response(result)

    @router.get("/package-revisions/{id}/proposals", tags=["Packages"])
    async def get_package_revision_proposals(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        cursor: str | None = None,
        limit: int | None = None,
    ) -> PaginatedFactProposalsResponse:
        page = await list_fact_proposals(
            session,
            principal=principal,
            package_revision_id=id,
            cursor=cursor,
            limit=limit,
        )
        return PaginatedFactProposalsResponse(
            items=page.items,
            next_cursor=page.next_cursor,
        )

    @router.post("/proposals/{id}/accept", tags=["Packages"])
    async def post_proposal_accept(
        id: uuid.UUID,
        body: AcceptProposalRequest,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> JSONResponse:
        if_match = request.headers.get("if-match")
        result = await accept_fact_proposal(
            session,
            principal=principal,
            fact_proposal_id=id,
            if_match=if_match,
            edited_value=body.edited_value,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(
            content=result.payload,
            headers={"ETag": result.etag},
        )

    @router.post("/proposals/{id}/reject", tags=["Packages"])
    async def post_proposal_reject(
        id: uuid.UUID,
        body: RejectProposalRequest,
        request: Request,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> JSONResponse:
        if_match = request.headers.get("if-match")
        result = await reject_fact_proposal(
            session,
            principal=principal,
            fact_proposal_id=id,
            if_match=if_match,
            reason=body.reason,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(
            content=result.payload,
            headers={"ETag": result.etag},
        )

    @router.post(
        "/package-revisions/{id}/runs",
        status_code=202,
        tags=["Runs"],
    )
    async def post_package_revision_runs(
        id: uuid.UUID,
        body: StartRunRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        result = await start_run(
            session,
            principal=principal,
            package_revision_id=id,
            request=StartRunInput(
                run_type=body.run_type,
                parent_run_id=body.parent_run_id,
                assessment_item_ids=tuple(body.assessment_item_ids),
            ),
            config=runtime_state.config,
            authority_manifest_id=runtime_state.authority_manifest_id,
            project_root=runtime_state.snapshot.project_root,
            idempotency_key=idempotency_key,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload)

    @router.get("/package-revisions/{id}/runs", tags=["Runs"])
    async def get_package_revision_runs(
        id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        cursor: str | None = None,
        limit: int | None = None,
    ) -> PaginatedRunsResponse:
        page = await list_runs(
            session,
            principal=principal,
            package_revision_id=id,
            cursor=cursor,
            limit=limit,
        )
        return PaginatedRunsResponse(items=page.items, next_cursor=page.next_cursor)

    @router.get("/runs/{run_id}", tags=["Runs"])
    async def get_run_by_id(
        run_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        return await get_run(
            session,
            principal=principal,
            run_id=run_id,
        )

    @router.post("/runs/{run_id}/cancel", status_code=202, tags=["Runs"])
    async def post_run_cancel(
        run_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> JSONResponse:
        result = await cancel_run(
            session,
            principal=principal,
            run_id=run_id,
            hmac_key=audit_hmac_key,
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload)

    @router.get("/runs/{run_id}/matrix", tags=["Runs"])
    async def get_run_matrix_rows(
        run_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        cursor: str | None = None,
        limit: int | None = None,
        status: (
            Literal[
                "supported",
                "partial",
                "unsupported",
                "insufficient_evidence",
            ]
            | None
        ) = None,
    ) -> PaginatedMatrixRowsResponse:
        page = await get_run_matrix(
            session,
            principal=principal,
            run_id=run_id,
            cursor=cursor,
            limit=limit,
            status=status,
        )
        return PaginatedMatrixRowsResponse(
            items=page.items,
            next_cursor=page.next_cursor,
            total=page.total,
        )

    return router
