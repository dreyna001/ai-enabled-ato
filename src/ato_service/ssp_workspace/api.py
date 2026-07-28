"""FastAPI routes for the bounded internal SSP workflow."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Any
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.api_dependencies import (
    get_audit_hmac_key,
    get_blob_store,
    get_db_session,
    get_runtime_state,
)
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    require_authenticated_principal,
    require_mutation_context,
)
from ato_service.blobs import BlobStore, BlobStoreError
from ato_service.package_rbac import principal_has_role, require_any_package_role
from ato_service.ssp_workspace.evidence import (
    EvidenceRemovalError,
    EvidenceUploadError,
    ingest_workspace_evidence,
    remove_workspace_evidence,
)
from ato_service.ssp_workspace.generation import ModelPrompt, SspGenerationError
from ato_service.ssp_workspace.persistence import WorkspacePersistenceError
from ato_service.ssp_workspace.profile_bundles import ProfileBundleError
from ato_service.ssp_workspace.profiles import (
    ProfilePersistenceError,
    activate_profile,
    import_profile,
    list_profiles,
    parse_profile_archive,
)
from ato_service.ssp_workspace.service import (
    AgentPatchNotFoundError,
    AgentPatchStateError,
    ApprovalNotFoundError,
    WorkspaceNotReviewableError,
    apply_proposed_patch,
    approve_workspace_revision,
    create_initialized_workspace,
    generate_workspace_draft,
    list_workspace_rows,
    load_workspace_envelope,
    migrate_workspace_profile,
    propose_agent_patch,
    reject_proposed_patch,
    render_approved_export,
    restore_workspace_revision,
    save_system_categorization,
    save_control_edit,
    save_question_answer,
    save_section_edit,
)
from ato_service.text_llm import (
    ChatMessage,
    TextModelCallError,
    TextModelConfigurationError,
    build_text_model_client,
)
from ato_service.systems import create_system, list_systems


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_id: uuid.UUID
    profile_version_id: uuid.UUID


class CreateSspSystemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=255)


IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{16,128}$",
    ),
]


class ExpectedRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision_id: uuid.UUID


class EditSectionRequest(ExpectedRevisionRequest):
    content: str = Field(max_length=100_000)


class EditControlRequest(ExpectedRevisionRequest):
    implementation_statement: str = Field(max_length=100_000)
    implementation_status: str | None = Field(default=None, max_length=64)
    responsibility: str | None = Field(default=None, max_length=64)


class AnswerQuestionRequest(ExpectedRevisionRequest):
    answer: str = Field(min_length=1, max_length=20_000)


class SaveCategorizationRequest(ExpectedRevisionRequest):
    confidentiality: str = Field(pattern=r"^(low|moderate|high)$")
    integrity: str = Field(pattern=r"^(low|moderate|high)$")
    availability: str = Field(pattern=r"^(low|moderate|high)$")
    confidentiality_rationale: str = Field(min_length=1, max_length=4_000)
    integrity_rationale: str = Field(min_length=1, max_length=4_000)
    availability_rationale: str = Field(min_length=1, max_length=4_000)


class ProposePatchRequest(ExpectedRevisionRequest):
    instruction: str = Field(min_length=1, max_length=20_000)


class MigrateProfileRequest(ExpectedRevisionRequest):
    profile_version_id: uuid.UUID
    impact_level: str = Field(pattern=r"^(low|moderate|high)$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_read_principal(request: Request) -> AuthenticatedPrincipal:
    return require_authenticated_principal(request)


def get_mutation_principal(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    origin: Annotated[str | None, Header()] = None,
) -> AuthenticatedPrincipal:
    return require_mutation_context(request, x_csrf_token, origin)


def _error_response(exc: Exception) -> JSONResponse:
    code = getattr(exc, "error_code", "validation_failed")
    if isinstance(exc, AuthorizationDeniedError):
        return JSONResponse(
            status_code=403,
            content={"error": "authorization_denied", "error_code": "authorization_denied"},
        )
    if code in {"resource_not_found", "approval_not_found"}:
        status = 404
    elif code in {
        "revision_stale",
        "illegal_state_transition",
        "profile_already_imported",
    }:
        status = 409
    elif isinstance(exc, (TextModelConfigurationError,)):
        status = 503
        code = "model_not_configured"
    elif isinstance(exc, (TextModelCallError, SspGenerationError)):
        status = 502
        code = "model_generation_failed"
    else:
        status = 422
    return JSONResponse(status_code=status, content={"error": code, "error_code": code})


async def _authorize_workspace(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    workspace_id: uuid.UUID,
    roles: tuple[str, ...],
) -> Any:
    from ato_service.db.models import SspWorkspace, System

    row = (
        await session.execute(
            select(SspWorkspace, System)
            .join(System, System.system_id == SspWorkspace.system_id)
            .where(SspWorkspace.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        from ato_service.ssp_workspace.persistence import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("workspace not found")
    _, system = row
    require_any_package_role(principal, system=system, roles=roles)
    return system


def _require_platform_admin(principal: AuthenticatedPrincipal) -> None:
    if not principal_has_role(principal, "platform_admin"):
        raise AuthorizationDeniedError()


def _model_adapter(runtime_state: Any) -> Any:
    client = build_text_model_client(runtime_state.config)

    async def invoke(prompt: ModelPrompt) -> str:
        import asyncio

        return await asyncio.to_thread(
            client.complete,
            [ChatMessage(role="user", content=prompt.user)],
            system=prompt.system,
        )

    return invoke


def build_ssp_workspace_router() -> APIRouter:
    router = APIRouter(tags=["SSP Workspaces"])

    @router.get("/ssp-systems")
    async def get_ssp_systems(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        page = await list_systems(
            session,
            principal=principal,
            cursor=None,
            limit=100,
            include_archived=False,
        )
        return {"items": page.items}

    @router.post("/ssp-systems", status_code=201)
    async def post_ssp_system(
        body: CreateSspSystemRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        owner_group = principal.groups[0]
        result = await create_system(
            session,
            principal=principal,
            audit_hmac_key=audit_hmac_key,
            idempotency_key=idempotency_key,
            display_name=body.display_name,
            external_system_id=None,
            owner_group=owner_group,
            viewer_groups=[],
            customer_enterprise_id=(
                runtime_state.config.installation_customer_enterprise_id
            ),
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload)

    @router.get("/ssp-profiles")
    async def get_profiles(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        del principal
        rows = await list_profiles(session)
        return {
            "items": [
                {
                    "profile_version_id": str(item.profile_version_id),
                    "profile_id": item.profile_key,
                    "version": item.version,
                    "status": item.status,
                    "bundle_sha256": item.bundle_sha256,
                    "imported_by": item.imported_by,
                    "imported_at": item.imported_at.isoformat(),
                    "activated_at": (
                        item.activated_at.isoformat() if item.activated_at else None
                    ),
                    "display_name": item.bundle["manifest"]["display_name"],
                }
                for item in rows
            ]
        }

    @router.post("/ssp-profiles/import")
    async def post_profile_import(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        bundle: Annotated[UploadFile, File()],
    ) -> Response:
        try:
            _require_platform_admin(principal)
            content = await bundle.read(52_428_801)
            row = await import_profile(
                session,
                bundle=parse_profile_archive(content),
                imported_by=principal.actor_id,
                now=_utc_now(),
            )
            return JSONResponse(
                status_code=201,
                content={"profile_version_id": str(row.profile_version_id), "status": row.status},
            )
        except (
            AuthorizationDeniedError,
            ProfileBundleError,
            ProfilePersistenceError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-profiles/{profile_version_id}/activate")
    async def post_profile_activate(
        profile_version_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> Response:
        try:
            _require_platform_admin(principal)
            row = await activate_profile(
                session,
                profile_version_id=profile_version_id,
                now=_utc_now(),
            )
            return JSONResponse(
                status_code=200,
                content={"profile_version_id": str(row.profile_version_id), "status": row.status},
            )
        except (AuthorizationDeniedError, ProfilePersistenceError) as exc:
            return _error_response(exc)

    @router.get("/ssp-workspaces")
    async def get_workspaces(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for row in await list_workspace_rows(session):
            try:
                await _authorize_workspace(
                    session,
                    principal=principal,
                    workspace_id=row.workspace_id,
                    roles=("viewer",),
                )
            except AuthorizationDeniedError:
                continue
            items.append(await load_workspace_envelope(session, workspace_id=row.workspace_id))
        return {"items": items}

    @router.post("/ssp-workspaces")
    async def post_workspace(
        payload: CreateWorkspaceRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        from ato_service.db.models import System

        try:
            system = (
                await session.execute(
                    select(System).where(System.system_id == payload.system_id)
                )
            ).scalar_one_or_none()
            if system is None:
                from ato_service.ssp_workspace.persistence import WorkspaceNotFoundError

                raise WorkspaceNotFoundError("system not found")
            require_any_package_role(
                principal,
                system=system,
                roles=("system_owner", "isso"),
            )
            workspace = await create_initialized_workspace(
                session,
                system_id=payload.system_id,
                profile_version_id=payload.profile_version_id,
                impact_level=None,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            envelope = await load_workspace_envelope(
                session, workspace_id=workspace.workspace_id
            )
            return JSONResponse(status_code=201, content=envelope)
        except (AuthorizationDeniedError, WorkspacePersistenceError, ProfileBundleError) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/categorization")
    async def post_categorization(
        workspace_id: uuid.UUID,
        payload: SaveCategorizationRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("isso",),
            )
            await save_system_categorization(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                confidentiality=payload.confidentiality,
                integrity=payload.integrity,
                availability=payload.availability,
                confidentiality_rationale=payload.confidentiality_rationale,
                integrity_rationale=payload.integrity_rationale,
                availability_rationale=payload.availability_rationale,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            ValueError,
        ) as exc:
            return _error_response(exc)

    @router.get("/ssp-workspaces/{workspace_id}")
    async def get_workspace(
        workspace_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            WorkspaceNotReviewableError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/evidence")
    async def post_evidence(
        workspace_id: uuid.UUID,
        expected_revision_id: Annotated[uuid.UUID, Form()],
        file: Annotated[UploadFile, File()],
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        blob_store: Annotated[BlobStore, Depends(get_blob_store)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("system_owner", "isso"),
            )
            content = await file.read(runtime_state.config.limits.max_single_file_bytes + 1)
            await ingest_workspace_evidence(
                session,
                workspace_id=workspace_id,
                expected_revision_id=expected_revision_id,
                filename=file.filename or "evidence",
                media_type=file.content_type or "application/octet-stream",
                content=content,
                actor_id=principal.actor_id,
                now=_utc_now(),
                blob_store=blob_store,
                config=runtime_state.config,
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=201,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            EvidenceUploadError,
            BlobStoreError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/generate")
    async def post_generate(
        workspace_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await generate_workspace_draft(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                model=_model_adapter(runtime_state),
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            TextModelConfigurationError,
            TextModelCallError,
            SspGenerationError,
        ) as exc:
            return _error_response(exc)

    @router.delete(
        "/ssp-workspaces/{workspace_id}/evidence/{evidence_artifact_id}"
    )
    async def delete_evidence(
        workspace_id: uuid.UUID,
        evidence_artifact_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[
            AuthenticatedPrincipal, Depends(get_mutation_principal)
        ],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("system_owner", "isso"),
            )
            await remove_workspace_evidence(
                session,
                workspace_id=workspace_id,
                evidence_artifact_id=evidence_artifact_id,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            EvidenceRemovalError,
        ) as exc:
            return _error_response(exc)

    @router.patch("/ssp-workspaces/{workspace_id}/sections/{section_key}")
    async def patch_section(
        workspace_id: uuid.UUID,
        section_key: str,
        payload: EditSectionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        return await _edit_route(
            session=session,
            principal=principal,
            workspace_id=workspace_id,
            audit_hmac_key=audit_hmac_key,
            operation=lambda: save_section_edit(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                section_key=section_key,
                content=payload.content,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            ),
        )

    @router.patch("/ssp-workspaces/{workspace_id}/controls/{control_id}")
    async def patch_control(
        workspace_id: uuid.UUID,
        control_id: str,
        payload: EditControlRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        return await _edit_route(
            session=session,
            principal=principal,
            workspace_id=workspace_id,
            audit_hmac_key=audit_hmac_key,
            operation=lambda: save_control_edit(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                control_id=control_id,
                implementation_statement=payload.implementation_statement,
                implementation_status=payload.implementation_status,
                responsibility=payload.responsibility,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            ),
        )

    @router.post("/ssp-workspaces/{workspace_id}/questions/{question_id}/answer")
    async def post_question_answer(
        workspace_id: uuid.UUID,
        question_id: uuid.UUID,
        payload: AnswerQuestionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        return await _edit_route(
            session=session,
            principal=principal,
            workspace_id=workspace_id,
            audit_hmac_key=audit_hmac_key,
            operation=lambda: save_question_answer(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                question_id=question_id,
                answer=payload.answer,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            ),
        )

    @router.post("/ssp-workspaces/{workspace_id}/agent/patches")
    async def post_agent_patch(
        workspace_id: uuid.UUID,
        payload: ProposePatchRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            patch = await propose_agent_patch(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                instruction=payload.instruction,
                model=_model_adapter(runtime_state),
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=201,
                content={
                    "patch_id": str(patch.patch_id),
                    "status": patch.status,
                    "summary": patch.summary,
                    "operations": patch.operations,
                },
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            TextModelConfigurationError,
            TextModelCallError,
            SspGenerationError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/agent/patches/{patch_id}/apply")
    async def post_apply_patch(
        workspace_id: uuid.UUID,
        patch_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await apply_proposed_patch(
                session,
                workspace_id=workspace_id,
                patch_id=patch_id,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            AgentPatchNotFoundError,
            AgentPatchStateError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/agent/patches/{patch_id}/reject")
    async def post_reject_patch(
        workspace_id: uuid.UUID,
        patch_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await reject_proposed_patch(
                session,
                workspace_id=workspace_id,
                patch_id=patch_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            AgentPatchNotFoundError,
            AgentPatchStateError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/approve")
    async def post_approve(
        workspace_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await approve_workspace_revision(
                session,
                workspace_id=workspace_id,
                revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            WorkspaceNotReviewableError,
        ) as exc:
            return _error_response(exc)

    @router.post(
        "/ssp-workspaces/{workspace_id}/revisions/{revision_id}/restore"
    )
    async def post_restore_revision(
        workspace_id: uuid.UUID,
        revision_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await restore_workspace_revision(
                session,
                workspace_id=workspace_id,
                revision_id=revision_id,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (AuthorizationDeniedError, WorkspacePersistenceError, ValueError) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/migrate-profile")
    async def post_migrate_profile(
        workspace_id: uuid.UUID,
        payload: MigrateProfileRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            _, diff = await migrate_workspace_profile(
                session,
                workspace_id=workspace_id,
                profile_version_id=payload.profile_version_id,
                impact_level=payload.impact_level,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            envelope = await load_workspace_envelope(session, workspace_id=workspace_id)
            envelope["profile_migration_diff"] = asdict(diff)
            return JSONResponse(status_code=200, content=envelope)
        except (AuthorizationDeniedError, WorkspacePersistenceError, ValueError) as exc:
            return _error_response(exc)

    @router.get("/ssp-workspaces/{workspace_id}/exports/{export_format}")
    async def get_export(
        workspace_id: uuid.UUID,
        export_format: str,
        revision_id: uuid.UUID,
        include_open_questions: bool,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            content = await render_approved_export(
                session,
                workspace_id=workspace_id,
                revision_id=revision_id,
                export_format=export_format,
                include_open_questions=include_open_questions,
            )
            media_type = (
                "application/json"
                if export_format == "json"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            extension = "json" if export_format == "json" else "docx"
            return Response(
                content=content,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="ssp-{revision_id}.{extension}"',
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, no-store",
                },
            )
        except (AuthorizationDeniedError, ApprovalNotFoundError, ValueError) as exc:
            return _error_response(exc)

    return router


async def _edit_route(
    *,
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    workspace_id: uuid.UUID,
    audit_hmac_key: bytes,
    operation: Any,
) -> Response:
    del audit_hmac_key
    try:
        await _authorize_workspace(
            session, principal=principal, workspace_id=workspace_id, roles=("isso",)
        )
        await operation()
        return JSONResponse(
            status_code=200,
            content=await load_workspace_envelope(session, workspace_id=workspace_id),
        )
    except (AuthorizationDeniedError, WorkspacePersistenceError, ValueError) as exc:
        return _error_response(exc)
